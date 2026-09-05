from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

from engine.catalog.db import Asset
from engine.catalog.ollama_runtime import OLLAMA_KEEP_ALIVE, heavy_request
from engine.ingest.proxy import analysis_video_path
from engine.ingest.semantic_gate import (
    SEMANTIC_SCHEMA_VERSION,
    semantic_response_json_schema,
)

VISION_MODEL = "qwen3.5:9b"
# Legacy module default; runtime uses host_profile.resolve_vision_policy timeouts.
VISION_SEMANTIC_TIMEOUT = 300.0
VISION_CAPTION_TIMEOUT = 90.0
# Cap context: bare ollama defaults can advertise 262k and thrash VRAM/latency.
VISION_NUM_CTX = 8192

# Gate/transport reasons that warrant escalating to the larger model (still within
# MAX_SEMANTIC_ATTEMPTS — cascade consumes the same attempt budget, never exceeds it).
_ESCALATE_ERRORS = frozenset(
    {
        "vision_timeout",
        "schema_not_object",
        "vision_response_invalid",
        "vision_http_error",
        "semantic_analysis_exception",
    }
)

# Cross-industry persist filters. Industry wording (ground, 靓仔/美女) stays in pack notes.
_VISION_OPENER = re.compile(
    r"^(画面展示了一个|画面展示了|画面展示一处|画面展示一辆|画面展示一个|画面展示|"
    r"画面显示了一个|画面显示了|画面显示一处|画面显示一辆|画面显示一个|画面显示|"
    r"视频展示了一个|视频展示了|视频记录了一个|视频记录了|"
    r"The video (?:shows|captures|displays|features)\s+)",
    re.I,
)
_VISION_FRAME_NOISE = re.compile(
    r"（f[0-4](?:,\s*f[0-4])*可见）|"
    r"在\s*f[0-4]\s*帧中|"
    r"f[0-4]\s*帧中?|"
    r"在\s*f[0-4]\s*和\s*f[0-4]\s*(?:中|，)|"
    r"\bf[0-4]\b|"
    r"第[一二三四1234]帧(?:中)?|"
    r"后续帧中|在后续帧中|后续镜头"
)
_VISION_EMPTY_PERSON = re.compile(
    r"[。；，]?(画面中未出现人物|画面中无人物出现|画面无人物出现|未出现人物|无人物出现)[。；]?"
)
_VISION_PUNCT = re.compile(r"[，,]{2,}")


def sanitize_vision_prose(text: str) -> str:
    """Strip global boilerplate from model prose; do not invent replacement facts."""
    s = str(text or "").strip()
    if not s:
        return ""
    s = _VISION_OPENER.sub("", s, count=1).lstrip("，,。；、 ")
    s = _VISION_EMPTY_PERSON.sub("", s)
    s = _VISION_FRAME_NOISE.sub("", s)
    s = s.replace("无人可见", "")
    s = _VISION_PUNCT.sub("，", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"^[，。；、\s]+", "", s)
    s = re.sub(r"[，、]+\s*$", "。", s)
    s = s.replace("。。", "。").replace("，。", "。").strip()
    return s


def apply_global_vision_prose_filters(value: dict[str, Any]) -> dict[str, Any]:
    desc = sanitize_vision_prose(str(value.get("description") or ""))
    if desc:
        value["description"] = desc
    for frame in value.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        facts = frame.get("visible_facts")
        if isinstance(facts, list):
            cleaned = [sanitize_vision_prose(str(x)) for x in facts]
            frame["visible_facts"] = [x for x in cleaned if x]
    people = value.get("people")
    if isinstance(people, dict):
        for key in ("appearance", "clothing"):
            raw = people.get(key)
            if isinstance(raw, str) and raw.strip() in {"无人可见", "无"}:
                people[key] = ""
            elif isinstance(raw, str):
                people[key] = sanitize_vision_prose(raw)
    return value


def should_escalate_vision(
    *,
    gate: dict[str, Any],
    extraction_audit: dict[str, Any] | None,
) -> bool:
    """True when primary failed in a way that justifies trying the escalate model."""
    err = str((extraction_audit or {}).get("error") or "")
    if err in _ESCALATE_ERRORS:
        return True
    if not bool(gate.get("passed")):
        return True
    return False


class VisionTimeoutError(RuntimeError):
    """Vision request hit its wall-clock/read timeout via the shared gateway."""


