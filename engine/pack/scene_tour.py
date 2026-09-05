"""跟镜精品 · 规划与写词主入口。"""

from __future__ import annotations

import json
import re
import urllib.request
from typing import Any

from sqlalchemy.orm import Session

from engine.catalog.db import Customer
from engine.pack.scene_tour_brief import compile_scene_tour_brief
from engine.pack.scene_tour_buckets import (
    coverage_report,
    pick_clips_for_stages,
    recent_cliplet_cooldown_ids,
    station_inventory,
)
from engine.pack.scene_tour_copy import bucket_label, reason_zh
from engine.pack.scene_tour_skeleton import expand_route_stages, skeleton_for
from engine.pack.scene_tour_validate import validate_scene_tour_shots
from engine.template.engine import ClipPlan, MontagePlan


def _estimate_dur(text: str) -> float:
    n = max(8, len(text.replace(" ", "").replace("\n", "")))
    return max(2.2, min(6.5, n / 3.3))


def _brand_aliases(brief: dict[str, Any]) -> list[str]:
    """品牌别名：全片旁白合计最多出现一次。"""
    names: list[str] = []
    display = str(brief.get("brand_display") or "").strip()
    if display:
        names.append(display)
        # 「北京始峰伟业（始峰五金）」→ 拆出括号内外
        if "（" in display and "）" in display:
            outer, rest = display.split("（", 1)
            inner = rest.split("）", 1)[0].strip()
            if outer.strip():
                names.append(outer.strip())
            if inner:
                names.append(inner)
        for sep in ("/", "·", "|"):
            if sep in display:
                names.extend(p.strip() for p in display.split(sep) if p.strip())
    for key in ("customer_name",):
        v = str(brief.get(key) or "").strip()
        if v:
            names.append(v)
    # 去重，长词优先（先剥长名）
    uniq: list[str] = []
    seen: set[str] = set()
    for n in sorted(names, key=len, reverse=True):
        if n and n not in seen and n not in ("本店", "测店"):
            seen.add(n)
            uniq.append(n)
    return uniq


def _count_brand_hits(text: str, brands: list[str]) -> int:
    hits = 0
    remain = text
    for b in brands:
        if not b:
            continue
        c = remain.count(b)
        if c:
            hits += c
            remain = remain.replace(b, "")
    return hits


def _strip_brands(text: str, brands: list[str]) -> str:
    out = text
    for b in brands:
        if b:
            out = out.replace(b, "")
    # 清理「的店内」「，，」等剥离残留
    out = re.sub(r"[，,]{2,}", "，", out)
    out = re.sub(r"的{2,}", "的", out)
    out = re.sub(r"^\s*[的与和及]\s*", "", out)
    out = re.sub(r"\s+", "", out)
    return out.strip("，。、 ")


def _brand_for_narration(brand: str) -> str:
    """旁白点名用短称，避免「北京××（××）」拖垮断句与时长。"""
    raw = str(brand or "").strip()
    if not raw:
        return ""
    if "（" in raw and "）" in raw:
        inner = raw.split("（", 1)[1].split("）", 1)[0].strip()
        if 2 <= len(inner) <= 8:
            return inner
    short = raw.replace("北京", "").replace("公司", "").strip("（）() ")
    if 2 <= len(short) <= 8:
        return short
    # 取前缀汉字
    chars = re.findall(r"[\u4e00-\u9fff]", raw)
    return "".join(chars[:6]) if chars else raw[:6]


