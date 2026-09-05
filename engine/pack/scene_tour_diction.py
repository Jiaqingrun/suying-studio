"""跟镜精品 · 宣传导览合同（跨行业/跨客户）。

硬性：像店主带看做宣传介绍；断句分明；句句落画；企业口径加工，禁止纯画面清点与雅词堆砌。
"""

from __future__ import annotations

import re

_EMPTY_CLAIM_PADS = (
    "服务很到位",
    "体验很好",
    "值得一来",
    "品质有保障",
    "专业又放心",
    "品质生活",
    "精致生活",
    "品质如一",
    "欢迎咨询",
    "到店选购",
)

_ORAL = ("咱们", "啦", "哈哈哈", "绝绝子", "yyds", "冲鸭", "宝子", "家人们")

_FLAT_PATTERNS = (
    re.compile(r"身穿.+?(T恤|裤子|手套)"),
    re.compile(r"手持.+?走向"),
    re.compile(r"可见[\u4e00-\u9fff]{1,8}。?$"),
    re.compile(r"^(画面|视频)展示"),
    re.compile(r"男子|女子|人员身穿"),
    re.compile(r"清晰可见吸引"),
    re.compile(r"过往客户的目光"),
    re.compile(r"(红|黄|蓝|绿|黑|白|灰|棕).{0,6}(红|黄|蓝|绿|黑|白|灰|棕).{0,10}(红|黄|蓝|绿|黑|白|灰|棕)"),
    re.compile(r"(下方|上面|左侧|右侧).{0,4}(黑色|蓝色|红色|黄色).{0,8}(工具|货架|商品)"),
    re.compile(r"弯曲如.+堆叠"),
    re.compile(r"如.{0,2}(电钻|角磨|扳手|工具|管子|板材)"),
    re.compile(r"(黑|蓝|绿|黄|红).{0,2}(箱|柄|底).{0,4}(绿|黑|蓝|黄|红).{0,2}(箱|柄|底|工具)"),
    re.compile(r"管状物体|管状物|圆柱体|库存堆|巨大的仓"),
)

# 视觉模型清点体 / 场景复述（跟镜宣传旁白一律拒）
_CAPTION_PATTERNS = (
    re.compile(r"摆放着"),
    re.compile(r"背景为"),
    re.compile(r"环境为"),
    re.compile(r"室内场景"),
    re.compile(r"一名(女性|男性|女子|男子|顾客)"),
    re.compile(r"(女士|男子|女子|女性).{0,8}(操作|坐在|站立|俯身)"),
    re.compile(r"可见"),
    re.compile(r"时段\d"),
    re.compile(r"俯身操作"),
    re.compile(r"笔记本电脑|塑料袋"),
    re.compile(r"两人(拥抱|相拥)"),
    re.compile(r"分类[:：]"),
    re.compile(r"映出(人物|门店)"),
    re.compile(r"右侧可见|旁边可见|背景可见"),
    re.compile(r"整理其中(一个|只)"),
    # 不合理人话的槽位搭配
    re.compile(r"顺着(浴袍|拖鞋|证书|奖牌|奖杯|垃圾桶|洞洞板|搁板|仪器|护理品|塑料袋|推车)往里走"),
    re.compile(r"(玻璃隔断|黄色座椅|垃圾桶|洞洞板)铺好巾单"),
)

# 宣传向信号：至少命中一类，避免纯场景说明书
_PROMO_CUES = (
    "到店",
    "护理",
    "沟通",
    "安心",
    "舒适",
    "在架",
    "选材",
    "装车",
    "配货",
    "换上",
    "欢迎",
    "本地",
    "连锁",
    "体验",
    "流程",
    "服务",
    "干净",
    "节奏",
    "用心",
    "细致",
    "专业",
    "门店",
    "为您",
    "给你",
    "方便",
    "当场",
    "一眼",
    "有序",
    "温和",
    "陪伴",
    "方案",
    "批发",
    "出库",
    "在架可见",
    "深耕",
    "多店",
)

_PURPLE_MARKERS = (
    "铺陈",
    "次第",
    "映入",
    "端然",
    "入画",
    "映带",
    "疏朗",
    "门楣",
    "匾额",
    "横披",
    "落画",
    "凝于",
    "生辉",
    "掩映",
    "伫立",
)

_MACHINE_ANCHORS = ("管状物", "管状物体", "圆柱体", "长条状", "矩形板材")

_LATIN = re.compile(r"[A-Za-z]")
_COLOR_WORDS = ("红底", "黄底", "蓝黑", "黑底", "白底", "灰底", "绿底")