class VisionHTTPError(RuntimeError):
    """Vision request failed for a non-timeout transport/HTTP reason."""


def _active_vision_model() -> str:
    try:
        from engine.catalog.host_profile import resolve_vision_policy

        return resolve_vision_policy().primary_model or VISION_MODEL
    except Exception:
        try:
            from engine.catalog.ollama_status import active_models_from_settings

            _, vision = active_models_from_settings()
            return vision or VISION_MODEL
        except Exception:
            return VISION_MODEL


def heuristic_describe(asset: Asset, start: float, end: float) -> str:
    name = Path(asset.source_path).stem
    cat = asset.category or "未分类"
    dur = round(end - start, 1)
    orientation = "竖屏" if (asset.height or 0) > (asset.width or 0) else "横屏"
    bits = [
        f"分类:{cat}",
        f"素材:{name}",
        f"片段{start:.1f}-{end:.1f}秒共{dur}秒",
        orientation,
        "实拍业务素材",
    ]
    return "；".join(bits)


def extract_frame(video: Path, at_sec: float, out_path: Path) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, at_sec):.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-q:v",
        "4",
        str(out_path),
    ]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0 and out_path.exists()


def _extract_frame_near(
    video: Path,
    target_sec: float,
    out_path: Path,
    *,
    start: float,
    end: float,
) -> tuple[bool, float]:
    """Try a bounded nearby timestamp when a keyframe boundary cannot decode."""
    lower = min(end, start + 0.02)
    upper = max(lower, end - 0.02)
    candidates = (target_sec, target_sec - 0.08, target_sec + 0.08)
    tried: set[float] = set()
    for candidate in candidates:
        at = round(min(max(candidate, lower), upper), 3)
        if at in tried:
            continue
        tried.add(at)
        if extract_frame(video, at, out_path):
            return True, at
    return False, round(min(max(target_sec, lower), upper), 3)


def vision_caption(
    image_path: Path,
    *,
    model: str | None = None,
    timeout: float = VISION_CAPTION_TIMEOUT,
) -> str | None:
    model = model or _active_vision_model()
    try:
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        prompt = (
            "你是短视频实拍素材标注员。"
            "用一句中文描述画面（20–45字），必须点明："
            "①真实场景类型；"
            "②可见主要物品；③若有动作也写上。"
            "不要开场白，不要引号，不要推测价格或品牌口号。"
        )
        result = heavy_request(
            kind="vision",
            path="/api/chat",
            payload={
                "model": model,
                "stream": False,
                "think": False,
                "keep_alive": OLLAMA_KEEP_ALIVE,
                "messages": [{"role": "user", "content": prompt, "images": [b64]}],
            },
            timeout_sec=timeout,
            model=model,
        )
        if not result.get("ok"):
            return None
        body = result.get("body") or {}
        content = (body.get("message") or {}).get("content") or "" if isinstance(body, dict) else ""
        # strip possible thinking leakage — keep last non-empty line-ish
        text = content.strip().split("\n")[0].strip()
        if len(text) < 4:
            return None
        return text[:120]
    except Exception:
        return None


_THINK_BLOCK = re.compile(
    r"<think>[\s\S]*?</think>|<thinking>[\s\S]*?</thinking>",
    re.IGNORECASE,
)


def _extract_first_json_object(text: str) -> str | None:
    """Return the first balanced `{...}` span, or None if truncated/absent."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_json_response(content: str) -> dict[str, Any] | None:
    """Parse model content into one JSON object without inventing fields.

    Handles markdown fences, leading/trailing prose, and think-blocks that
    contain stray braces. Arrays / non-objects / truncated JSON → None.
    """
    raw = _THINK_BLOCK.sub("", content or "").strip()
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fenced:
        raw = fenced.group(1).strip()
    # Top-level arrays must fail closed — do not peel the first element.
    if raw.startswith("["):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    candidate = _extract_first_json_object(raw)
    if candidate is None:
        return None
    try:
        value = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def build_vision_semantic_prompt(
    frame_manifest: list[dict[str, Any]],
    allowed_frame_ids: list[str],
    *,
    extra_notes: list[str] | None = None,
) -> str:
    """Cross-industry vision prompt plus optional industry-pack notes."""
    extra = ""
    notes = [str(n).strip() for n in (extra_notes or []) if str(n).strip()]
    if notes:
        bullets = "\n".join(f"- {n}" for n in notes)
        extra = f"\n行业附加（只约束 description / visible_facts / people 用词，不改 JSON 字段名与 label 枚举）：\n{bullets}\n"
    return f"""