def _template_line(
    bucket: str,
    anchors: list[str],
    brand: str,
    role: str,
    *,
    allow_brand: bool = False,
    industry_key: str = "",
    pitch: dict[str, Any] | None = None,
    shot_index: int = 0,
    used_angles: list[str] | None = None,
) -> str:
    """宣传导览模板：画面锚点 + 企业口径；禁止纯场景清点。"""
    from engine.pack.scene_tour_diction import is_usable_anchor, normalize_diction_text
    from engine.pack.scene_tour_grounding import pick_slot_anchor

    a0 = pick_slot_anchor(bucket, [a for a in anchors if is_usable_anchor(a)])
    brand_bit = _brand_for_narration(brand) if allow_brand and brand else ""
    pitch = pitch if isinstance(pitch, dict) else {}
    lines = [str(x).strip() for x in (pitch.get("lines") or []) if str(x).strip()]
    positioning = str(pitch.get("positioning") or "").strip()
    used = {str(x) for x in (used_angles or []) if str(x).strip()}
    # 按镜轮换卖点，且本片内不重复同一句流程话
    angle = ""
    if lines:
        ordered = [lines[(int(shot_index) + i) % len(lines)] for i in range(len(lines))]
        for cand in ordered:
            short = cand
            if len(re.sub(r"\s+", "", short)) > 16:
                short = re.sub(r"[，。；].*$", "", short)[:14]
            if short and short not in used:
                angle = short
                break
        if not angle:
            angle = ""  # 宁可不贴卖点，也不句句重复流程
    elif positioning and positioning not in used:
        angle = positioning[:14]

    def _with_brand(body: str) -> str:
        if brand_bit:
            return f"{brand_bit}，{body}"
        return body

    # 生活服务：固定搭配，绝不把任意物件塞进「顺着×往里走」
    life = {
        "honor_wall": (
            f"墙上{a0}整齐陈列，本地门店把口碑摆在明处。"
            if a0 not in ("荣誉墙", "展示墙")
            else "荣誉展示摆在明处，本地门店靠口碑说话。"
        ),
        "culture_wall": f"侧壁写着店里的服务理念，到店先把规矩看清楚。",
        "slippers": (
            f"进门换上{a0}，把外面的匆忙留在门外。"
            if "拖鞋" in a0 or a0 in ("鞋架", "鞋柜")
            else "进门先换鞋，把外面的匆忙留在门外。"
        ),
        "vanity": "梳妆台面收拾利落，进门就感到干净舒适。",
        "corridor": (
            f"顺着{a0}往里走，门店空间敞亮好找。"
            if any(k in a0 for k in ("走廊", "过道", "廊道", "门洞", "拱门", "通道"))
            else "再往里走，空间敞亮，护理间好找。"
        ),
        "sterilize": "消毒间门牌清楚，卫生环节看得见更安心。",
        "treatment_bed": (
            f"{a0}已铺好巾单，到店沟通后再按你的节奏护理。"
            if any(k in a0 for k in ("护理床", "床位", "巾单"))
            else "护理床铺好巾单，到店沟通后再按你的节奏护理。"
        ),
        "treatment_room": "护理间里器械备妥，流程按步来、不慌不乱。",
        "treatment_action": "护理做得细致，服务态度认真温和。",
        "supply": "备料分格摆好，用得到的都在手边。",
        "tea": "小憩处备着茶点，到店体验更从容。",
        "entrance": "走近入口好认好进，本地门店就在这里。",
        "other": "现场细节看得见，服务态度也经得起看。",
    }
    # 仓配：在架陈列能力宣传（禁词「现货」不进旁白）
    supply = {
        "storefront": (
            f"{brand_bit}，门口{a0}清楚，到店选材好找门。"
            if brand_bit
            else f"先看这面{a0}，到店选材一眼能认出来。"
        ),
        "entrance": f"走近入口看见{a0}，到店选材好进。",
        "warehouse": (
            f"仓里{a0}码得整齐，在架当场能看清。"
            if a0 not in ("仓内货架",)
            else "仓里货架码得整齐，在架当场能看清。"
        ),
        "loading": "跟到装卸现场看装车，出库配货有序可跟。",
        "product_closeup": f"近看{a0}成色，规格细节方便您选。",
        "other": "现场一看就明白，仓配在架能力摆在眼前。",
        "supply": "架上分格清楚，取用配货都方便。",
    }
    special = life if industry_key == "life-service" else supply if industry_key == "building-supply" else {**supply, **life}
    if bucket in special:
        text = special[bucket]
        if allow_brand and brand_bit and not text.startswith(brand_bit):
            text = _with_brand(text)
        # 第二拍轻贴短卖点；已用过的流程句不再贴
        if angle and angle not in text and "，" in text:
            parts = text.rstrip("。").split("，")
            if brand_bit and parts and parts[0] == brand_bit and len(parts) >= 2:
                text = f"{parts[0]}，{parts[1]}，{angle}。"
            elif len(parts) >= 2:
                # 保留画面半句，只换/补卖点半句（避免整句变流程复读）
                text = f"{parts[0]}，{angle}。"
        if used_angles is not None and angle:
            used_angles.append(angle)
        return normalize_diction_text(text)

    catalog = {
        "open": (
            f"先看这边，{brand_bit or '本店'}把现场服务亮出来。"
            if brand_bit
            else "先看这边，到店体验从这里开始。"
        ),
        "body": f"转到这边，{angle or '服务细节'}看得见。",
        "close": "收在这一角，欢迎到店慢慢了解。",
    }
    if used_angles is not None and angle:
        used_angles.append(angle)
    return normalize_diction_text(catalog.get(role, catalog["body"]))


