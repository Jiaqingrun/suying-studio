#!/usr/bin/env python3
"""Trial: recipe combo → formal local-AI copy → Edge TTS → continuous motion cuts → render."""

from __future__ import annotations

import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.catalog.db import Asset, Cliplet, Customer, get_session
from engine.catalog.keyword_pack import get_active_pack, list_title_pool
from engine.catalog.ollama_runtime import OLLAMA_KEEP_ALIVE, OLLAMA_URL
from engine.catalog.vector_index import search_cliplets
from engine.config.settings import load_settings
from engine.ingest.semantic_gate import semantic_gate_passed
from engine.pack.emoji_stickers import strip_emoji_for_speech
from engine.pack.narration_script import srt_from_narration_segments
from engine.pack.ollama_narration import resolve_narration_model
from engine.pack.text_sanitize import ensure_zh_speech_breaks, strip_all_punctuation
from engine.pack.tts import narration_bed_from_result, synthesize_script
from engine.pack.video_lock import load_video_lock
from engine.render.ffmpeg import render_plan
from engine.render.voice_subtitle import burn_subtitles_inplace
from engine.template.engine import (
    DEFAULT_TEMPLATE,
    ClipPlan,
    MontagePlan,
    TemplateDefinition,
    plan_to_dict,
)


RECIPE_COMBO = ("sorting_fulfillment", "loading_handling")
TITLE_CATS = ("仓内陈列标题", "装车搬运标题")
THEME = "仓配"
SEED = 20260802
MOTION_ACTIONS = {
    "loading",
    "unloading",
    "teamwork",
    "sorting",
    "stacking",
    "delivery",
    "carrying",
    "packing",
    "product_use",
}
MOTION_SCENES = {"loading", "transport", "delivery", "sorting", "装车卸货"}
SORTING_ACTIONS = {"sorting", "packing"}
SORTING_TOKENS = ("分拣", "拣货", "配货备料", "按单分拣", "分拣配货")
COLLOQUIAL_BAN = (
    "你看",
    "就是这样",
    "就是这么",
    "真的",
    "赶紧看",
    "来看看",
    "跟着镜头",
    "一眼就",
    "看着踏实",
    "嗨",
    "呀",
    "呐",
    "嘿",
    "哇",
    "回事儿",
    "这么干",
)


def _polish_formal_script(script: str) -> str:
    text = strip_emoji_for_speech(script or "")
    text = ensure_zh_speech_breaks(text, max_chars=18)
    for a, b in (
        ("分拣。配货", "分拣配货"),
        ("装车。搬运", "装车搬运"),
        ("仓配。现场", "仓配现场"),
        ("陈列。分拣", "陈列分拣"),
    ):
        text = text.replace(a, b)
    for ban in COLLOQUIAL_BAN:
        text = text.replace(ban, "")
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。．]{2,}", "。", text)
    text = text.strip(" ，,。．")
    if text and not text.endswith(("。", "！", "？")):
        text += "。"
    return text


def _pick_recipe_components(pack_data: dict, rng: random.Random) -> dict:
    recipes = pack_data.get("recipes") or {}
    library = ((pack_data.get("copy_components") or {}).get("library")) or {}
    selected: list[dict] = []
    labels: list[str] = []
    for rid in RECIPE_COMBO:
        recipe = recipes.get(rid) or {}
        labels.append(str(recipe.get("label") or rid))
        for slot in recipe.get("sequence") or ("hook", "visible_facts", "process", "value", "cta"):
            key = {
                "hook": "hooks",
                "visible_facts": "visible_facts",
                "process": "process",
                "value": "value",
                "cta": "cta",
            }.get(str(slot), str(slot))
            items = [x for x in (library.get(key) or []) if isinstance(x, dict) and x.get("text")]
            if not items:
                continue
            prefer = [
                x
                for x in items
                if any(
                    tok in f"{x.get('id', '')}{x.get('text', '')}"
                    for tok in ("分拣", "配货", "装车", "搬运", "出库", "仓库", "发货")
                )
            ]
            pick = rng.choice(prefer or items)
            selected.append({"recipe": rid, "slot": key, "id": pick.get("id"), "text": pick["text"]})
    seen: set[str] = set()
    uniq: list[dict] = []
    for item in selected:
        t = str(item["text"]).strip()
        if not t or t in seen:
            continue
        seen.add(t)
        uniq.append(item)
        if len(uniq) >= 5:
            break
    return {"labels": labels, "components": uniq, "texts": [x["text"] for x in uniq]}