你是跨行业视频素材的事实标注器。分析同一切片的多张按时间排序抽帧，只写肉眼可见事实。
禁止依据文件名、行业、品牌、常识推断；禁止推测人物职业身份、情绪、精神状态或产品用途。
看不清的内容只能写入对应帧的 unknowns，禁止出现在 visible_facts 或 description；
禁止用“可能/应该/似乎/看起来像/用于/体现/彰显”等推测措辞补全。
严格返回单个 JSON 对象，不要 Markdown。schema_version 必须为 {SEMANTIC_SCHEMA_VERSION}。
本次唯一合法帧清单：{json.dumps(frame_manifest, ensure_ascii=False)}
frame_id 和 evidence_frame_ids 只能逐字引用以下 ID：{json.dumps(allowed_frame_ids, ensure_ascii=False)}。
frames 必须按上述清单每帧恰好返回一次，不得漏帧、改名、增加 f0/f4 等不存在 ID。
场景 label 只能多选自：delivery, product_use, loading, stacking, inventory_full, company_image,
warehouse, storefront, production, installation_site, office, transport, product_closeup,
people_activity, other_visible。
互动 label 只能多选自：delivery, installation, repair, product_use, loading, unloading,
sorting, stacking, demonstration, inspection, consultation, teamwork, none_visible。
人物宣传 label 只能多选自：person_visible_unclassified, employee_individual, employee_group,
customer_interaction, work_portrait, team_image, none_visible。
JSON 必须具有：
{{
 "schema_version":"{SEMANTIC_SCHEMA_VERSION}","source_backend":"vision",
 "description":"20-400字，综合描述位置、主体、动作、数量/颜色/空间关系等可见细节",
 "frames":[{{"frame_id":"f1","timestamp_sec":0.0,"visible_facts":["至少两条具体事实"],"unknowns":[]}}],
 "scenes":[{{"label":"warehouse","confidence":0.0,"evidence_frame_ids":["f1"]}}],
 "products":[{{"name":"画面可见名称或外观称呼","category":"可见粗类","use":"仅在用途被画面直接展示时填写，否则写未知","key_attributes":["颜色/形状/包装等"],"confidence":0.0,"evidence_frame_ids":["f1"]}}],
 "interactions":[{{"label":"none_visible","confidence":0.0,"evidence_frame_ids":["f1"]}}],
 "people":{{"present":false,"count":0,"appearance":"","clothing":"","promotion_labels":[{{"label":"none_visible","confidence":1.0,"evidence_frame_ids":["f1"]}}]}},
 "consistency":{{"consistent":true,"issues":[]}},
 "overall_confidence":0.0
}}
硬性结构规则：
1. 每帧 visible_facts 至少两条，只含肯定可见的物体、颜色、数量、位置、姿态或动作；
   无法辨认的文字、品牌、身份、用途、情绪放入 unknowns，二者不得混写。
2. people.present=true 时 count>=1，promotion_labels 禁止 none_visible；无法确认职业身份时，
   必须使用 person_visible_unclassified，只描述可见衣着/姿态，不猜员工或客户。
3. people.present=false 时 count=0，promotion_labels 必须且只能为 none_visible；
   appearance 与 clothing 用空字符串，不要写“无人可见”。
4. 产品 key_attributes 只能写可见颜色、形状、材质表观、包装和文字；没有任何可见外观属性时写
   ["unknown"]，use 未被动作直接展示时写 "unknown"，不得猜用途。
