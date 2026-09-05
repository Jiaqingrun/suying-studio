"""跟镜精品 · 跟镜简报编译（本客户材料 only）。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from engine.pack.scene_tour_copy import reason_zh
from engine.pack.scene_tour_skeleton import resolve_industry_key, skeleton_for


def _banned_from_profile(profile: dict[str, Any]) -> list[str]:
    compliance = profile.get("compliance") if isinstance(profile.get("compliance"), dict) else {}
    terms = compliance.get("banned_terms") if isinstance(compliance.get("banned_terms"), list) else []
    out = [str(t).strip() for t in terms if str(t).strip()]
    # 跟镜默认加一批硬禁（生活服务）
    out.extend(["免单", "包治", "根治", "疗效保证", "一次见效", "医美手术"])
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _short_blurb_from_markdown(md: str) -> str:
    """从企业资料合规弱化稿抽取「短版」简介。"""
    text = str(md or "")
    m = re.search(r"\*\*短版\*\*\s*\n([^\n]+)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"短版\s*\n([^\n]+)", text)
    if m:
        return m.group(1).strip()
    return ""


def _pitch_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    brand = profile.get("brand") if isinstance(profile.get("brand"), dict) else {}
    pitch = profile.get("pitch") if isinstance(profile.get("pitch"), dict) else {}
    about = profile.get("about") if isinstance(profile.get("about"), dict) else {}
    positioning = str(
        pitch.get("positioning")
        or brand.get("positioning")
        or about.get("positioning")
        or brand.get("tagline")
        or ""
    ).strip()
    tagline = str(pitch.get("tagline") or brand.get("slogan") or brand.get("tagline") or "").strip()
    lines: list[str] = []
    for key in ("lines", "selling_points", "bullets"):
        raw = pitch.get(key) or about.get(key) or brand.get(key)
        if isinstance(raw, list):
            lines.extend(str(x).strip() for x in raw if str(x).strip())
    for key in ("intro", "short_intro", "blurb"):
        v = pitch.get(key) or about.get(key) or brand.get(key)
        if isinstance(v, str) and v.strip():
            lines.append(v.strip())
    return {
        "positioning": positioning,
        "tagline": tagline,
        "lines": lines,
    }


def _pitch_from_repo_docs(customer_name: str) -> dict[str, Any]:
    """仓内 configs/customers/<名>/企业资料-合规弱化.md。"""
    root = Path(__file__).resolve().parents[2] / "configs" / "customers" / str(customer_name)
    md = _read_text(root / "企业资料-合规弱化.md")
    short = _short_blurb_from_markdown(md)
    lines: list[str] = []
    if short:
        lines.append(short)
    # 口号行
    m = re.search(r"\|\s*口号\s*\|\s*([^|]+)\|", md)
    tagline = m.group(1).strip() if m else ""
    m2 = re.search(r"\|\s*定位\s*\|\s*([^|]+)\|", md)
    positioning = re.sub(r"\*+", "", m2.group(1)).strip() if m2 else ""
    # 服务口径要点
    for pat in (
        r"到店沟通需求后确定护理方案[^\n]*",
        r"服务态度礼貌认真[^\n]*",
        r"肌肤护理[^\n]*",
    ):
        hit = re.search(pat, md)
        if hit:
            lines.append(hit.group(0).strip(" -"))
    return {"positioning": positioning, "tagline": tagline, "lines": lines}


def _industry_default_pitch(industry_key: str) -> dict[str, Any]:
    if industry_key == "life-service":
        return {
            "positioning": "本地养生美容护理门店",
            "tagline": "",
            "lines": [
                "到店沟通后再定护理方案",
                "流程说清楚再开始",
                "温和护理，态度认真细致",
            ],
        }
    if industry_key == "building-supply":
        return {
            "positioning": "仓配在架批发",
            "tagline": "",
            "lines": [
                "仓内在架一目了然",
                "装车出库有序可跟",
                "到店选材，规格成色看得见",
            ],
        }
    return {"positioning": "", "tagline": "", "lines": []}


def compile_enterprise_pitch(
    *,
    customer_name: str,
    profile: dict[str, Any],
    industry_key: str,
) -> dict[str, Any]:
    """汇总企业宣传口径（本客户 only）。"""
    chunks = [
        _industry_default_pitch(industry_key),
        _pitch_from_repo_docs(customer_name),
        _pitch_from_profile(profile),
    ]
    positioning = ""
    tagline = ""
    lines: list[str] = []
    for ch in chunks:
        if ch.get("positioning"):
            positioning = str(ch["positioning"])
        if ch.get("tagline"):
            tagline = str(ch["tagline"])
        for line in ch.get("lines") or []:
            s = str(line).strip()
            if s and s not in lines:
                lines.append(s)
    # 压缩过长句；模板轮换只用短卖点，长句留给模型参考
    compact: list[str] = []
    for line in lines:
        pure = re.sub(r"\s+", "", line)
        pure = re.sub(r"\*+", "", pure)
        if len(pure) > 40:
            pure = pure[:40]
        if pure and pure not in compact:
            compact.append(pure)
        if len(compact) >= 10:
            break
    short = [l for l in compact if 6 <= len(l) <= 20]
    if not short:
        short = [(l[:16] if len(l) > 16 else l) for l in compact if l][:6]
    return {
        "positioning": re.sub(r"\*+", "", positioning)[:40],
        "tagline": re.sub(r"[（(].*$", "", tagline).strip()[:24],
        "lines": short[:8],
        "hooks": compact[:8],
    }


def compile_scene_tour_brief(
    *,
    customer_id: int,
    customer_name: str,
    profile: dict[str, Any] | None,
    rule_industry_key: str | None = None,
    reference_hooks: list[str] | None = None,
) -> dict[str, Any]:
    """编译跟镜简报。失败时 ok=False 且 reasons 为中文。"""
    profile = profile if isinstance(profile, dict) else {}
    industry_key = resolve_industry_key(profile)
    reasons: list[str] = []
    if not industry_key:
        reasons.append(reason_zh("missing_industry"))
        return {
            "ok": False,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "reasons": reasons,
        }
    if rule_industry_key and str(rule_industry_key).strip() not in ("", industry_key):
        reasons.append(reason_zh("industry_mismatch"))
        return {
            "ok": False,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "industry_key": industry_key,
            "reasons": reasons,
        }
    skel = skeleton_for(industry_key)
    if not skel:
        reasons.append(f"暂无「{industry_key}」行业游览骨架，请联系运维补充。")
        return {
            "ok": False,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "industry_key": industry_key,
            "reasons": reasons,
        }

    brand = profile.get("brand") if isinstance(profile.get("brand"), dict) else {}
    display = str(brand.get("display_name") or customer_name or "本店").strip()
    stages = list(skel.get("stages") or [])
    pitch = compile_enterprise_pitch(
        customer_name=str(customer_name),
        profile=profile,
        industry_key=str(industry_key),
    )
    hooks = [str(x).strip() for x in (reference_hooks or []) if str(x).strip()][:20]
    # 企业卖点并入参考角度（写词用），仍禁止整段金句照抄
    for line in (pitch.get("hooks") or pitch.get("lines") or []):
        if line not in hooks:
            hooks.append(line)
        if len(hooks) >= 20:
            break
    payload = {
        "ok": True,
        "customer_id": int(customer_id),
        "customer_name": customer_name,
        "industry_key": industry_key,
        "industry_label_zh": skel.get("label_zh"),
        "brand_display": display,
        "stages": stages,
        "min_shots": int(skel.get("min_shots") or 8),
        "max_shots": int(skel.get("max_shots") or 16),
        "title_seeds": list(skel.get("title_seeds") or []),
        "weak_anchors": dict(skel.get("weak_anchors") or {}),
        "banned_terms": _banned_from_profile(profile),
        "reference_hooks": hooks,
        "enterprise_pitch": pitch,
        "tone": "promo_guide",
        "reasons": [],
    }
    blob = json.dumps(
        {
            "customer_id": payload["customer_id"],
            "industry_key": payload["industry_key"],
            "stages": payload["stages"],
            "banned": payload["banned_terms"],
            "hooks": payload["reference_hooks"],
            "pitch": pitch,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    payload["fingerprint"] = hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
    return payload