def _pick_title(pack, rng: random.Random) -> str:
    cats = ((pack.data_json.get("keyword_pool") or {}).get("title_pool") or {}).get("categories") or {}
    # Prefer loading/shipping titles for this recipe combo; avoid static product closeups.
    preferred_cats = ("装车搬运标题", "配送标题", "服务与配送标题", "仓内陈列标题")
    pool: list[str] = []
    for cat in preferred_cats:
        for t in cats.get(cat) or []:
            if isinstance(t, str) and t.strip():
                pool.append(t.strip())
    if not pool:
        for cat in TITLE_CATS:
            for t in cats.get(cat) or []:
                if isinstance(t, str) and t.strip():
                    pool.append(t.strip())
    if not pool:
        pool = list_title_pool(pack, theme="loading", prefer_rich=True) or list_title_pool(
            pack, theme="仓配", prefer_rich=True
        )
    if not pool:
        return "仓配装车\n现场介绍"
    ban_title = ("你看", "一眼", "赶紧", "板材", "堆叠实拍", "白色")
    calm = [
        t
        for t in pool
        if "\n" in t
        and not any(b in t for b in ban_title)
        and any(k in t for k in ("装车", "发货", "搬运", "出库", "仓配", "配货", "分拣", "拣货"))
    ]
    pick = rng.choice(calm or [t for t in pool if "\n" in t and not any(b in t for b in ban_title)] or pool)
    return strip_all_punctuation(pick, keep_newlines=True) or pick


def _formal_rewrite(
    *,
    base_script: str,
    components: list[str],
    brand: str,
    title: str,
    model: str,
    target_duration_sec: float,
    seed: int,
) -> dict:
    prompt = (
        "你是企业宣传短视频的旁白主持人。请写一段信息更密、节奏更稳的正式主持人口播。\n"
        "结构（按顺序，可多句）：\n"
        "1) 开场定位：交代品牌 + 这是仓配作业现场介绍；\n"
        "2) 分拣配货：说明按单分拣、核对品类、备料配货（至少 2 句，信息要具体但不编造参数）；\n"
        "3) 装车搬运：说明出库装车、搬运衔接、发货准备（至少 2 句）；\n"
        "4) 证据边界：内容来自当前实拍可见过程；\n"
        "5) 收束：具体型号规格与交付安排以实际确认为准。\n"
        "主持人口播要求：\n"
        "- 像稳重主持人：句长相对均匀（每句 10–16 字），少感叹，少口语推力；\n"
        "- 信息密度高于闲聊：把「分拣→配货→装车→搬运」讲清楚；\n"
        "- 禁止：你看、真的、赶紧看、就是这样、回事儿、跟着镜头、嗨、呀、嘿、哇；\n"
        "- 禁止：大家好、本期主题、保证、最好、第一、厂家直销、马上发货；\n"
        "- 不要复述屏幕标题；不要编造库存、价格、时效；\n"
        "- 必须改写词池要点，禁止连续照搬原句；\n"
        f"- 总字数适合约 {target_duration_sec:.0f} 秒（中文约每秒 3.6–3.9 字，宁可略密）；\n"
        "- 只用中文句号「。」断句；\n"
        '- 只输出 JSON：{"script":"口播全文"}\n\n'
        f"品牌：{brand}\n"
        f"屏幕标题（勿照念）：{title}\n"
        "词池要点（事实边界）：\n- "
        + "\n- ".join(components)
        + f"\n底稿（需改写加密度）：\n{base_script}\n"
    )
    with httpx.Client(timeout=180.0, trust_env=False) as client:
        resp = client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "stream": False,
                "keep_alive": OLLAMA_KEEP_ALIVE,
                "messages": [{"role": "user", "content": prompt}],
                "options": {"temperature": 0.55, "seed": int(seed)},
            },
        )
    if resp.status_code != 200:
        return {"ok": False, "error": f"ollama HTTP {resp.status_code}", "script": base_script}
    content = (resp.json().get("message") or {}).get("content") or ""
    m = re.search(r"\{[\s\S]*\}", content)
    script = ""
    if m:
        try:
            data = json.loads(m.group(0))
            script = str(data.get("script") or "").strip()
        except json.JSONDecodeError:
            script = ""
    if not script:
        script = content.strip().split("\n")[0].strip()
    script = _polish_formal_script(script)
    if len(script) < 12:
        return {"ok": False, "error": "formal rewrite too short", "script": base_script, "raw": content[:400]}
    return {"ok": True, "script": script, "model": model}