5. 每个 evidence_frame_ids 至少含一个上述合法 frame_id；不要引用未提供的 ID。
中文 description 写法（全行业中性，禁止空话）：
6. 禁止用“画面展示了/画面显示/视频展示了/视频记录了”开头，直接写看见的主体与动作。
7. description 与 visible_facts 禁止出现抽帧编号（f1/f2/f3、“第N帧”、“后续帧”）。
8. 禁止空人套话“画面中未出现人物”“无人物出现”；画面没人就不要写人。
没有可辨产品时 products=[]。同一标签只写一次。置信度必须与清晰度匹配，不得为过门禁虚报。
{extra}"""


def vision_semantic_analysis(
    frame_paths: list[tuple[str, float, Path]],
    *,
    model: str | None = None,
    timeout: float = VISION_SEMANTIC_TIMEOUT,
    extra_notes: list[str] | None = None,
) -> dict[str, Any] | None:
    """Analyze ordered frames as one clip; return JSON only, never guessed fallback."""
    model = model or _active_vision_model()
    if len(frame_paths) < 2:
        return None
    frame_manifest = [
        {"frame_id": fid, "timestamp_sec": ts} for fid, ts, _ in frame_paths
    ]
    allowed_frame_ids = [fid for fid, _, _ in frame_paths]
    prompt = build_vision_semantic_prompt(
        frame_manifest, allowed_frame_ids, extra_notes=extra_notes
    )
    images = [base64.b64encode(path.read_bytes()).decode("ascii") for _, _, path in frame_paths]
    # Pass the full gate-aligned JSON Schema (not bare "json") and pin
    # temperature=0 so structured outputs stay deterministic across retries.
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "format": semantic_response_json_schema(),
        "options": {"temperature": 0, "num_ctx": VISION_NUM_CTX},
        "messages": [{"role": "user", "content": prompt, "images": images}],
    }
    result = heavy_request(
        kind="vision",
        path="/api/chat",
        payload=payload,
        timeout_sec=timeout,
        model=model,
    )
    if not result.get("ok"):
        # Preserve the timeout-vs-other distinction so analyze_cliplet_semantics
        # can audit vision_timeout instead of collapsing into schema_not_object.
        if str(result.get("error_kind") or "") == "timeout":
            raise VisionTimeoutError(str(result.get("error") or "vision_timeout"))
        raise VisionHTTPError(str(result.get("error") or "vision_http_error"))
    body = result.get("body") or {}
    message = (body.get("message") or {}) if isinstance(body, dict) else {}
    content = message.get("content") or ""
    parsed = _parse_json_response(content)
    return _normalize_semantic_response(parsed, frame_paths)


def _normalize_semantic_response(
    data: dict[str, Any] | None,
    frame_paths: list[tuple[str, float, Path]],
) -> dict[str, Any] | None:
    """Normalize equivalent JSON shapes without inventing semantic content."""
    if not isinstance(data, dict):
        return None
    # Copy model output so audit callers can safely compare raw fixtures.
    value = json.loads(json.dumps(data, ensure_ascii=False))
    by_id = {fid: ts for fid, ts, _ in frame_paths}
    allowed = set(by_id)

    normalized_frames: list[dict[str, Any]] = []
    seen_frames: set[str] = set()
    for frame in value.get("frames") or []:
        if not isinstance(frame, dict):
            continue
        frame_id = str(frame.get("frame_id") or "")
        # Unsupported/duplicate frame objects cannot describe an image supplied
        # by this call, so conservatively discard them rather than remapping.
        if frame_id not in allowed or frame_id in seen_frames:
            continue
        frame["timestamp_sec"] = by_id[frame_id]
        normalized_frames.append(frame)
        seen_frames.add(frame_id)
    if isinstance(value.get("frames"), list):
        value["frames"] = normalized_frames

    def normalize_evidence(row: dict[str, Any]) -> None:
        evidence = row.get("evidence_frame_ids")
        if isinstance(evidence, str):
            evidence = [evidence]
        if isinstance(evidence, list):
            # Removing a nonexistent citation is safe; never substitute another
            # frame ID because that would fabricate evidence.
            row["evidence_frame_ids"] = list(
                dict.fromkeys(v for v in evidence if isinstance(v, str) and v in allowed)
            )

    for section in ("scenes", "products", "interactions"):
        for row in value.get(section) or []:
            if isinstance(row, dict):
                normalize_evidence(row)
                if section == "products" and isinstance(row.get("key_attributes"), str):
                    attribute = row["key_attributes"].strip()
                    row["key_attributes"] = [attribute] if attribute else []
    people = value.get("people")
    if isinstance(people, dict):
        for row in people.get("promotion_labels") or []:
            if isinstance(row, dict):
                normalize_evidence(row)
    return apply_global_vision_prose_filters(value)


def analyze_cliplet_semantics(
    asset: Asset,
    start: float,
    end: float,
    *,
    cache_dir: Path,
    attempt: int = 1,
    use_vision: bool = True,
    model: str | None = None,
    timeout: float | None = None,
    cascade_stage: str = "primary",
    pack_id: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Precisely sample three traceable frames; retries use different offsets."""
    from engine.catalog.host_profile import resolve_vision_policy

    policy = resolve_vision_policy()
    stage = cascade_stage if cascade_stage in ("primary", "escalate") else "primary"
    use_model = model or policy.model_for_stage(stage)
    use_timeout = float(timeout if timeout is not None else policy.timeout_for_stage(stage))
    audit: dict[str, Any] = {
        "attempt": attempt,
        "backend": "vision",
        "frames": [],
        "vision_model": use_model,
        "cascade_stage": stage,
        "vision_tier": policy.tier,
        "timeout_sec": use_timeout,
        "cascade_enabled": policy.cascade,
    }
    if not use_vision:
        audit["error"] = "vision_disabled"
        return None, audit
    video = analysis_video_path(asset)
    if not video.exists():
        audit["error"] = "video_missing"
        return None, audit
    duration = max(0.01, end - start)
    fractions_by_attempt = (
        (0.2, 0.5, 0.8),
        (0.12, 0.42, 0.72),
        (0.28, 0.58, 0.88),
    )
    fractions = fractions_by_attempt[min(max(attempt, 1), 3) - 1]
    frame_paths: list[tuple[str, float, Path]] = []
    for i, fraction in enumerate(fractions, 1):
        at = min(max(start + 0.02, start + duration * fraction), max(start + 0.02, end - 0.02))
        frame_id = f"f{i}"
        frame = cache_dir / (
            f"semantic_{asset.uuid}_{int(start * 1000)}_{int(end * 1000)}_a{attempt}_{frame_id}.jpg"
        )
        extracted, actual_at = _extract_frame_near(
            video,
            at,
            frame,
            start=start,
            end=end,
        )
        audit["frames"].append(
            {
                "frame_id": frame_id,
                "timestamp_sec": actual_at,
                "target_timestamp_sec": round(at, 3),
                "path": str(frame),
                "extracted": extracted,
            }
        )
        if extracted:
            frame_paths.append((frame_id, actual_at, frame))
    if len(frame_paths) < 2:
        audit["error"] = "frame_extraction_insufficient"
        return None, audit
    try:
        from engine.catalog.industry_pack import semantic_vision_notes_for_pack

        extra_notes = semantic_vision_notes_for_pack(pack_id)
        result = vision_semantic_analysis(
            frame_paths,
            model=use_model,
            timeout=use_timeout,
            extra_notes=extra_notes,
        )
    except VisionTimeoutError:
        audit["error"] = "vision_timeout"
        return None, audit
    except (VisionHTTPError, httpx.HTTPError) as exc:
        audit["error"] = "vision_http_error"
        audit["error_type"] = type(exc).__name__
        return None, audit
    if result is None:
        # Distinguish empty/non-object responses for cascade + audit clarity.
        audit["error"] = "schema_not_object"
        return None, audit
    # Timestamps and frame identifiers are extraction facts, not model opinions.
    by_id = {fid: ts for fid, ts, _ in frame_paths}
    for frame in result.get("frames") or []:
        if isinstance(frame, dict) and frame.get("frame_id") in by_id:
            frame["timestamp_sec"] = by_id[frame["frame_id"]]
    audit["model_returned"] = True
    return result, audit


def describe_cliplet_vision(
    asset: Asset,
    start: float,
    end: float,
    *,
    cache_dir: Path,
    use_vision: bool = True,
) -> tuple[str, str]:
    """Return (description, backend) where backend is vision|heuristic."""
    fallback = heuristic_describe(asset, start, end)
    if not use_vision:
        return fallback, "heuristic"

    video = analysis_video_path(asset)
    if not video.exists():
        return fallback, "heuristic"

    mid = start + max(0.1, (end - start) * 0.4)
    frame = cache_dir / f"frame_{asset.uuid}_{int(start*10)}_{int(end*10)}.jpg"
    if not extract_frame(video, mid, frame):
        return fallback, "heuristic"

    caption = vision_caption(frame)
    if not caption:
        return fallback, "heuristic"

    cat = asset.category or "未分类"
    # Keep category/time as retrieval anchors + vision sentence
    merged = f"分类:{cat}；{caption}；时段{start:.1f}-{end:.1f}秒"
    return merged, "vision"