def is_usable_anchor(token: str) -> bool:
    """锚点须为人话中文名词；拒绝英文与机器名。"""
    t = str(token or "").strip()
    if len(t) < 2 or len(t) > 16:
        return False
    if _LATIN.search(t):
        return False
    if any(m in t for m in _MACHINE_ANCHORS):
        return False
    if not re.search(r"[\u4e00-\u9fff]", t):
        return False
    return True


def normalize_diction_text(text: str) -> str:
    body = str(text or "").strip()
    body = body.replace(" ", "").replace("\n", "").replace("\t", "")
    body = _LATIN.sub("", body)
    body = re.sub(r"[，,]{2,}", "，", body)
    body = re.sub(r"[。．]{2,}", "。", body)
    body = re.sub(r"，+。", "。", body)
    body = re.sub(r"。+，", "。", body)
    body = body.strip("，、；; ")
    if body and not body.endswith(("。", "！", "？")):
        body = f"{body}。"
    return body


def is_flat_diction(text: str) -> bool:
    body = str(text or "").strip()
    if not body:
        return True
    if any(p.search(body) for p in _FLAT_PATTERNS):
        return True
    if body.endswith("可见") or "，可见" in body or "可见" in body[-6:]:
        return True
    if sum(1 for c in _COLOR_WORDS if c in body) >= 2:
        return True
    return False


def is_caption_inventory(text: str) -> bool:
    """视觉清点 / 场景说明书口吻。"""
    body = str(text or "").strip()
    if not body:
        return True
    if any(p.search(body) for p in _CAPTION_PATTERNS):
        return True
    return False


def has_promo_cue(text: str) -> bool:
    body = str(text or "")
    return any(c in body for c in _PROMO_CUES)


def too_like_visual_description(text: str, visual_description: str, *, min_overlap: int = 8) -> bool:
    """旁白与画面描述大段重合 → 视为复述清点。"""
    body = re.sub(r"[，。、；！？\s]", "", normalize_diction_text(text))
    vis = str(visual_description or "")
    if "；" in vis:
        vis = vis.split("；", 1)[-1]
    vis = re.sub(r"时段[\d\.\-]+秒", "", vis)
    vis = re.sub(r"[，。、；！？\s：:]", "", vis)
    if len(body) < 10 or len(vis) < 16:
        return False
    # 连续 4 字命中计数
    hits = 0
    for i in range(0, max(0, len(body) - 3)):
        gram = body[i : i + 4]
        if gram and gram in vis:
            hits += 1
            if hits >= min_overlap:
                return True
    return False


def purple_marker_count(text: str) -> int:
    body = str(text or "")
    return sum(1 for m in _PURPLE_MARKERS if m in body)


def has_breath_break(text: str) -> bool:
    body = str(text or "")
    return ("，" in body) or ("、" in body) or ("；" in body)


def max_chars_for_available(available_sec: float, *, cps: float = 3.15) -> int:
    avail = max(0.0, float(available_sec or 0))
    budget = int(avail * 0.88 * max(2.4, float(cps)))
    return max(16, min(34, budget))