def _ollama_line(
    *,
    bucket: str,
    anchors: list[str],
    brand: str,
    banned: list[str],
    hooks: list[str],
    allow_brand: bool = False,
    industry_key: str = "",
    model: str = "qwen2.5:32b",
    visual_description: str = "",
    pitch: dict[str, Any] | None = None,
    forbid_tails: list[str] | None = None,
) -> str | None:
    pitch = pitch if isinstance(pitch, dict) else {}
    positioning = str(pitch.get("positioning") or "").strip()
    tagline = str(pitch.get("tagline") or "").strip()
    pitch_lines = [str(x).strip() for x in (pitch.get("lines") or []) if str(x).strip()][:5]
    industry_hint = (
        "行业是建材仓配批发：结合在架陈列/装车/选材能力做宣传，只点画面里真实物件，勿写护理美容话术。"
        if industry_key == "building-supply"
        else "行业是生活服务门店：结合到店沟通、温和护理、清晰流程做宣传，只点画面里真实陈设，勿编造疗效。"
        if industry_key == "life-service"
        else "结合本店企业口径做宣传介绍，只写画面里真实出现的内容。"
    )
    brand_rule = (
        f"本句允许点一次短品牌「{_brand_for_narration(brand) or brand}」，不要写全称括号；品牌后用逗号换气。"
        if allow_brand and brand
        else "本句禁止出现品牌名/店名（片名已露出）。"
    )
    visual_rule = (
        f"画面里出现的物件（只能点其中一个作锚点，禁止复述整段描述）："
        f"{','.join(anchors[:4]) or '现场陈设'}。"
        "禁止照抄或改写视觉模型长描述。"
        if anchors
        else "只能用画面要点词，禁止编造。"
    )
    # 故意不把完整 visual_description 喂给模型，避免清点复述
    del visual_description
    pitch_rule = (
        f"企业定位：{positioning or '本店'}。"
        f"可用宣传角度（选一句意思融入，勿整段照抄）：{'；'.join(pitch_lines) if pitch_lines else '；'.join(hooks[:3]) or '到店体验'}。"
        + (f"口号可意会勿硬念全句：{tagline}。" if tagline else "")
    )
    forbid = [str(x).strip() for x in (forbid_tails or []) if str(x).strip()]
    forbid_rule = (
        f"后半拍禁止再写这些已用过的收束（须换全新角度，勿同义复读）：{'；'.join(forbid[:6])}。"
        if forbid
        else "后半拍卖点每镜换角度，禁止全片重复同一收束短语（如句句「您的舒适体验」）。"
    )
    prompt = (
        "你是跟镜精品旁白写手。写一句「店主带看现场」的宣传口播，目的是介绍企业、促成到店了解。"
        "硬性：①像人说话、体面自然（不要古文赋体）；②必须用逗号「，」分成两拍；"
        "③前半拍落一个看得见的画面锚点，后半拍用企业口径做宣传加工；④纯中文，禁止英文字母。"
        "禁止：画面清点（摆放着/可见/一名女性/背景为/俯身操作）；禁止复述视觉描述；"
        "禁止端然/疏朗/入画等雅词堆砌；禁止管状物等机器名；禁止空壳营销套话与疗效承诺。"
        "写一句 16–28 字（含标点），像：换上拖鞋，把外面的匆忙留在门外。"
        "禁止胡乱搭配：不能说顺着浴袍往里走、玻璃隔断铺好巾单这类不像人话的句子。"
        f"{industry_hint}"
        f"{visual_rule}"
        f"{pitch_rule}"
        f"{forbid_rule}"
        f"{brand_rule}"
        f"场景：{bucket_label(bucket)}。"
        f"禁用词：{','.join(banned[:30])}。"
        '只输出 JSON：{"text":"..."}'
    )
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.5, "num_predict": 120},
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            data = json.load(r)
        content = (data.get("message") or {}).get("content") or ""
        parsed = json.loads(content)
        text = str(parsed.get("text") or "").strip()
        return text or None
    except Exception:
        return None