def _host_scaffold(brand: str) -> str:
    short = brand
    if "（" in brand and "）" in brand:
        short = brand[brand.find("（") + 1 : brand.find("）")].strip() or brand
    elif "(" in brand and ")" in brand:
        short = brand[brand.find("(") + 1 : brand.find(")")].strip() or brand
    return _polish_formal_script(
        f"这里介绍的是{short}仓配作业现场。"
        "工作人员先按单据进行分拣。"
        "核对品类后再完成配货备料。"
        "备齐货物随后转入出库环节。"
        "装车与搬运按顺序衔接推进。"
        "画面记录的是当前实拍过程。"
        "具体型号规格请以实际确认为准。"
        "交付安排同样以确认结果为准。"
    )


def _is_sorting_clip(row: Cliplet) -> bool:
    acts = {str(x) for x in (row.actions_json or []) if str(x).strip()}
    if acts & SORTING_ACTIONS:
        return True
    blob = " ".join(
        [
            str(row.scene or ""),
            " ".join(acts),
            str(row.description or ""),
        ]
    )
    # Require explicit sorting/picking language — avoid false hits like gloves-only shots.
    return any(tok in blob for tok in SORTING_TOKENS)


def _is_motion_clip(row: Cliplet) -> bool:
    acts = {str(x) for x in (row.actions_json or []) if str(x).strip()}
    if acts & MOTION_ACTIONS:
        return True
    scene = str(row.scene or "").strip()
    if scene in MOTION_SCENES:
        return True
    sem = row.semantic_json if isinstance(row.semantic_json, dict) else {}
    interactions = sem.get("interactions") or []
    labels: set[str] = set()
    if isinstance(interactions, list):
        for item in interactions:
            if isinstance(item, dict) and item.get("label"):
                labels.add(str(item["label"]))
    if labels & (MOTION_ACTIONS | {"搬运", "装车", "卸货", "分拣", "协作"}):
        return True
    # reject obvious still displays
    desc = str(row.description or "")
    static_marks = ("整齐排列", "静态", "货架上整齐", "陈列展示")
    if any(m in desc for m in static_marks) and not acts:
        return False
    return False