def shorten_ornate_line(text: str, max_chars: int) -> str:
    """超预算时保留前半拍，用人话收束。"""
    body = normalize_diction_text(text)
    pure = re.sub(r"[，。、；！？\s]", "", body)
    if len(pure) <= max_chars:
        return body
    if "，" in body:
        head = body.split("，", 1)[0].strip("。， ")
        head_pure = re.sub(r"\s+", "", head)
        if 10 <= len(head_pure) <= max_chars - 4:
            return normalize_diction_text(f"{head}，到店一眼能体会")
    chars = list(re.findall(r"[\u4e00-\u9fff0-9]", pure))
    cut = "".join(chars[: max(12, max_chars - 4)])
    if "，" not in cut:
        mid = max(6, len(cut) // 2)
        cut = f"{cut[:mid]}，{cut[mid:]}"
    return normalize_diction_text(cut)


def diction_fail_reasons(
    text: str,
    *,
    available_sec: float | None = None,
    visual_description: str | None = None,
) -> list[str]:
    """返回中文失败原因；空列表 = 通过。"""
    reasons: list[str] = []
    body = normalize_diction_text(text)
    pure = re.sub(r"[，。、；！？\s]", "", body)
    if not pure:
        reasons.append("旁白为空")
        return reasons
    if _LATIN.search(str(text or "")):
        reasons.append("旁白含英文/拉丁词，须纯中文")
    hard_max = 34
    if available_sec is not None and float(available_sec) > 0.5:
        hard_max = max_chars_for_available(float(available_sec))
    if len(pure) < 12:
        reasons.append("旁白过短，不像一句完整介绍")
    if len(pure) > hard_max:
        reasons.append(f"旁白过长（>{hard_max}字），难对齐画面时长")
    for pad in _EMPTY_CLAIM_PADS:
        if pad in body:
            reasons.append(f"空壳营销用语「{pad}」")
            break
    for o in _ORAL:
        if o in body:
            reasons.append(f"网口语「{o}」不入跟镜宣传")
            break
    for m in _MACHINE_ANCHORS:
        if m in body:
            reasons.append(f"机器物名「{m}」不像人话，请换成人话名词或换片")
            break
    if is_caption_inventory(body):
        reasons.append("旁白像画面清点/视觉复述，不是企业宣传介绍")
    if is_flat_diction(body):
        reasons.append("流水账口吻（衣着清点/色块罗列/可见补丁），未达宣传体面")
    if not has_promo_cue(body):
        reasons.append("缺少宣传向表述（到店/护理/在架/服务等），不像企业介绍")
    if visual_description and too_like_visual_description(body, visual_description):
        reasons.append("旁白与画面描述大段重合，禁止复述清点")
    if not has_breath_break(body):
        reasons.append("缺少逗号断句，语气一口气念完")
    if purple_marker_count(body) >= 3:
        reasons.append("雅词堆砌过重（端然/疏朗/入画等），不像人带看")
    if "门楣" in body and ("光影" in body or "次第" in body or "端然" in body):
        reasons.append("门头赋体开场不像宣传导览")
    return list(dict.fromkeys(reasons))


def promo_second_beat(text: str) -> str:
    """宣传句第二拍（逗号后），用于同片卖点去重。"""
    body = normalize_diction_text(text).rstrip("。！？； ")
    if "，" in body:
        return re.sub(r"\s+", "", body.split("，", 1)[1])
    return re.sub(r"\s+", "", body)


def _shared_promo_cores(beats: list[str], *, min_len: int = 5) -> list[str]:
    """跨句第二拍中，长度≥min_len 且出现≥2 次的公共宣传核（取最长互不包含）。"""
    from collections import Counter

    counts: Counter[str] = Counter()
    for beat in beats:
        if len(beat) < min_len:
            continue
        local: set[str] = set()
        upper = min(14, len(beat))
        for n in range(min_len, upper + 1):
            for i in range(len(beat) - n + 1):
                local.add(beat[i : i + n])
        for frag in local:
            counts[frag] += 1
    cands = [s for s, c in counts.items() if c >= 2]
    cands.sort(key=lambda s: (-len(s), s))
    kept: list[str] = []
    for s in cands:
        if any(s != t and s in t for t in kept):
            continue
        kept.append(s)
    return kept


def line_reuses_promo_tail(text: str, used_beats: list[str] | None, *, min_len: int = 5) -> bool:
    """本句第二拍是否与已用收束撞车（含公共核≥min_len）。"""
    beat = promo_second_beat(text)
    if not beat or not used_beats:
        return False
    for prev in used_beats:
        if not prev:
            continue
        if beat == prev:
            return True
        if len(beat) >= min_len and len(prev) >= min_len:
            if beat in prev or prev in beat:
                return True
            shared = _shared_promo_cores([beat, prev], min_len=min_len)
            if shared:
                return True
    return False


def cross_shot_promo_repeat_reasons(texts: list[str], *, min_len: int = 5) -> list[str]:
    """同片多句宣传收束复用 → 中文失败原因。"""
    beats = [promo_second_beat(t) for t in texts if str(t or "").strip()]
    cores = _shared_promo_cores(beats, min_len=min_len)
    if not cores:
        return []
    # 只报最长的 1～2 个，避免噪声
    shown = cores[:2]
    return [
        f"同片宣传收束重复「{c}」（每镜须换卖点角度，禁止句句同一尾句）"
        for c in shown
    ]


def replace_promo_second_beat(text: str, new_tail: str) -> str:
    """保留画面半句，替换宣传第二拍。"""
    body = normalize_diction_text(text).rstrip("。！？； ")
    tail = re.sub(r"[。！？；\s]+$", "", str(new_tail or "").strip())
    if not tail:
        return normalize_diction_text(body)
    if "，" in body:
        head = body.split("，", 1)[0].strip()
        return normalize_diction_text(f"{head}，{tail}")
    return normalize_diction_text(f"{body}，{tail}")


def polish_anchor_join(text: str, anchor: str) -> str:
    """缺锚点时用人话接入，禁止「可见×」。"""
    t = normalize_diction_text(text).rstrip("。，、； ")
    a = str(anchor or "").strip()
    if not is_usable_anchor(a):
        return normalize_diction_text(t)
    if a in t:
        return normalize_diction_text(t)
    return normalize_diction_text(f"{t}，{a}也在眼前")


def has_ornate_signal(text: str) -> bool:
    return purple_marker_count(text) > 0