def write_lines_for_shots(
    shots: list[dict[str, Any]],
    brief: dict[str, Any],
    *,
    use_ollama: bool = True,
) -> list[dict[str, Any]]:
    from engine.pack.scene_tour_diction import (
        diction_fail_reasons,
        is_usable_anchor,
        line_reuses_promo_tail,
        max_chars_for_available,
        normalize_diction_text,
        polish_anchor_join,
        promo_second_beat,
        replace_promo_second_beat,
        shorten_ornate_line,
    )
    from engine.pack.scene_tour_grounding import anchors_from_evidence

    brand = str(brief.get("brand_display") or "本店")
    brands = _brand_aliases(brief)
    industry_key = str(brief.get("industry_key") or "")
    banned = list(brief.get("banned_terms") or [])
    hooks = list(brief.get("reference_hooks") or [])
    pitch = brief.get("enterprise_pitch") if isinstance(brief.get("enterprise_pitch"), dict) else {}
    weak = brief.get("weak_anchors") if isinstance(brief.get("weak_anchors"), dict) else {}
    brand_used = False
    narr_brand = _brand_for_narration(brand) or brand
    used_angles: list[str] = []
    used_beats: list[str] = []
    pitch_pool = [str(x).strip() for x in (pitch.get("lines") or []) if str(x).strip()]
    # 备用收束：保证模板改尾时有差异化角度
    fallback_tails = pitch_pool + [
        "到店沟通后再定方案",
        "流程说清楚再开始",
        "服务态度认真温和",
        "在架当场能看清",
        "欢迎到店慢慢了解",
    ]
    out: list[dict[str, Any]] = []
    for i, shot in enumerate(shots):
        bucket = str(shot.get("bucket") or "other")
        role = str(shot.get("role") or "body")
        tokens = list(shot.get("visual_tokens") or [])
        visual_desc = str(shot.get("visual_description") or "")
        avail = float(shot.get("available_sec") or shot.get("duration_sec") or 6.5)
        char_budget = max_chars_for_available(avail)
        anchors = [
            a
            for a in anchors_from_evidence(
                bucket,
                weak=list(weak.get(bucket) or [bucket_label(bucket)]),
                tokens=tokens,
                visual_description=visual_desc,
            )
            if is_usable_anchor(a)
        ]
        if not anchors:
            anchors = [bucket_label(bucket)]
        allow_brand = (not brand_used) and (i == 0 and role in ("open", "body"))
        text = None
        if use_ollama:
            text = _ollama_line(
                bucket=bucket,
                anchors=anchors,
                brand=narr_brand,
                banned=banned,
                hooks=hooks,
                allow_brand=allow_brand,
                industry_key=industry_key,
                visual_description=visual_desc,
                pitch=pitch,
                forbid_tails=used_beats,
            )
            if text:
                text = normalize_diction_text(text)
                if diction_fail_reasons(
                    text, available_sec=avail, visual_description=visual_desc
                ) or line_reuses_promo_tail(text, used_beats):
                    text = None
        if not text:
            text = _template_line(
                bucket,
                anchors,
                narr_brand,
                role,
                allow_brand=allow_brand,
                industry_key=industry_key,
                pitch=pitch,
                shot_index=i,
                used_angles=used_angles,
            )
        text = normalize_diction_text(text)
        if anchors and not any(a in text for a in anchors):
            # 仅当槽位锚点本身可入句时才强行拼入，避免「浴袍」硬塞
            from engine.pack.scene_tour_grounding import pick_slot_anchor

            slot_a = pick_slot_anchor(bucket, anchors)
            if slot_a and slot_a not in text and slot_a != bucket_label(bucket):
                text = polish_anchor_join(text, slot_a)
        hits = _count_brand_hits(text, brands)
        if hits and (brand_used or not allow_brand or hits > 1):
            text = _strip_brands(text, brands)
            text = normalize_diction_text(text)
        # 字数必须落入片源预算，否则声画必崩
        text = shorten_ornate_line(text, char_budget)
        if diction_fail_reasons(text, available_sec=avail, visual_description=visual_desc):
            # 二次兜底：宣传模板（禁用品牌防重复）
            text = shorten_ornate_line(
                _template_line(
                    bucket,
                    anchors,
                    narr_brand,
                    role,
                    allow_brand=False,
                    industry_key=industry_key,
                    pitch=pitch,
                    shot_index=i,
                    used_angles=used_angles,
                ),
                char_budget,
            )
        # 同片收束撞车：只换第二拍，保留画面锚点半句
        if line_reuses_promo_tail(text, used_beats):
            fresh = ""
            for cand in fallback_tails:
                short = cand
                if len(re.sub(r"\s+", "", short)) > 16:
                    short = re.sub(r"[，。；].*$", "", short)[:14]
                if short and not line_reuses_promo_tail(
                    replace_promo_second_beat(text, short), used_beats
                ):
                    fresh = short
                    break
            if fresh:
                text = shorten_ornate_line(
                    replace_promo_second_beat(text, fresh), char_budget
                )
                if fresh not in used_angles:
                    used_angles.append(fresh)
        if _count_brand_hits(text, brands) > 0:
            brand_used = True
        beat = promo_second_beat(text)
        if beat and beat not in used_beats:
            used_beats.append(beat)
        item = dict(shot)
        item["text"] = text
        item["anchors"] = anchors
        item["duration_sec"] = round(min(float(avail), max(2.8, _estimate_dur(text))), 3)
        out.append(item)
    return out