def _continuous_motion_plan(
    session,
    *,
    customer_id: int,
    title: str,
    script: str,
    need_sec: float,
    seed: int,
) -> tuple[list[ClipPlan], list[str]]:
    """Pick fewer longer continuous takes; never mid-slice randomly inside a cliplet."""
    warnings: list[str] = []
    rng = random.Random(seed)
    queries = [
        f"{script} 分拣配货 拣货",
        "分拣 sorting packing 配货 手套",
        f"{script} 装车搬运 出库",
        "装车 搬运 teamwork loading",
        "货车 卸货 unloading transport",
    ]
    hit_map: dict[int, tuple[Cliplet, float]] = {}
    for q in queries:
        for row, score in search_cliplets(
            session,
            q,
            top_k=60,
            min_duration=2.5,
            customer_id=customer_id,
            orientation="portrait",
            strict_semantic_v1=True,
        ):
            prev = hit_map.get(row.id)
            if prev is None or score > prev[1]:
                hit_map[row.id] = (row, score)
    # Also pull action-tagged usable strict cliplets that search may miss
    tagged = list(
        session.scalars(
            select(Cliplet)
            .join(Asset, Cliplet.asset_id == Asset.id)
            .where(
                Asset.customer_id == customer_id,
                Asset.status == "ready",
                Asset.orientation == "portrait",
                Cliplet.status == "usable",
                Cliplet.embedding_json.is_not(None),
                Cliplet.duration_sec >= 2.5,
            )
        ).all()
    )
    for row in tagged:
        if not semantic_gate_passed(row):
            continue
        if not _is_motion_clip(row):
            continue
        if row.id not in hit_map:
            hit_map[row.id] = (row, 0.35)
    hits = list(hit_map.values())
    motion = [(c, score) for c, score in hits if _is_motion_clip(c)]
    if len(motion) < 6:
        extra = [
            (c, score)
            for c, score in hits
            if str(c.scene or "") in MOTION_SCENES
        ]
        seen = {c.id for c, _ in motion}
        for row, score in extra:
            if row.id not in seen:
                motion.append((row, score))
    if not motion:
        raise RuntimeError("无动态可用切片（loading/transport/action）")
    sorting = [(c, score) for c, score in motion if _is_sorting_clip(c)]
    # Hard-prefer clips tagged sorting/packing even if semantic score is lower.
    for row in tagged:
        if not semantic_gate_passed(row):
            continue
        acts = {str(x) for x in (row.actions_json or []) if str(x).strip()}
        if not (acts & SORTING_ACTIONS):
            continue
        if float(row.duration_sec or 0) < 2.8:
            continue
        if row.id not in {c.id for c, _ in sorting}:
            sorting.append((row, max(hit_map.get(row.id, (row, 0.55))[1], 0.8)))
    loadingish = [(c, score) for c, score in motion if not _is_sorting_clip(c)]
    warnings.append(f"动态候选 {len(motion)} 条（分拣相关 {len(sorting)}）")

    # Cover VO with fewer long continuous takes (+slack so picture > VO)
    target = max(need_sec + 0.55, need_sec * 1.04)

    used_ids: set[int] = set()
    clips: list[ClipPlan] = []
    filled = 0.0
    i = 0

    def _rank(pool: list[tuple[Cliplet, float]], *, boost_sorting: bool = False) -> list[tuple[Cliplet, float]]:
        def key(item: tuple[Cliplet, float]) -> tuple:
            row, score = item
            acts = {str(x) for x in (row.actions_json or []) if str(x).strip()}
            tagged_sort = 1 if acts & SORTING_ACTIONS else 0
            return (-(1 if boost_sorting and tagged_sort else 0), -score, -float(row.duration_sec or 0))

        ordered = sorted(pool, key=key)
        top = ordered[:16]
        rest = ordered[16:]
        rng.shuffle(top)
        return sorted(top, key=key) + rest

    # Reserve ~45% front for sorting; require at least 2 sorting takes when available.
    sorting_budget = max(need_sec * 0.4, 12.0) if sorting else 0.0
    phase_pools = [
        ("sorting", _rank(sorting, boost_sorting=True), sorting_budget),
        ("loading", _rank(loadingish or motion), target),
    ]

    def _append_take(row: Cliplet, score: float, take: float) -> bool:
        nonlocal filled, i
        if row.id in used_ids:
            return False
        asset = session.scalar(select(Asset).where(Asset.uuid == row.asset_uuid))
        if not asset or not Path(str(asset.storage_path)).is_file():
            return False
        remain = target - filled
        role = "hook" if i == 0 else ("cta" if remain - take <= 3.0 else "body")
        name = "hook" if i == 0 else (f"body{i}" if role == "body" else "cta")
        clips.append(
            ClipPlan(
                slot=name,
                asset_uuid=row.asset_uuid,
                source_path=asset.storage_path,
                start_sec=round(float(row.start_sec or 0.0), 3),
                duration_sec=round(float(take), 3),
                cliplet_id=int(row.id),
                score=round(float(score), 4),
                description=row.description,
                theme=row.theme,
                scene=row.scene,
                objects=list(row.objects_json or []),
                actions=list(row.actions_json or []),
            )
        )
        used_ids.add(row.id)
        filled += take
        i += 1
        return True

    for phase_name, pool, phase_target in phase_pools:
        phase_filled = 0.0
        for row, score in pool:
            if filled >= target:
                break
            if phase_name == "sorting" and phase_filled >= phase_target and clips:
                break
            avail = float(row.duration_sec or 0.0)
            if avail < 2.8:
                continue
            remain = target - filled
            take = min(avail, 8.0, max(2.8, remain))
            if take < 2.8:
                continue
            if _append_take(row, score, take):
                phase_filled += take
        if phase_name == "sorting":
            sorting_clips = sum(
                1
                for c in clips
                if ("sorting" in (c.actions or []))
                or ("packing" in (c.actions or []))
                or any(tok in str(c.description or "") for tok in SORTING_TOKENS)
            )
            warnings.append(f"分拣段约 {phase_filled:.1f}s / {sorting_clips} 镜")

    # Fallback fill from all motion if still short
    if filled + 0.05 < need_sec:
        for row, score in _rank(motion):
            if filled >= target:
                break
            avail = float(row.duration_sec or 0.0)
            if avail < 2.8:
                continue
            take = min(avail, 8.0, max(2.8, target - filled))
            _append_take(row, score, take)

    if filled + 0.05 < need_sec:
        raise RuntimeError(f"动态连续切片不足：画面 {filled:.2f}s < 旁白 {need_sec:.2f}s")
    if filled <= need_sec:
        last = clips[-1]
        row = session.get(Cliplet, last.cliplet_id)
        if row is not None:
            room = float(row.duration_sec or 0.0) - float(last.duration_sec)
            bump = min(max(0.25, need_sec - filled + 0.35), max(0.0, room))
            if bump > 0:
                last.duration_sec = round(float(last.duration_sec) + bump, 3)
                filled += bump
                warnings.append(f"末段连续延长 {bump:.2f}s 以满足成片>旁白")
    if filled <= need_sec:
        raise RuntimeError(f"无法满足成片>旁白：画面 {filled:.2f}s / 旁白 {need_sec:.2f}s")
    warnings.append(
        f"连续动态取片 {len(clips)} 段 / {filled:.1f}s；禁中途随机截断；仅动作镜头"
    )
    for c in clips:
        if not (c.actions or str(c.scene or "") in MOTION_SCENES):
            warnings.append(f"弱动态告警 cliplet={c.cliplet_id} scene={c.scene}")
    _ = title
    return clips, warnings