def pick_title(brief: dict[str, Any], seed: int) -> str:
    seeds = list(brief.get("title_seeds") or ["门店实景\n画面讲解"])
    if not seeds:
        return "门店实景\n画面讲解"
    return seeds[int(seed) % len(seeds)]


def build_scene_tour_plan(
    session: Session,
    *,
    customer: Customer,
    seed: int,
    orientation: str = "portrait",
    production_rules: dict[str, Any] | None = None,
    use_ollama: bool = True,
    cooldown_limit: int = 40,
) -> MontagePlan:
    profile = customer.profile_json if isinstance(customer.profile_json, dict) else {}
    rules = production_rules if isinstance(production_rules, dict) else {}
    rule_industry = rules.get("industry_key")
    brief = compile_scene_tour_brief(
        customer_id=int(customer.id),
        customer_name=str(customer.name),
        profile=profile,
        rule_industry_key=str(rule_industry) if rule_industry else None,
        reference_hooks=list(rules.get("reference_hooks") or [])
        if isinstance(rules.get("reference_hooks"), list)
        else None,
    )
    if not brief.get("ok"):
        return MontagePlan(
            seed=seed,
            template_name="scene-tour",
            theme="scene_tour",
            category="scene_tour",
            title="跟镜精品",
            clips=[],
            warnings=[],
            blocked=True,
            block_reasons=list(brief.get("reasons") or [reason_zh("missing_industry")]),
            orientation=orientation,
            content_fingerprint={"production_mode": "scene_tour", "brief": brief},
        )

    cov = coverage_report(
        session,
        customer_id=int(customer.id),
        stages=list(brief.get("stages") or []),
        min_per_bucket=1,
        orientation=orientation,
    )
    if not cov.get("ok"):
        return MontagePlan(
            seed=seed,
            template_name="scene-tour",
            theme="scene_tour",
            category="scene_tour",
            title="跟镜精品",
            clips=[],
            warnings=[],
            blocked=True,
            block_reasons=list(cov.get("reasons") or ["场景覆盖不足"]),
            orientation=orientation,
            content_fingerprint={
                "production_mode": "scene_tour",
                "brief": brief,
                "coverage": cov,
            },
        )

    inv_rep = station_inventory(
        session,
        customer_id=int(customer.id),
        orientation=orientation,
        require_grounding=True,
        min_available_sec=6.8,
    )
    inv_counts = dict(inv_rep.get("counts") or {})
    # 必选站位硬库存为 0 → 拒片（有才排）
    for st in brief.get("stages") or []:
        if not st.get("required"):
            continue
        rk = str(st.get("key") or "")
        if inv_counts.get(rk, 0) <= 0:
            return MontagePlan(
                seed=seed,
                template_name="scene-tour",
                theme="scene_tour",
                category="scene_tour",
                title="跟镜精品",
                clips=[],
                warnings=[],
                blocked=True,
                block_reasons=[
                    reason_zh("required_missing", bucket=bucket_label(rk)),
                    "片库无可用片段，不能安排该站位。",
                ],
                orientation=orientation,
                content_fingerprint={
                    "production_mode": "scene_tour",
                    "brief": brief,
                    "station_inventory": inv_rep,
                },
            )

    skel = skeleton_for(str(brief.get("industry_key") or "")) or {
        "stages": list(brief.get("stages") or []),
        "route_recipes": [],
    }
    route_stages, route_meta = expand_route_stages(
        skel,
        inventory=inv_counts,
        seed=int(seed),
        recent_path_fps=[],
    )
    if len(route_stages) < 3:
        return MontagePlan(
            seed=seed,
            template_name="scene-tour",
            theme="scene_tour",
            category="scene_tour",
            title="跟镜精品",
            clips=[],
            warnings=[],
            blocked=True,
            block_reasons=[reason_zh("too_few_shots"), "按库存可排站位不足。"],
            orientation=orientation,
            content_fingerprint={
                "production_mode": "scene_tour",
                "brief": brief,
                "station_inventory": inv_rep,
                "route": route_meta,
            },
        )

    cool = recent_cliplet_cooldown_ids(
        session, customer_id=int(customer.id), limit=int(cooldown_limit)
    )
    exclude_ids: set[int] = set()
    pick_warnings: list[str] = []
    shots: list[dict[str, Any]] = []
    report: dict[str, Any] = {"ok": False, "reasons": ["未规划"]}
    heal_attempts: list[dict[str, Any]] = []

    # 自纠：声画错配 / 粗标片段 → 排除 cliplet 后重选（不依赖人工 Cursor）
    cooldown_relaxed = False
    for attempt in range(1, 6):
        draft_shots, pick_warnings = pick_clips_for_stages(
            session,
            customer_id=int(customer.id),
            stages=route_stages,
            cooldown_ids=cool,
            orientation=orientation,
            seed=seed + attempt - 1,
            exclude_ids=exclude_ids,
            require_grounding=True,
            inventory_counts=inv_counts,
        )
        if len(draft_shots) < 4:
            if cool and not cooldown_relaxed:
                cool = set()
                cooldown_relaxed = True
                pick_warnings = list(pick_warnings) + ["选片冷却已自动放宽，以保证跟镜成片。"]
                continue
            report = {
                "ok": False,
                "reasons": [reason_zh("too_few_shots")] + pick_warnings,
                "bad_cliplet_ids": sorted(exclude_ids),
            }
            break
        # 篇章顺序对照「本次路线」而非整份行业骨架（允许配方非骨架单调序）
        brief_route = dict(brief)
        brief_route["stages"] = list(route_stages)
        shots = write_lines_for_shots(draft_shots, brief_route, use_ollama=use_ollama)
        report = validate_scene_tour_shots(shots, brief=brief_route)
        if not report.get("ok"):
            shots = write_lines_for_shots(draft_shots, brief_route, use_ollama=False)
            report = validate_scene_tour_shots(shots, brief=brief_route)
        heal_attempts.append(
            {
                "attempt": attempt,
                "ok": bool(report.get("ok")),
                "reasons": list(report.get("reasons") or [])[:6],
                "excluded": sorted(exclude_ids),
                "cliplet_ids": [int(s.get("cliplet_id") or 0) for s in shots],
                "route": route_meta,
            }
        )
        if report.get("ok"):
            break
        bad = {int(x) for x in (report.get("bad_cliplet_ids") or []) if x}
        if not bad:
            bad = {int(s.get("cliplet_id") or 0) for s in shots if s.get("cliplet_id")}
        if not bad - exclude_ids:
            break
        exclude_ids |= bad

    if len(shots) < 4 or not report.get("ok"):
        return MontagePlan(
            seed=seed,
            template_name="scene-tour",
            theme="scene_tour",
            category="scene_tour",
            title=pick_title(brief, seed) if shots else "跟镜精品",
            clips=[],
            warnings=pick_warnings,
            blocked=True,
            block_reasons=list(report.get("reasons") or [reason_zh("too_few_shots")])
            + pick_warnings,
            orientation=orientation,
            content_fingerprint={
                "production_mode": "scene_tour",
                "brief": brief,
                "validation_report": report,
                "shot_script": shots,
                "heal_attempts": heal_attempts,
                "station_inventory": inv_rep,
                "route": route_meta,
            },
        )

    clips: list[ClipPlan] = []
    for i, shot in enumerate(shots):
        role = str(shot.get("role") or "body")
        name = "hook" if i == 0 else ("cta" if i == len(shots) - 1 else f"body{i}")
        clips.append(
            ClipPlan(
                slot=name,
                asset_uuid=str(shot["asset_uuid"]),
                source_path=str(shot["source_path"]),
                start_sec=float(shot.get("start_sec") or 0),
                duration_sec=float(shot.get("duration_sec") or 3.5),
                cliplet_id=int(shot["cliplet_id"]),
                score=float(shot.get("score") or 0.9),
                description=str(shot.get("text") or ""),
                theme="scene_tour",
                scene=str(shot.get("bucket") or "other"),
                objects=list(shot.get("anchors") or []),
                actions=[],
            )
        )

    locked = "。".join(
        str(s.get("text") or "").strip().rstrip("。") for s in shots if str(s.get("text") or "").strip()
    )
    if locked and not locked.endswith("。"):
        locked = f"{locked}。"
    title = pick_title(brief, seed)
    # READY_GATE：VIDEO_LOCK → Job 冻结规则；禁止只硬编码默认黄字（#426 熔断根因）
    from engine.pack.video_lock import resolve_title_style

    title_style = resolve_title_style(
        {
            "font_size": 84,
            "glyph_top_px": 220,
            "effect": "fade",
            "align": "center",
            "position": "top",
            "max_chars": 24,
            "max_lines": 2,
            "layout": "dual_chip",
        },
        customer_name=str(customer.name),
        production_rules=rules if isinstance(rules, dict) else None,
        profile=profile if isinstance(profile, dict) else None,
        output_root=getattr(customer, "output_root", None),
        orientation=orientation,
    )
    return MontagePlan(
        seed=seed,
        template_name="scene-tour",
        theme="scene_tour",
        category="scene_tour",
        title=title,
        clips=clips,
        warnings=pick_warnings + ["跟镜精品：旁白已锁定，不走词池金句"],
        blocked=False,
        block_reasons=[],
        orientation=orientation,
        title_style=title_style,
        content_fingerprint={
            "production_mode": "scene_tour",
            "brief_fingerprint": brief.get("fingerprint"),
            "brief": {
                "industry_key": brief.get("industry_key"),
                "customer_id": brief.get("customer_id"),
            },
            "locked_script": locked,
            "shot_script": shots,
            "validation_report": report,
            "coverage": cov,
            "station_inventory": inv_rep,
            "route": route_meta,
            "heal_attempts": heal_attempts,
            "clip_aligned_slots": True,
        },
        copy_components=[str(s.get("text") or "") for s in shots],
        allowed_facts=list(brief.get("banned_terms") or [])[:0],  # 不用 facts 灌金句
        forbidden_claims=list(brief.get("banned_terms") or []),
    )