def main() -> int:
    settings = load_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(settings.paths.render_root) / f"trial_narration_first_{stamp}"
    out_root.mkdir(parents=True, exist_ok=True)
    voice_dir = out_root / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)

    session = get_session()
    report: dict = {"ok": False, "out_root": str(out_root), "requirements": [
        "formal_intro_like_human",
        "less_colloquial",
        "continuous_clips_no_midcut",
        "motion_only_no_static",
    ]}
    try:
        customer = session.scalar(select(Customer).where(Customer.name == "北京始峰伟业"))
        if not customer:
            raise RuntimeError("未找到客户 北京始峰伟业")
        pack = get_active_pack(session, customer.name)
        if not pack:
            raise RuntimeError("无活跃词池")
        rng = random.Random(SEED + 1)
        combo = _pick_recipe_components(pack.data_json, rng)
        title = _pick_title(pack, rng)
        brand = str(
            (pack.data_json.get("identity") or {}).get("display_name")
            or (pack.data_json.get("company_info") or {}).get("display_name_preferred")
            or "始峰五金"
        )
        report.update(
            {
                "recipe_combo": list(RECIPE_COMBO),
                "recipe_labels": combo["labels"],
                "components": combo["components"],
                "title": title,
                "brand": brand,
                "theme": THEME,
                "keyword_pack": {"id": pack.id, "revision": pack.revision},
            }
        )

        base_script = "。".join(combo["texts"])
        if base_script and not base_script.endswith(("。", "！", "？")):
            base_script += "。"
        base_script = ensure_zh_speech_breaks(base_script, max_chars=16)

        model = resolve_narration_model(settings)
        rewritten = _formal_rewrite(
            base_script=base_script,
            components=combo["texts"],
            brand=brand,
            title=title,
            model=model,
            target_duration_sec=30.0,
            seed=SEED + 21,
        )
        report["ollama"] = {
            "model": model,
            "ok": bool(rewritten.get("ok")),
            "error": rewritten.get("error"),
            "style": "host_dense_stable",
        }
        script = _polish_formal_script(str(rewritten.get("script") or base_script))
        if not rewritten.get("ok"):
            print("WARN formal rewrite failed:", rewritten.get("error"))
        # Host density gate: must cover 分拣 + 装车/出库, and stay information-dense.
        chars = len(re.sub(r"\s+", "", script))
        has_sorting = "分拣" in script
        has_loading = any(x in script for x in ("装车", "出库", "搬运"))
        copied = sum(1 for t in combo["texts"] if t and t in script)
        if copied >= 2 or chars < 100 or not (has_sorting and has_loading):
            script = _host_scaffold(brand)
            report["ollama"]["scaffold_applied"] = True
        report["script"] = script
        report["script_base"] = base_script
        report["script_chars"] = len(re.sub(r"\s+", "", script))

        video_lock = load_video_lock(
            customer.name,
            output_root=customer.output_root or settings.paths.output_root,
            profile=customer.profile_json if isinstance(customer.profile_json, dict) else None,
        )
        voice = video_lock.get("voice") if isinstance(video_lock.get("voice"), dict) else {}
        narr = synthesize_script(
            script,
            voice_dir,
            lang="zh",
            provider="edge",
            voice=str(voice.get("voice") or settings.tts_voice or "zh-CN-XiaoxiaoNeural"),
            rate=str(voice.get("rate") or settings.tts_rate or "-8%"),
            pitch=str(voice.get("pitch") or getattr(settings, "tts_pitch", "+35Hz") or "+35Hz"),
            volume=str(voice.get("volume") or getattr(settings, "tts_volume", "+12%") or "+12%"),
            allow_fallback=False,
        )
        report["tts"] = {
            "provider": narr.provider,
            "total_duration_sec": narr.total_duration_sec,
            "segment_count": len(narr.segments),
            "voiceover": narr.out_dir,
        }
        srt_path = voice_dir / "subtitle.zh.srt"
        srt_path.write_text(
            srt_from_narration_segments(narr.segments, forbid_title=title),
            encoding="utf-8",
        )

        clips, plan_warnings = _continuous_motion_plan(
            session,
            customer_id=int(customer.id),
            title=title,
            script=script,
            need_sec=float(narr.total_duration_sec),
            seed=SEED + 31,
        )
        template = TemplateDefinition(**DEFAULT_TEMPLATE.model_dump())
        plan = MontagePlan(
            seed=SEED + 31,
            template_name=template.name,
            theme=THEME,
            category="default",
            title=title,
            clips=clips,
            warnings=plan_warnings
            + [
                f"trial: recipe_combo={'+'.join(combo['labels'])}",
                "trial: host_dense + sorting-front motion cuts",
            ],
            blocked=False,
            block_reasons=[],
            orientation="portrait",
            content_theme="仓配",
            copy_components=list(combo["texts"]),
            allowed_facts=list(combo["texts"]),
        )
        report["plan"] = {
            "title": plan.title,
            "clip_count": len(plan.clips),
            "picture_sec": round(sum(c.duration_sec for c in plan.clips), 3),
            "warnings": plan.warnings,
            "clips": [
                {
                    "slot": c.slot,
                    "cliplet_id": c.cliplet_id,
                    "scene": c.scene,
                    "actions": c.actions,
                    "start_sec": c.start_sec,
                    "duration_sec": c.duration_sec,
                    "score": c.score,
                }
                for c in plan.clips
            ],
        }
        (out_root / "plan.json").write_text(
            json.dumps(plan_to_dict(plan), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        vo = voice_dir / "voiceover.wav"
        narration_bed_from_result(narr, vo)
        video_path = out_root / "trial.mp4"
        ok = render_plan(
            settings,
            plan,
            video_path,
            template.model_dump(),
            render_meta={
                "trial": "narration_first_v2_formal_motion",
                "narration_script": script,
                "ollama_narration": bool(rewritten.get("ok")),
            },
            profile=customer.profile_json if isinstance(customer.profile_json, dict) else None,
            customer_name=customer.name,
            narration_path=vo,
        )
        if not ok:
            raise RuntimeError("render_plan failed")

        sub_lock = video_lock.get("subtitle") if isinstance(video_lock.get("subtitle"), dict) else {}
        burned = burn_subtitles_inplace(
            video_path,
            srt_path,
            work_dir=voice_dir,
            font_size=int(sub_lock.get("font_size") or 64),
            bottom_padding_px=int(sub_lock.get("bottom_padding_px") or 420),
            color=str(sub_lock.get("color") or "#FFFFFF"),
            stroke_color=str(sub_lock.get("outline") or "#000000"),
            stroke_width=int(sub_lock.get("outline_width") or 3),
            align=str(sub_lock.get("align") or "center"),
        )
        import subprocess

        def _dur(path: Path) -> float:
            p = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            try:
                return float((p.stdout or "0").strip() or 0)
            except ValueError:
                return 0.0

        vo_dur = _dur(vo)
        vid_dur = _dur(video_path)
        report["render"] = {
            "video": str(video_path),
            "voiceover": str(vo),
            "srt": str(srt_path),
            "subtitle_burned": bool(burned.get("ok")),
            "burn_error": burned.get("error"),
            "bytes": video_path.stat().st_size if video_path.is_file() else 0,
            "vo_duration_sec": vo_dur,
            "video_duration_sec": vid_dur,
            "picture_gt_vo": vid_dur > vo_dur,
        }
        report["ok"] = True
        (out_root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        report["error"] = str(exc)
        (out_root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
