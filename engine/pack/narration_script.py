"""Build spoken narration scripts from plan/title (no customer hardcoding)."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any

# Soft spoken pace for Edge 晓晓 @ -8% (chars / sec). Used to size scripts to picture length.
DEFAULT_SPOKEN_CPS_ZH = 3.8

# Product-neutral fallback only. Real body copy should come from industry pack
# ``narration_lines`` + visual hints — never pad duration with stock "现场记录体"
# or camera-direction meta (运镜指导必须由画面完成，不入口播).
_THEME_LINES: dict[str, list[str]] = {
    "default": [
        "服务会按清楚节奏为您安排好。",
        "沟通在前，安排在后，让您更安心。",
        "标准统一，态度一致，体验更完整。",
        "从护理到休息，服务收尾同样周到。",
    ],
}

# Machine / broadcast stock phrases.
STOCK_BANNED_PHRASES: tuple[str, ...] = (
    "这里记录的是当前现场",
    "画面中的细节以实拍为准",
    "镜头按顺序展示现场环节",
    "可以先按画面了解可见信息",
    "本期主题",
    "大家好",
    "欢迎收看",
    "今天给大家带来",
    "现场过程如实记录",
    "规范现场记录",
    "效率看得见",
    "服务更贴心",
    "用着更放心",
    "用着放心",
    # Compliance-slogan openers/closers (not speakable short-video VO)
    "可见的执行细节",
    "可核验的内容",
    "可见工序",
    "可见步骤",
    "照实说明",
    "如实交代",
    "仅介绍当前可见货品与服务",
    "本段不扩展",
    "未展示项不在本段范围内",
    # Warehouse machine / brochure speak (not life-service gold copy)
    "我们展示细节来满足您的需求",
    "陈列、分拣与搬运按顺序说明",
    "按顺序说明",
    "从陈列到搬运逐段看",
    "先看现场实拍",
    "先看可见信息",
    "这组镜头记录了现场",
    "把镜头拉近看看",
    "今天按画面说",
    "仓内陈列整齐清楚",
    # Camera-direction meta (must not be spoken — picture already does the cut)
    "镜头从整体环境切到局部细节",
    "不同镜头对应不同现场环节",
    "可见细节帮助进一步确认需求",
    "跟着当前画面",
    "顺着镜头",
    "跟着镜头",
    "仅描述当前画面",
    "画面外信息",
    "作业画面",
    "镜头跟着看",
    "镜头如实",
    "画面可见",
    "当前画面",
    # Life-service store-intro: avoid awkward / empty padding after arrival
    "安排到店服务",
    "来之前需要怎样安排",
    "来之前需要怎么安排",
    "会提前和您讲清楚",
    "当面把服务与安排讲清楚",
    "沟通清楚、安排合理，让您对每一步都放心",
    "先请听完",
    "请听完您的想法",
)

# Pure shot / edit instructions — VO must never utter these.
CAMERA_DIRECTION_MARKERS: tuple[str, ...] = (
    "镜头从整体环境切到",
    "切到局部细节",
    "不同镜头对应",
    "对应不同现场环节",
    "推镜",
    "拉镜",
    "摇镜",
    "运镜",
    "切换镜头",
    "全景切特写",
    "切到特写",
    "先全景再特写",
    "从整体环境切到",
    "镜头切换",
    "分镜",
    "机位",
    "跟镜",
    "摇到",
    "推到近景",
)

# Layout / prop description — still "describing the shot", not product intro.
VIDEO_META_SPEAK_MARKERS: tuple[str, ...] = (
    "画面主体",
    "视频展示",
    "画面展示",
    "画面中展示",
    "视频中展示",
    "镜头围绕",
    "镜头轻微",
    "轻微移动",
    "背景停放",
    "背景为",
    "背景是",
    "内部呈",
    "内部为",
    "可见中心",
    "所有物体均",
    "静止放置",
    "放置在",
    "侧面写有",
    "盒盖上印有",
    "包装上印有",
    "印有白色",
    "印有蓝色",
    "写有白色",
    "容器内部",
    "圆柱形金属",
    "绿色平面",
    "白色平面",
    "深绿色平面",
    "白色纸面",
    "白色底板",
    "地面上有",
    "杂草",
    "展示了设备",
    "展示了设备的",
    "中心有搅拌",
    "中心搅拌结构",
    "呈橙色",
    "为橙色",
    "为绿色",
    "构图",
    "清晰度",
    "对焦",
    "景深",
    "色调",
    "当前画面",
    "顺着镜头",
    "跟着镜头",
    "作业画面",
    # Spatial/layout caption speak (not commercial use)
    "最上方的",
    "最下方的",
    "从上到下",
    "从下到上",
    "依次递",
    "依次变",
    "尺寸依次",
    "排列在",
    "并排放",
    "手柄上有",
    "有刻字",
    "印有数字",
    "标有",
    "字样",
    "木质桌面",
    "桌面上放置",
    "桌面上摆放",
    "泡沫缓冲",
    "红棕色木",
    "顶部有",
    "底部有",
    "左侧有",
    "右侧有",
    "中间有",
    "白纸上",
    "放置着",
    "摆放着",
    "放置有",
    "摆放有",
    "绿色工具箱内",
    "漏斗状",
    "支撑腿",
    "控制箱",
    "罐体顶部",
    "管内装有",
    "膏状物",
    "镜头停",
    "仓内可看",
    "位置与",
    "侧面可",
    "手写文字",
    "可见白色",
    "可见文字",
)

# Soft questionnaire / paperwork speak → rewrite or drop in VO (all content types).
QUESTIONNAIRE_SPEAK_MARKERS: tuple[str, ...] = (
    "进一步确认需求",
    "进一步确认",
    "请先确认",
    "以实际确认结果为准",
    "以实际确认为准",
    "以咨询为准",
    "信息以当前实拍",
    "先核对品类再确认具体规格",
    "按实际采购需求了解",
    "需要哪类产品可以进一步了解",
)

# Exact component / template → spoken line (None = drop as unspeakable direction).
# Keep generic so every product type can share this map.
_META_COMPONENT_TO_SPOKEN: dict[str, str | None] = {
    "镜头从整体环境切到局部细节": None,
    "不同镜头对应不同现场环节": None,
    "可见细节帮助进一步确认需求": "细节当面看清楚。",
    "信息以当前实拍和实际确认结果为准": "货以当面看的为准。",
    "先核对品类再确认具体规格": "先看清品类，型号再当面定。",
    "按实际采购需求了解对应品类": "按采购需要挑对应品类。",
    "具体型号规格与价格请先确认": "型号规格价格请当面沟通。",
    "需要哪类产品可以进一步了解": "需要哪类产品可以跟我们说。",
    "库存和交付安排以实际确认结果为准": "库存与交付当面沟通。",
    "按使用需求咨询对应品类": "按工地需要咨询对应品类。",
    "画面可见产品外观与包装": "外观规格当面看清楚。",
    "镜头记录了仓内陈列": "仓里货码得整齐。",
    "仓里货码得整齐": "仓里货码得整齐。",
    "画面展示装车与搬运过程": "装车搬运这一段清楚。",
    "装车搬运这一段清楚": "装车搬运这一段清楚。",
    "现场可见多类货品整齐摆放": "多类货摆得整齐。",
    "多类货摆得整齐": "多类货摆得整齐。",
    "人物正在进行画面可见的操作": "现场操作步骤清楚。",
    "按画面顺序看陈列分拣与搬运": "拣货码货再装车。",
    "拣货码货再装车": "拣货码货再装车。",
    "从外观包装再看到现场操作": None,  # product: do not speak meta process
    "这段记录的是实际作业过程": None,
    "镜头跟着可见动作往下走": "跟着现场一步步过。",
    "可见过程按顺序展开": "备货装车连着做。",
    "细节都在当前画面里": "细节当面看清楚。",
    "一步跟着一步看清楚": "一步一步看清楚。",
    # Legacy machine openers → human warehouse
    "先看现场实拍": "今天仓里过一遍货。",
    "从陈列到搬运逐段看": "跟着装车走一趟。",
    "先看可见信息": "先看这几样常用的。",
    "这组镜头记录了现场": "仓里这批备货。",
    "把镜头拉近看看": "这批货先过一遍。",
    "今天按画面说": "当面看货再定。",
    "从画面认识这批货": "工地常用几样。",
}

_CAMERA_DIR_RE = re.compile(
    r"(镜头从.+切到|不同镜头对应|切到局部|切到特写|切换镜头|"
    r"推镜|拉镜|摇镜|运镜|分镜|机位|先全景再特写|全景切特写)"
)
_QUESTIONNAIRE_RE = re.compile(
    r"(进一步确认(需求)?|请先确认|以实际确认|以咨询为准|"
    r"帮助进一步|方便您进一步|再做确认)"
)
_VIDEO_META_RE = re.compile(
    r"(画面主体|视频展示了?|画面展示了?|镜头围绕|背景停放|背景为|背景是|"
    r"内部呈|内部为|容器内部|所有物体|静止放置|侧面写有|盒盖上印有|"
    r"白色纸面|白色平面|绿色平面|深绿色|白色底板|镜头轻微|轻微移动|"
    r"放置在户外|放置在白色|中心搅拌结构|可见中心|地面上有杂草|"
    r"色调|景深|对焦|构图|清晰度|"
    r"最上方|最下方|从上到下|依次递|依次变|手柄上有|有刻字|"
    r"木质桌面|桌面上(放置|摆放)|排列在绿|排列在白|"
    # Spatial / appearance caption speak (describe-the-shot, not product intro)
    r"(顶部|底部|左侧|右侧|中间|后方|上方|下方)有|"
    r"位于(上|下|左|右|中)|"
    r"白纸上|绿(色)?背景|背景上|放置着|摆放着|放置三|"
    r"深灰|浅灰|红棕色|漏斗状|支撑腿|控制箱|罐体|"
    r"圆柱形|锥形|管内装有|膏状物|镜头停|"
    r"位置与|仓内可看|看得见的地方|工作位一致|"
    # Caption inventory speak (clothing / pile / clarity checklist)
    r"身穿|胸前有|堆放着|停放着|清晰可见|孔洞清晰|"
    r"背景中|背景光线|金属立柱|木质托盘上)"
)

# Performative fillers that sound scripted on Edge TTS (Phase B · user ban + peers).
ORAL_FILLER_BANS: tuple[str, ...] = (
    "你看",
    "真的",
    "就是这样",
    "赶紧看",
    "赶紧",
    "看着就踏实",
    "看着踏实",
    "踏实了",
    "你瞧",
    "你看啊",
    "你看这",
    "来看",
    "快看",
    "赶紧来",
    "赶紧来看",
    "注意看",
    "仔细看",
    "往这儿看",
    "往这里看",
    "真是",
    "真挺",
    "真心",
    "真不错",
    "就这样",
    "就这么",
    "就这么干",
    "就这么简单",
    "咱们的",
    "咱这",
    "咱就",
    "咱们这",
    "哇塞",
    "厉害了",
    "绝了",
    "绝绝",
    "冲了",
    "必须冲",
    "显得踏实",
    "看着就放心",
    "放心吧",
)

# Shipping/logistics claims — forbidden when visuals are product-tabletop only.
SHIPPING_CLAIM_TERMS: tuple[str, ...] = (
    "装车",
    "发货",
    "出库",
    "卸货",
    "上车",
    "装货",
    "送货",
    "车队",
    "出车",
    "发车",
    "物流",
    "配送上门",
    "当天发",
    "马上发",
)

# Product VO must not narrate packaging / print metadata (user: commercial showcase only).
PRODUCT_PACKAGING_BANS: tuple[str, ...] = (
    "包装盒",
    "包装",
    "盒内",
    "盒盖",
    "纸盒",
    "纸箱",
    "字样",
    "印有",
    "印刷",
    "标签",
    "贴纸",
    "贴牌",
    "外盒",
    "封箱",
    "正面朝上",
    "盒装",
    "塑封",
    "白色平面",
    "绿色平面",
    "木质桌面",
    "桌面上",
    "平面上",
)

# Tokens too broad — only use when no more-specific product token hits the same hint.
_PRODUCT_GENERIC_TOKENS: frozenset[str] = frozenset({"工具", "五金"})

# token → short commercial use line (generic industry, no guarantees).
_PRODUCT_USE_BY_TOKEN: tuple[tuple[str, str], ...] = (
    ("尖嘴钳", "尖嘴钳，精细夹持好使。"),
    ("斜口钳", "斜口钳，剪切修边常用。"),
    ("钢丝钳", "钢丝钳，剪切夹持耐用。"),
    ("精品钳", "精品钳系列，剪夹日常更顺手。"),
    ("钳子", "钳类工具，剪切夹持常用。"),
    ("钳", "钳类工具，剪切夹持常用。"),
    ("扳手", "扳手类，紧固拆装好搭档。"),
    ("套筒头", "套筒头，拧紧拆装配套件。"),
    ("套筒", "套筒系列，拧紧拆卸更省力。"),
    ("延长杆", "套筒延长杆，加长拧固更顺手。"),
    ("接杆", "套筒接杆，加长拧固更顺手。"),
    ("水龙头", "水龙头五金，卫浴装配常用。"),
    ("水龙", "水龙头五金，卫浴装配常用。"),
    ("工具箱", "工具箱套装，收纳携带更齐整。"),
    ("电钻", "电动工具，钻孔装配更利索。"),
    ("电动", "电动工具，钻孔拧固更利索。"),
    ("充电器", "充电器，现场补电续航用得上。"),
    ("充电", "充电套装，续航补电更省心。"),
    ("电池", "配套电池，续航补电用得上。"),
    ("钻头", "金属钻头，钻孔开孔常用。"),
    ("批头", "批头耗材，拧固开孔常用。"),
    ("起子", "起子批头，装配拧紧常用。"),
    ("卷尺", "钢卷尺，量尺划线方便带。"),
    ("长城精工", "精工卷尺系列，量尺现场好用。"),
    ("焊条", "焊接耗材，现场施焊好配备。"),
    ("焊丝", "焊丝耗材，连续焊接更顺。"),
    ("砂轮", "砂轮片，打磨切断少不了。"),
    ("切割", "切割耗材，开料断料用得上。"),
    ("手套", "劳防手套，搬运操作保手。"),
    ("电缆", "电缆线材，现场布线常用。"),
    # Facility-scale equipment is not tabletop product VO (avoid 装车/场地混口播)
    ("螺丝", "紧固件，装配固定少不了。"),
    ("五金", "五金工具，工地门店都用得上。"),
    # Broad “工具” last; skipped when any concrete SKU also matches
    ("工具", "实用工具，日常作业用得上。"),
)

_LOADING_THEME_KEYS = (
    "配送",
    "仓配",
    "装车",
    "发货",
    "物流",
    "出库",
    "service",
    "wholesale",
    "批发",
    "工地",
)
_PRODUCT_THEME_KEYS = ("产品", "五金", "特写", "单品", "耗材")
_PRODUCT_VISUAL_MARKERS = (
    "product_closeup",
    "产品特写",
    "桌面",
    "白纸",
    "包装盒",
    "盒内",
    "静置",
    "工具箱",
    "卷尺",
    "扳手",
    "木质桌面",
    "白色平面",
    "绿色平面",
    "特写",
)
_OPS_VISUAL_MARKERS = (
    "装车",
    "卸货",
    "货车",
    "仓库",
    "货架",
    "warehouse",
    "loading",
    "车斗",
    "分拣",
    "码垛",
    "工人",
    "卡车",
)

_INTERJECTIONS = ("嗨", "呀", "哦", "嗯", "呐", "啦", "嘿", "哈", "哇")
_TECH = ("参数", "规格", "立方", "型号参数", "技术指标")
_TONE_OPENERS: dict[str, tuple[str, ...]] = {
    "plain": (
        "跟着{brand}现场这一段说清楚。",
        "先从{brand}眼前看得见的部分讲。",
        "把{brand}过程一步步过一遍。",
        "这一段只讲{brand}现场在做什么。",
        "从近处看清{brand}当前在做什么。",
        "走到哪一步，就说到{brand}哪一步。",
    ),
    "warm": (
        "来到{brand}，先感受我们怎样为您服务。",
        "欢迎来到{brand}，先感受我们怎样为您服务。",
        "来到{brand}，我们的服务包您满意。",
        "来到{brand}，我们把服务认认真真跟您说清楚。",
        "欢迎来到{brand}，服务会从沟通开始一步步做好。",
        "来到{brand}，我们希望您始终安心、被尊重。",
    ),
    "energetic": (
        "直接到{brand}作业位。",
        "直接看{brand}这一段怎么做。",
        "把{brand}过程紧一点讲完。",
        "节奏紧一点，先看{brand}关键一步。",
        "进到{brand}现场继续说。",
        "先把{brand}眼前这一段讲清楚。",
    ),
    "passionate": (
        "把{brand}现场过一遍。",
        "来，看{brand}怎么作业。",
        "{brand}这一幕过程摊开讲。",
        "贴近{brand}，一步步说清楚。",
        "这一段聚焦{brand}眼前动作。",
        "从入口把{brand}讲明白。",
    ),
    "professional": (
        "下面把{brand}眼前这一段过一遍。",
        "先讲清{brand}当前在做什么。",
        "把{brand}现场环节过一遍。",
        "跟着{brand}这一段作业往下说。",
        "这一段聚焦{brand}现场动作。",
        "从近处把{brand}过程讲清楚。",
    ),
}
_TONE_CLOSERS: dict[str, tuple[str, ...]] = {
    "plain": (
        "具体品类再按需确认。",
        "以上只以当前实拍为准。",
        "需要哪一类再往下细看。",
        "未展示项请另行核对。",
    ),
    "warm": (
        "您有时间随时可以来店里体验详聊。",
        "欢迎抽空来店感受一次完整服务。",
        "想试一试的话，到店详聊会更清楚。",
        "约个时间来店，我们当面为您讲明白。",
    ),
    "energetic": (
        "更多细目按实单再核。",
        "看完这一段再接着选。",
        "品类细节随后再对清楚。",
        "要哪一步再往下沟通。",
    ),
    "passionate": (
        "过程先说到这儿。",
        "细节清楚了再对接。",
        "需要哪一类再接着说。",
        "要点讲完，需求再议。",
    ),
    "professional": (
        "其余细节再按需补充。",
        "这一段先说到眼前可见部分。",
        "细节以实拍与当面确认为准。",
        "未展示的部分再另外沟通。",
    ),
}


def normalize_spoken_key(text: str, *, max_chars: int = 12) -> str:
    """Stable key for opener de-dupe (strip punct/space, keep leading han)."""
    compact = re.sub(r"\s+", "", str(text or ""))
    compact = re.sub(r"[，,。．！？!?、；;：:…·\-—_「」\"'（）()【】\[\]]", "", compact)
    return compact[: max(4, int(max_chars))]


def first_sentence(script: str) -> str:
    raw = re.sub(r"\s+", "", str(script or "").strip())
    if not raw:
        return ""
    for sep in ("。", "！", "？", ".", "!", "?"):
        if sep in raw:
            return raw.split(sep, 1)[0].strip()
    return raw[:18]


def opener_key(script_or_opener: str, *, max_chars: int = 12) -> str:
    return normalize_spoken_key(first_sentence(script_or_opener), max_chars=max_chars)


def script_has_oral_filler(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    return any(p and p in compact for p in ORAL_FILLER_BANS)


def script_has_camera_direction(text: str) -> bool:
    """True if the line is shot/edit direction, not speakable ground truth."""
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if any(m and m in compact for m in CAMERA_DIRECTION_MARKERS):
        return True
    return bool(_CAMERA_DIR_RE.search(compact))


def script_has_questionnaire_speak(text: str) -> bool:
    """True if the line is paperwork / questionnaire tone unfit for short video VO."""
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if any(m and m in compact for m in QUESTIONNAIRE_SPEAK_MARKERS):
        return True
    return bool(_QUESTIONNAIRE_RE.search(compact))


# Product VO: sentence must look like use/sales copy, not visual inventory.
_PRODUCT_COMMERCIAL_OK_RE = re.compile(
    r"(常用|好使|省力|省心|搭档|到店|选配|品类|系列|投料|混料|搅拌|"
    r"钻孔|紧固|量尺|划线|夹持|剪切|续航|补电|护手|转运|"
    r"配件|在售|好物|五金|跟我们|当面|按需|装配|携带|"
    r"工地|门店|上架|速递|速览|挑选|配齐|拿货|工具|设备|耗材|"
    r"满足您的需求|展示细节|实用|耐用|顺手|好带|好用|"
    r"卫浴|收纳|拧固|开孔)"
)


def product_sentence_is_commercial(text: str) -> bool:
    """True if a product VO sentence is use/sales speak (not shot description)."""
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact or script_has_video_meta_speak(compact) or script_has_packaging_meta(compact):
        return False
    if script_has_camera_direction(compact) or script_has_shipping_claim(compact):
        return False
    if any(
        k in compact
        for k in (
            "实际作业",
            "人员操作",
            "仓内可看",
            "分拣与搬运",
            "陈列",
            "红色设备",
            "设备外壳",
            "外壳颜色",
            "从外观看到",
            "现场操作",
            "照实说明",
            "可见细节",
            "镜头停",
        )
    ):
        return False
    return bool(_PRODUCT_COMMERCIAL_OK_RE.search(compact))


def filter_product_commercial_script(script: str) -> str:
    """Keep only commercial/use sentences for product reels."""
    kept: list[str] = []
    for part in re.split(r"(?<=[。！？!?])\s*", str(script or "")):
        p = part.strip()
        if not p:
            continue
        body = p if p.endswith(("。", "！", "？", "!", "?")) else p + "。"
        if product_sentence_is_commercial(body):
            kept.append(body)
    return "".join(kept)


def script_has_video_meta_speak(text: str) -> bool:
    """True when line describes the shot/composition as if captioning a video.

    Product/service VO introduces goods or on-site service — never 'the frame shows…'.
    """
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if any(m and m in compact for m in VIDEO_META_SPEAK_MARKERS):
        return True
    return bool(_VIDEO_META_RE.search(compact))


def scrub_video_meta_sentences(script: str) -> str:
    """Drop speech sentences that are pure video/composition narration."""
    raw = (script or "").strip()
    if not raw:
        return ""
    kept: list[str] = []
    for part in re.split(r"(?<=[。！？!?])\s*", raw):
        p = part.strip()
        if not p:
            continue
        if script_has_video_meta_speak(p) or script_has_camera_direction(p):
            # Try product salvage from the same blob
            rescue = commercial_product_lines_from_hints([p], limit=1)
            if rescue:
                kept.append(rescue[0])
            continue
        kept.append(p if p.endswith(("。", "！", "？", "!", "?")) else p + "。")
    return "".join(kept)


def to_spoken_line(text: str) -> str | None:
    """Map keyword-pack / recipe meta into speakable VO, or drop unspeakable lines.

    Universal for all selected video types (product / loading / overview / …):
    - Camera / edit directions never enter speech (picture already cuts).
    - Questionnaire soft-confirm is rewritten to natural service lines.
    - Returns None when the line must not be spoken at all.
    """
    raw = re.sub(r"\s+", "", str(text or "").strip())
    if not raw:
        return None
    if raw in _META_COMPONENT_TO_SPOKEN:
        mapped = _META_COMPONENT_TO_SPOKEN[raw]
        if mapped is None:
            return None
        return mapped if mapped.endswith(("。", "！", "？")) else mapped + "。"
    # Partial matches for long components
    for key, mapped in _META_COMPONENT_TO_SPOKEN.items():
        if key and key in raw:
            if mapped is None:
                return None
            return mapped if mapped.endswith(("。", "！", "？")) else mapped + "。"
    if script_has_camera_direction(raw):
        return None
    # Vision caption / composition talk → salvage product use only.
    # Multi-clause service VO (预约 / 倾听 / 到店…) may exceed a short-line cap — keep it.
    if script_has_video_meta_speak(raw):
        commercial = commercial_product_lines_from_hints([str(text)], limit=2)
        return commercial[0] if commercial else None
    _service_markers = (
        "您",
        "我们",
        "到店",
        "来店",
        "服务",
        "预约",
        "护理",
        "沟通",
        "安排",
        "欢迎",
    )
    is_service_vo = any(m in raw for m in _service_markers)
    if len(raw) > 36 and not is_service_vo:
        commercial = commercial_product_lines_from_hints([str(text)], limit=2)
        return commercial[0] if commercial else None
    if len(raw) > 96:
        # Extreme free-text — refuse to dump full captions into VO
        commercial = commercial_product_lines_from_hints([str(text)], limit=2)
        return commercial[0] if commercial else None
    if script_has_questionnaire_speak(raw):
        # Mode-neutral: warehouse-friendly, not brochure / service-filler
        return "细节当面看清楚。"
    body = str(text or "").strip()
    # Short service/use claims may pass; never keep pure description without period
    if not body.endswith(("。", "！", "？")):
        body = body + "。"
    return body


def filter_grounding_for_speech(lines: list[str] | None) -> list[str]:
    """Convert recipe/allowed-fact strings into speakable body lines (drop directions)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in lines or []:
        spoken = to_spoken_line(str(raw))
        if not spoken:
            continue
        key = re.sub(r"\s+", "", spoken)
        if key in seen:
            continue
        if script_has_camera_direction(spoken) or script_has_stock_ban(spoken):
            continue
        seen.add(key)
        out.append(spoken)
    return out


def script_has_shipping_claim(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    return any(p and p in compact for p in SHIPPING_CLAIM_TERMS)


def script_has_packaging_meta(text: str) -> bool:
    """True if VO is describing packaging / print / surface props (not product use)."""
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    return any(p and p in compact for p in PRODUCT_PACKAGING_BANS)


def commercial_product_lines_from_hints(
    hints: list[str] | None,
    *,
    limit: int = 4,
) -> list[str]:
    """Turn product-ish vision text into short commercial use lines (no packaging).

    Order follows first appearance of tokens in the hint text (timeline / left-to-right).
    Broad tokens (工具/五金) never beat a concrete SKU on the same clip.
    """
    out: list[str] = []
    seen: set[str] = set()
    hint_list = [str(h).strip() for h in (hints or []) if str(h).strip()]
    if not hint_list:
        return []
    for blob in hint_list:
        hits: list[tuple[int, int, str, str]] = []
        for token, line in _PRODUCT_USE_BY_TOKEN:
            pos = blob.find(token)
            if pos < 0:
                continue
            # Prefer longer tokens at same locus (套筒头 > 套筒 > 筒)
            spoken = line if line.endswith(("。", "！", "？")) else line + "。"
            hits.append((pos, -len(token), spoken, token))
        hits.sort(key=lambda x: (x[0], x[1]))
        concrete = [h for h in hits if h[3] not in _PRODUCT_GENERIC_TOKENS]
        ranked = concrete if concrete else hits
        for _, _, spoken, _token in ranked:
            if spoken in seen:
                continue
            seen.add(spoken)
            out.append(spoken)
            if len(out) >= limit:
                return out
    return out


def product_has_commercial_hook(description: str | None) -> bool:
    """True when a clip description maps to at least one sellable product line."""
    return bool(commercial_product_lines_from_hints([str(description or "")], limit=1))


# Related SKUs share a family so later cuts of the same class stay silent
# (exact line de-dupe alone is not enough: 精工卷尺 vs 钢卷尺).
_TOKEN_PRODUCT_FAMILY: dict[str, str] = {
    "尖嘴钳": "pliers",
    "斜口钳": "pliers",
    "钢丝钳": "pliers",
    "精品钳": "pliers",
    "钳子": "pliers",
    "钳": "pliers",
    "扳手": "wrench",
    "套筒头": "socket",
    "套筒": "socket",
    "延长杆": "socket",
    "接杆": "socket",
    "水龙头": "faucet",
    "水龙": "faucet",
    "工具箱": "toolbox",
    "电钻": "power_tool",
    "电动": "power_tool",
    "充电器": "power_tool",
    "充电": "power_tool",
    "电池": "power_tool",
    "钻头": "bit",
    "批头": "bit",
    "起子": "bit",
    "卷尺": "tape",
    "长城精工": "tape",
    "焊条": "weld",
    "焊丝": "weld",
    "砂轮": "abrasive",
    "切割": "abrasive",
    "手套": "ppe",
    "电缆": "cable",
    "螺丝": "fastener",
}


def _normalize_spoken_line(text: str) -> str:
    compact = re.sub(r"\s+", "", str(text or ""))
    return re.sub(r"[。！？.!?,，、]+$", "", compact)


def _product_families_in_text(blob: str) -> list[str]:
    """Product families for a clip description / spoken line (appearance order)."""
    text = str(blob or "")
    if not text:
        return []
    hits: list[tuple[int, int, str]] = []
    for token, family in _TOKEN_PRODUCT_FAMILY.items():
        if token in _PRODUCT_GENERIC_TOKENS:
            continue
        pos = text.find(token)
        if pos < 0:
            continue
        hits.append((pos, -len(token), family))
    hits.sort(key=lambda x: (x[0], x[1]))
    out: list[str] = []
    seen: set[str] = set()
    for _, _, fam in hits:
        if fam in seen:
            continue
        seen.add(fam)
        out.append(fam)
    return out


def _filter_fresh_sku_lines(
    sku_lines: list[str],
    *,
    used_spoken: set[str],
    used_families: set[str],
) -> list[str]:
    """Drop exact repeats and same-class SKU VO already spoken earlier in the plan."""
    fresh: list[str] = []
    for ln in sku_lines:
        key = _normalize_spoken_line(ln)
        if not key or key in used_spoken:
            continue
        families = _product_families_in_text(ln)
        if families and all(f in used_families for f in families):
            continue
        if families and any(f in used_families for f in families):
            # Partial overlap with a already-voiced class → skip (same-class rehash)
            continue
        fresh.append(ln if ln.endswith(("。", "！", "？")) else ln + "。")
    return fresh


def _mark_spoken_sku_lines(
    lines: list[str],
    *,
    used_spoken: set[str],
    used_families: set[str],
) -> None:
    for ln in lines:
        key = _normalize_spoken_line(ln)
        if key:
            used_spoken.add(key)
        for fam in _product_families_in_text(ln):
            used_families.add(fam)


def product_slot_scripts_from_plan(
    plan_or_clips: Any,
    *,
    brand: str = "本店",
    cps: float = 3.2,
    variation_seed: int | None = None,
) -> list[dict[str, Any]]:
    """One VO block per slot, sized for that slot's duration — no SKUs from later/earlier cuts.

    Same product class only spoken once across the plan; later matching cuts stay silent
    (TTS pads the slot). Empty text is intentional and preferred over repeating lines.

    Returns list of {slot, duration_sec, text} in timeline order.
    """
    clips = getattr(plan_or_clips, "clips", None) or plan_or_clips or []
    brand_short = _short_brand(brand)
    rng = random.Random(int(variation_seed or 0) or 0)
    openers = [
        f"本期{brand_short}带来一组在售好物。",
        f"下面速览{brand_short}在售品类。",
        f"{brand_short}热销工具与耗材速览。",
        f"先看{brand_short}这几样在售货。",
    ]
    closers = [
        "看中的型号到店直接拿。",
        "缺哪样到店跟我们配。",
        "工地补料可到店配齐。",
    ]
    soft_no_sku = [
        "这一镜在架货，到店可对照。",
        "款式细节当面看清楚。",
        "到店按现场货架挑选。",
    ]
    opener = rng.choice(openers)
    closer = rng.choice(closers)
    clip_list = list(clips)
    n = len(clip_list)
    used_spoken: set[str] = set()
    used_families: set[str] = set()
    out: list[dict[str, Any]] = []
    for i, c in enumerate(clip_list):
        if hasattr(c, "description"):
            desc = str(getattr(c, "description", "") or "")
            slot = str(getattr(c, "slot", "") or f"s{i}")
            dur = float(getattr(c, "duration_sec", 0) or 0) or 4.0
        elif isinstance(c, dict):
            desc = str(c.get("description") or "")
            slot = str(c.get("slot") or f"s{i}")
            dur = float(c.get("duration_sec") or 0) or 4.0
        else:
            continue
        # ~0.90 of slot so speech ends before the cut (pad fills the rest)
        budget = max(16, int(float(dur) * float(cps or 3.2) * 0.90))
        sku_raw = commercial_product_lines_from_hints([desc], limit=2)
        # Short slots: 1 SKU max so VO does not spill into the next cut
        if dur <= 4.5 and sku_raw:
            sku_raw = sku_raw[:1]
        sku_lines = _filter_fresh_sku_lines(
            sku_raw,
            used_spoken=used_spoken,
            used_families=used_families,
        )
        clip_families = _product_families_in_text(desc)
        same_class_already = bool(clip_families) and all(f in used_families for f in clip_families)
        # Description only hits already-voiced families (no new line text) — stay silent
        if not sku_lines and clip_families and any(f in used_families for f in clip_families):
            same_class_already = True

        parts: list[str] = []
        if i == 0:
            parts.append(opener if opener.endswith(("。", "！", "？")) else opener + "。")
        if sku_lines:
            parts.extend(sku_lines)
        elif not same_class_already:
            # Unknown / no SKU: light soft line once. Never re-announce known classes.
            soft = soft_no_sku[i % len(soft_no_sku)]
            soft_norm = _normalize_spoken_line(soft)
            if soft_norm not in used_spoken:
                parts.append(soft if soft.endswith(("。", "！", "？")) else soft + "。")
                used_spoken.add(soft_norm)
        # else: same-class repeat → intentional silence (empty parts ok)
        if i == n - 1 and n >= 1:
            parts.append(closer if closer.endswith(("。", "！", "？")) else closer + "。")
        # Fit budget, keep SKU preferenced over opener if overflow on short hook
        kept: list[str] = []
        han = 0

        def _han_len(s: str) -> int:
            return len(re.sub(r"[^\u4e00-\u9fff]", "", s))

        for p in parts:
            need = _han_len(p)
            if kept and han + need > budget:
                continue
            kept.append(p)
            han += need
        if not kept and sku_lines:
            kept = [sku_lines[0]]
        # Prefer product line if opener alone ate budget with no SKU
        if sku_lines and not any(
            _normalize_spoken_line(sku_lines[0])[:4] in _normalize_spoken_line(x) for x in kept
        ):
            kept = [sku_lines[0]] + ([closer] if i == n - 1 else [])
        # Silence pad is OK — do not force filler onto same-class repeats
        text = "".join(kept)
        # Only mark families/lines actually kept in this slot's VO
        spoken_here = [
            s
            for s in sku_lines
            if any(
                _normalize_spoken_line(s) == _normalize_spoken_line(k)
                or _normalize_spoken_line(s) in _normalize_spoken_line(k)
                for k in kept
            )
        ]
        if spoken_here:
            _mark_spoken_sku_lines(
                spoken_here,
                used_spoken=used_spoken,
                used_families=used_families,
            )
        out.append(
            {
                "slot": slot,
                "duration_sec": round(dur, 3),
                "text": text,
                "silent": not bool(re.sub(r"[。！？\s]", "", text)),
            }
        )
    return out


def script_from_product_slots(slots: list[dict[str, Any]] | None) -> str:
    return "".join(str(s.get("text") or "") for s in (slots or []) if str(s.get("text") or "").strip())


def script_has_stock_ban(text: str) -> bool:
    """Any hard ban: stock broadcast, oral fillers, camera direction, video-meta speak."""
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact:
        return False
    if any(p and p in compact for p in STOCK_BANNED_PHRASES):
        return True
    if script_has_camera_direction(compact):
        return True
    if script_has_video_meta_speak(compact):
        return True
    if script_has_oral_filler(compact):
        return True
    return False


def is_loading_ops_theme(theme: str | None) -> bool:
    t = (theme or "").strip().lower()
    if not t:
        return False
    for key in _LOADING_THEME_KEYS:
        if key.lower() in t or t in key.lower():
            return True
    return False


def is_product_theme(theme: str | None) -> bool:
    t = (theme or "").strip()
    if not t:
        return False
    return any(k in t for k in _PRODUCT_THEME_KEYS)


def visual_context_flags(
    clip_hints: list[str] | None = None,
    *,
    scene_tags: list[str] | None = None,
    theme: str | None = None,
) -> dict[str, bool]:
    """Classify whether VO should be product-display or ops/loading."""
    blob_parts = [str(x) for x in (clip_hints or []) if str(x).strip()]
    blob_parts.extend(str(x) for x in (scene_tags or []) if str(x).strip())
    blob = " ".join(blob_parts).lower()
    product_hits = sum(1 for m in _PRODUCT_VISUAL_MARKERS if m.lower() in blob)
    ops_hits = sum(1 for m in _OPS_VISUAL_MARKERS if m.lower() in blob)
    loading_theme = is_loading_ops_theme(theme)
    product_theme = is_product_theme(theme)
    # Majority product markers without ops visuals => product tabletop mode
    product_display = product_hits >= 2 and ops_hits == 0
    if product_theme and ops_hits == 0:
        product_display = True
    if loading_theme and ops_hits >= 1:
        product_display = False
    loading_ops = loading_theme or ops_hits >= 2
    return {
        "product_display": bool(product_display and not loading_ops),
        "loading_ops": bool(loading_ops),
        "product_hits": product_hits,
        "ops_hits": ops_hits,
    }


def resolve_delivery_tone(
    tone: str,
    *,
    theme: str | None = None,
    product_display: bool = False,
    loading_ops: bool = False,
) -> str:
    """Map production-rule tone to opener/closer banks.

    Explicit ``warm`` / friendly tones are honored (life-service store intros).
    Product default stays ``professional`` only when tone is plain/professional.
    Loading may keep warm colloquial process VO; no oral-filler path.
    """
    _ = theme
    t = (tone or "plain").strip() or "plain"
    if t not in _TONE_OPENERS:
        t = "plain"
    if product_display:
        # Production rules may set warm for brand/store feel — do not crush to professional
        if t in ("warm", "energetic", "passionate"):
            return "warm"
        return "professional"
    if loading_ops:
        if t in ("plain", "professional"):
            return t  # keep formal if already
        if t in ("passionate", "energetic"):
            return "warm"  # slightly colloquial, process-forward
        return t
    # default: keep warm; map energetic/passionate → warm (not plain/professional slogans)
    if t in ("passionate", "energetic"):
        return "warm"
    return t


def collect_scene_tags(plan_or_clips: Any, *, limit: int = 12) -> list[str]:
    clips = getattr(plan_or_clips, "clips", None) or plan_or_clips or []
    out: list[str] = []
    for c in clips:
        vals: list[str] = []
        if hasattr(c, "scene"):
            vals.append(str(getattr(c, "scene", "") or ""))
        if isinstance(c, dict):
            vals.append(str(c.get("scene") or ""))
            objs = c.get("objects") or c.get("object")
            if isinstance(objs, list):
                vals.extend(str(x) for x in objs[:4])
            elif objs:
                vals.append(str(objs))
        for v in vals:
            v = (v or "").strip()
            if v and v not in out:
                out.append(v)
        if len(out) >= limit:
            break
    return out


def recent_narration_openers(
    session: Any,
    customer_id: int | None,
    *,
    limit_outputs: int = 12,
) -> list[str]:
    """Load first-sentence openers from recent customer sidecars (fail-open)."""
    if customer_id is None or session is None:
        return []
    try:
        from sqlalchemy import select

        from engine.catalog.db import Job, RenderOutput

        rows = list(
            session.scalars(
                select(RenderOutput)
                .join(Job, RenderOutput.job_id == Job.id)
                .where(Job.customer_id == int(customer_id))
                .order_by(RenderOutput.id.desc())
                .limit(max(1, int(limit_outputs)))
            ).all()
        )
    except Exception:  # noqa: BLE001
        return []

    openers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        script = ""
        side = str(getattr(row, "sidecar_path", "") or "").strip()
        if side:
            try:
                data = json.loads(Path(side).read_text(encoding="utf-8"))
                meta = data.get("meta") if isinstance(data, dict) else None
                if isinstance(meta, dict):
                    script = str(meta.get("narration_script") or "")
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                script = ""
        if not script and isinstance(getattr(row, "qc_json", None), dict):
            script = str((row.qc_json or {}).get("narration_script") or "")
        if not script:
            continue
        opener = first_sentence(script)
        key = opener_key(opener)
        if not key or key in seen:
            continue
        seen.add(key)
        openers.append(opener)
    return openers


def title_content_fragments(title: str) -> list[str]:
    """On-screen title pieces that must never appear in VO / captions."""
    raw = (title or "").replace("\r\n", "\n").replace("\r", "\n").replace("｜", "\n").strip()
    if not raw:
        return []
    frags: list[str] = []
    for part in raw.split("\n"):
        t = re.sub(r"\s+", "", part.strip())
        if len(t) >= 4:
            frags.append(t)
    compact = re.sub(r"\s+", "", raw.replace("\n", ""))
    if len(compact) >= 4 and compact not in frags:
        frags.append(compact)
    # Longest first so replacements don't leave leftovers
    return sorted(set(frags), key=len, reverse=True)


def scrub_title_from_spoken(text: str, title: str) -> str:
    """Remove on-screen title wording from spoken / caption text."""
    out = text or ""
    for frag in title_content_fragments(title):
        if frag and frag in out:
            out = out.replace(frag, "")
    # Clean doubled punctuation / empty clauses left behind
    out = re.sub(r"[，,]{2,}", "，", out)
    out = re.sub(r"[。．]{2,}", "。", out)
    out = re.sub(r"，([。！？])", r"\1", out)
    out = re.sub(r"(我们帮您打理好[。．]?)", "", out)  # orphaned title-tail phrase
    return out.strip(" ，,。．")


def assert_spoken_not_raw_vision(
    script: str,
    visual_hints: list[str] | None,
    *,
    source: str = "base_script",
) -> None:
    """Fail closed when spoken text is a raw long vision caption (not hooks/词池).

    Allowed when Ollama is off: industry pack / hooks / recipe contracts.
    Forbidden: shipping an unmodified long clip description as voiceover.
    """
    spoken = re.sub(r"\s+", "", (script or "").strip())
    if len(spoken) < 24:
        return
    for hint in visual_hints or []:
        raw = re.sub(r"\s+", "", str(hint or "").strip())
        if len(raw) < 24:
            continue
        if spoken == raw or (len(raw) >= 40 and raw in spoken and len(spoken) - len(raw) < 8):
            raise ValueError(
                f"旁白底稿禁止直接使用生画面描述（{source}）；请走词池/hooks 合同面"
            )


def text_contains_title(text: str, title: str) -> bool:
    compact = re.sub(r"\s+", "", (text or "").replace("\n", ""))
    if not compact:
        return False
    for frag in title_content_fragments(title):
        if frag and frag in compact:
            return True
    return False


def _ensure_period(sentence: str) -> str:
    s = (sentence or "").strip()
    if not s:
        return s
    if not s.endswith(("。", "！", "？", ".", "!", "?")):
        return s + "。"
    return s


def _pick_opener(
    tone_key: str,
    brand_short: str,
    rng: random.Random,
    exclude_keys: set[str],
) -> str:
    pool = [
        tpl.format(brand=brand_short)
        for tpl in _TONE_OPENERS.get(tone_key, _TONE_OPENERS["plain"])
    ]
    allowed = [o for o in pool if opener_key(o) not in exclude_keys and not script_has_stock_ban(o)]
    if not allowed:
        allowed = [o for o in pool if not script_has_stock_ban(o)] or list(pool)
    return rng.choice(allowed)


def theme_body_lines(
    theme: str,
    pack_lines: list[str] | None = None,
) -> list[str]:
    """Prefer industry pack lines; fall back to tiny neutral bank."""
    out: list[str] = []
    seen: set[str] = set()
    for ln in list(pack_lines or []) + _theme_bank(theme):
        s = _ensure_period(str(ln).strip())
        if not s or s in seen or script_has_stock_ban(s) or not _ok_line(s):
            continue
        seen.add(s)
        out.append(s)
    return out


def narration_script_zh(
    title: str,
    *,
    brand: str = "品牌",
    theme: str = "default",
    description: str = "",
    clip_hints: list[str] | None = None,
    target_duration_sec: float | None = None,
    cps: float = DEFAULT_SPOKEN_CPS_ZH,
    speak_title: bool = False,
    tone: str = "plain",
    variation_seed: int | None = None,
    allowed_facts: list[str] | None = None,
    forbidden_claims: list[str] | None = None,
    recipe_components: list[str] | None = None,
    exclude_openers: list[str] | None = None,
    industry_lines: list[str] | None = None,
    max_fill_ratio: float | None = None,
    scene_tags: list[str] | None = None,
    product_display: bool | None = None,
    loading_ops: bool | None = None,
) -> str:
    """Chinese voiceover: open → visual/facts body → close.

    Prefer picture/facts over template padding. Sparse content uses a lower
    fill ratio and never loops stock lines to bloated duration.

    On-screen titles are NOT spoken by default (speak_title=False).
    """
    target = float(target_duration_sec) if target_duration_sec and target_duration_sec > 0 else 26.0
    target = max(16.0, min(target, 45.0))
    cps_eff = max(float(cps), 2.5)

    lines: list[str] = []
    brand_short = _short_brand(brand)
    title_frags = title_content_fragments(title)
    rng = random.Random(variation_seed)
    flags = visual_context_flags(clip_hints, scene_tags=scene_tags, theme=theme)
    is_product = bool(product_display) if product_display is not None else flags["product_display"]
    is_loading = bool(loading_ops) if loading_ops is not None else flags["loading_ops"]
    tone_key = resolve_delivery_tone(
        tone,
        theme=theme,
        product_display=is_product,
        loading_ops=is_loading,
    )
    exclude_keys = {
        opener_key(x) for x in (exclude_openers or []) if str(x).strip()
    }

    def _has_title(ln: str) -> bool:
        c = re.sub(r"\s+", "", ln or "")
        return any(f in c for f in title_frags)

    def _try_add(ln: str, *, force_period: bool = True) -> bool:
        body = _ensure_period(ln) if force_period else (ln or "").strip()
        if not body or not _ok_line(body) or _has_title(body) or script_has_stock_ban(body):
            return False
        if is_product and script_has_shipping_claim(body):
            return False
        if is_product and script_has_packaging_meta(body):
            return False
        if is_product and not product_sentence_is_commercial(body):
            return False
        if body in lines or body in "".join(lines):
            return False
        lines.append(body)
        return True

    # --- density / fill targets ---
    grounded_raw = [
        str(x).strip()
        for x in [*(recipe_components or []), *(allowed_facts or [])]
        if str(x).strip()
    ]
    denied = [str(x).strip() for x in (forbidden_claims or []) if str(x).strip()]
    # Keyword-pack recipes often mix camera directions + questionnaire value lines —
    # convert to speakable service VO for *all* content types (not product-only).
    grounded = []
    for s in filter_grounding_for_speech(grounded_raw):
        if any(term and term in s for term in denied):
            continue
        if is_product and script_has_shipping_claim(s):
            continue
        if is_product and script_has_packaging_meta(s):
            continue
        if script_has_camera_direction(s) or script_has_questionnaire_speak(s):
            continue
        if script_has_video_meta_speak(s):
            continue
        if is_product and any(
            k in s
            for k in (
                "作业过程",
                "仓内",
                "装车",
                "人员操作",
                "现场走",
            )
        ):
            # Product closeups must not inherit warehouse process recipe lines
            continue
        grounded.append(s)
    if is_product:
        # Commercial product intro — **timeline order** from each clip (no shuffle).
        ordered: list[str] = []
        for h in clip_hints or []:
            for ln in commercial_product_lines_from_hints([h], limit=2):
                if ln not in ordered:
                    ordered.append(ln)
        if not ordered:
            ordered = commercial_product_lines_from_hints(clip_hints, limit=6)
        if not ordered:
            # fall back to industry pack lines (already commercial after pack edit)
            ordered = [
                ln
                for ln in theme_body_lines(theme or "产品", industry_lines)
                if not script_has_packaging_meta(ln) and not script_has_shipping_claim(ln)
            ][:6]
        hint_lines = ordered
        # Rich only when plan actually has product/visual facts (not stock industry pads).
        rich = (
            len([h for h in (clip_hints or []) if str(h).strip()]) >= 1
            or len(grounded) >= 1
            or len(commercial_product_lines_from_hints(clip_hints, limit=1) or []) >= 1
        )
    elif is_loading:
        # Continuous process VO (not raw vision captions as one-liners).
        hint_lines = loading_ops_flow_lines(
            clip_hints,
            brand=brand_short,
            limit=3,
            exclude_keys=exclude_keys,
            rng=rng,
        )
        rich = True
    else:
        hint_lines = clip_description_hints_as_lines(clip_hints, limit=4)
        rich = len(hint_lines) >= 2 or len(grounded) >= 2
    if max_fill_ratio is not None and float(max_fill_ratio) > 0:
        fill = float(max_fill_ratio)
    else:
        # Product/ops: fill most of timeline so VO does not die mid-video
        if is_product:
            # Empty product reels (no hints/facts) stay short — avoid pure stock padding.
            fill = 0.92 if rich else 0.55
        elif is_loading:
            fill = 0.90
        else:
            fill = 0.85 if rich else 0.60
    fill = max(0.40, min(fill, 0.95))
    need_chars = int(target * cps_eff * fill)
    max_chars = int(target * cps_eff * min(0.95, fill + 0.03))
    # Product/loading: do not gut long drafts back under ~fill (trim used to
    # pop mid-lines until half the timeline lost speech).
    if is_product or is_loading:
        max_chars = max(max_chars, need_chars + 36)

    # 1) Opener — product commercial; loading flow already contains continuous open.
    # Life-service packs often put the spoken open in recipe ``hook`` (来到/欢迎来到…);
    # prefer that as opener so we don't stack engine warm + pack hook both starting 来到.
    def _is_pack_opener_line(ln: str) -> bool:
        c = re.sub(r"\s+", "", ln or "")
        if not c:
            return False
        return c.startswith(("来到", "欢迎来到", "您好", f"来到{brand_short}", f"欢迎来到{brand_short}"))

    pack_opener: str | None = None
    remaining_grounded = list(grounded)
    if not is_product and not is_loading:
        for i, s in enumerate(remaining_grounded):
            if _is_pack_opener_line(s) and not script_has_stock_ban(s):
                pack_opener = s
                del remaining_grounded[i]
                break

    if is_product:
        product_openers = [
            f"本期{brand_short}带来一组在售好物。",
            f"下面速览{brand_short}在售品类。",
            f"{brand_short}本期上架好物速递。",
            f"一组{brand_short}常用好物放出来看。",
            f"{brand_short}热销工具与耗材速览。",
            f"先看{brand_short}这几样在售货。",
        ]
        allowed = [
            o
            for o in product_openers
            if opener_key(o) not in exclude_keys and not script_has_stock_ban(o)
        ]
        _try_add(rng.choice(allowed or product_openers))
    elif not is_loading:
        if pack_opener:
            _try_add(pack_opener)
        else:
            _try_add(_pick_opener(tone_key, brand_short, rng, exclude_keys))

    # 2) **Product: timeline product lines first** (before recipe so order matches cuts)
    # Non-product: grounded business claims preferred
    if is_product:
        pass  # product lines added in step 3 immediately after opener
    else:
        # Pack recipe lines are the story spine — include full sequence before char budget cuts.
        for sentence in remaining_grounded:
            _try_add(sentence)

    # Title content is on-screen only
    if speak_title:
        t = (title or "").replace("\n", "").replace("｜", "").strip()
        if t and t not in ("", brand_short) and len(t) >= 4:
            if not any(t in ln for ln in lines):
                _try_add(f"{t}，请对照挑选。")

    # 3) Product commercial (timeline) / loading flow / visual hints
    if is_loading:
        for ln in hint_lines:
            _try_add(ln)
            if _est_chars(lines) >= need_chars:
                break
    elif is_product:
        for h in hint_lines:
            clipped = h if len(h) <= 40 else h[:40]
            _try_add(clipped)
            if _est_chars(lines) >= need_chars:
                break
        # recipe grounded last as soft pad
        if _est_chars(lines) < need_chars:
            for sentence in grounded:
                if not product_sentence_is_commercial(sentence):
                    continue
                _try_add(sentence)
                if _est_chars(lines) >= need_chars:
                    break
    else:
        shuffled_hints = list(hint_lines)
        rng.shuffle(shuffled_hints)
        for h in shuffled_hints[:4]:
            clipped = h if len(h) <= 40 else h[:40]
            _try_add(clipped)
            if _est_chars(lines) >= need_chars:
                break

    desc = (description or "").strip()
    if desc and _est_chars(lines) < need_chars and not is_product and not is_loading:
        for sep in ("。", "！", "？", "\n"):
            if sep in desc:
                desc = desc.split(sep)[0].strip()
                break
        if desc and not script_has_shipping_claim(desc):
            _try_add(desc[:36])

    # 4) Industry / theme body (loading: at most one continuous line to avoid telegram pads)
    body_pool = theme_body_lines(theme, industry_lines)
    if is_loading or is_product:
        # Never pad warehouse/product VO with life-service store-intro stock.
        life_stock = {_ensure_period(x) for x in _THEME_LINES.get("default") or []}
        body_pool = [ln for ln in body_pool if ln not in life_stock]
    if is_loading and not body_pool:
        body_pool = [
            "码放整齐再核对件数。",
            "现场动作清楚，一步步做完。",
            "备货装车连着做。",
            "工地常用几样先过完。",
        ]
    if is_product:
        body_pool = [
            ln
            for ln in body_pool
            if not script_has_shipping_claim(ln)
            and not script_has_packaging_meta(ln)
            and not script_has_video_meta_speak(ln)
            and not script_has_camera_direction(ln)
        ]
        if not body_pool:
            body_pool = [
                "现场款式型号可对照。",
                "按工地需要挑对应品类。",
                "细节当面看清楚。",
            ]
    rng.shuffle(body_pool)
    if is_loading:
        body_budget = 1 if _est_chars(lines) < need_chars else 0
    elif is_product:
        # Keep adding safe industry lines until timeline is mostly covered
        body_budget = max(6, len(body_pool)) if rich else min(2, len(body_pool))
    elif tone_key == "warm" and len(grounded) >= 3:
        # Life-service: prefer pack gold lines; avoid theme stock after a full story
        body_budget = 0 if _est_chars(lines) >= int(need_chars * 0.72) else 1
    else:
        body_budget = len(body_pool) if rich else min(3, len(body_pool))
    used_body = 0
    for ln in body_pool:
        if used_body >= body_budget:
            break
        # Prefer multi-clause industry lines for ops polish
        if is_loading and "，" not in ln and len(re.sub(r"\s+", "", ln)) < 14:
            continue
        if _try_add(ln):
            used_body += 1
        if _est_chars(lines) >= need_chars:
            break

    # 4b) Product: pad without inventing SKUs not in the cut order
    if is_product and _est_chars(lines) < need_chars:
        # Only restate / soft-extend products already derived from this plan's clips
        pad_pool = list(commercial_product_lines_from_hints(clip_hints, limit=8) or [])
        safe_generic = [
            "现场款式型号可对照。",
            "按使用需要咨询对应品类。",
            "细节清楚，好决定要哪几样。",
            "常用型号到店对照挑选。",
            "看中的品类到店直接拿。",
        ]
        for ln in industry_lines or []:
            s = _ensure_period(str(ln).strip())
            if (
                s
                and s not in pad_pool
                and not script_has_shipping_claim(s)
                and not script_has_packaging_meta(s)
                and not script_has_stock_ban(s)
                and product_sentence_is_commercial(s)
            ):
                pad_pool.append(s)
        # Never shuffle SKU lines ahead of cut order; only soft generic may cycle
        for ln in pad_pool + safe_generic:
            if _est_chars(lines) >= need_chars:
                break
            _try_add(ln)

    # 5) Closer
    if len(lines) >= 2:
        joined_so_far = re.sub(r"\s+", "", "".join(lines))
        # Pack CTA often already ends with 来店/详聊 — do not stack a second invite.
        pack_already_closes = any(
            k in joined_so_far
            for k in (
                "来店",
                "约个时间",
                "欢迎抽空",
                "体验详聊",
                "预约",
                "当面为您讲明白",
            )
        )
        if is_product:
            closer_pool = [
                "看中的型号到店直接拿。",
                "缺哪样到店跟我们配。",
                "单买成套都按需拿。",
                "工地补料可到店配齐。",
                "要哪一类跟我们说一声。",
                "在架品类，按需选配。",
            ]
        elif is_loading:
            # Warehouse ops: never bleed life-service「完整服务」warm closers
            if any(k in joined_so_far for k in ("服务用心", "细节不放过", "对接", "到店", "来店")):
                closer_pool = []
            else:
                closer_pool = [
                    "要哪一类跟我们说一声。",
                    "工地补料可到店配齐。",
                    "货以当面看的为准。",
                    "缺哪样到店跟我们配。",
                    "具体品类再按需确认。",
                ]
        elif pack_already_closes and tone_key == "warm":
            closer_pool = []
        else:
            closer_pool = list(_TONE_CLOSERS.get(tone_key, _TONE_CLOSERS["plain"]))
        rng.shuffle(closer_pool)
        for closer in closer_pool:
            if opener_key(closer) in exclude_keys:
                continue
            if _try_add(closer):
                break

    # 6) Rich only / product: more unique body if still short
    if (rich or is_product) and not is_loading and _est_chars(lines) < need_chars:
        for ln in body_pool:
            if _try_add(ln) and _est_chars(lines) >= need_chars:
                break

    # 6b) Soft process bridges (product) — fill remaining char budget without packing talk
    if is_product and _est_chars(lines) < need_chars:
        bridges = [
            "继续过这几样实用货。",
            "品类对照清楚，好挑好配。",
            "工地门店都能用得上。",
            "细节看完再定要哪一类。",
            "这一段就是在售快览。",
            "多型号对照，按需挑选。",
            "常见型号都摆在眼前。",
            "相关品类一起过一遍。",
            "该紧的紧，该量的量。",
            "现场常用几样先过完。",
            "细节当面看清楚。",
        ]
        rng.shuffle(bridges)
        for ln in bridges:
            if _est_chars(lines) >= need_chars:
                break
            _try_add(ln)

    # 6c) Hard cover — last mile so 28s reels never die mid-timeline
    if (is_product or is_loading) and _est_chars(lines) < need_chars:
        hard = (
            [
                *list(commercial_product_lines_from_hints(clip_hints, limit=8) or []),
                "现场款式型号可对照。",
                "按使用需要咨询对应品类。",
                "细节清楚，好决定要哪几样。",
                f"{brand_short}在售品类继续过一遍。",
                "常用型号到店对照挑选。",
                "看中的型号到店直接拿。",
            ]
            if is_product
            else [
                "码放整齐再核对件数。",
                "现场动作清楚，一步步做完。",
                "装完再回头确认一遍。",
                "服务到位，细节不马虎。",
            ]
        )
        for ln in hard:
            if _est_chars(lines) >= need_chars:
                break
            _try_add(ln)
        # still short: allow mild repeat of commercial lines under different suffix
        if is_product and _est_chars(lines) < need_chars:
            extra = commercial_product_lines_from_hints(clip_hints, limit=10) or []
            for ln in extra:
                if _est_chars(lines) >= need_chars:
                    break
                _try_add(ln)

    out: list[str] = []
    for p in lines:
        p = p.strip(" ，,")
        if not p or not _ok_line(p) or _has_title(p) or script_has_stock_ban(p):
            continue
        if is_product and script_has_shipping_claim(p):
            continue
        if is_product and script_has_packaging_meta(p):
            continue
        out.append(_ensure_period(p))

    while _est_chars(out) > max_chars and len(out) > 4:
        # Keep opener (0) and closer (-1); drop overflow middle body first
        if len(out) >= 3:
            out.pop(-2)
        else:
            out.pop()

    fallback = (
        f"本期{brand_short}带来一组在售好物。"
        if is_product
        else f"跟着{brand_short}现场走一遍。"
    )
    script = "".join(out) if out else fallback
    if not speak_title:
        script = scrub_title_from_spoken(script, title)
        if not script:
            script = fallback
    if (
        script_has_stock_ban(script)
        or (is_product and script_has_shipping_claim(script))
        or (is_product and script_has_packaging_meta(script))
    ):
        if is_product:
            script = fallback
        else:
            script = _ensure_period(_pick_opener(tone_key, brand_short, rng, exclude_keys))
    return script


def expand_script_to_timeline(
    script: str,
    *,
    target_duration_sec: float,
    base_script: str = "",
    product_display: bool = False,
    loading_ops: bool = False,
    brand: str = "本店",
    clip_hints: list[str] | None = None,
    industry_lines: list[str] | None = None,
) -> str:
    """If spoken copy is too short for picture length, splice safe lines until ~fill.

    Keeps product/loading mouth closed on shipping/packaging/stock bans.
    """
    if not (product_display or loading_ops):
        return (script or base_script or "").strip()

    def _han(s: str) -> int:
        return len(re.sub(r"[^\u4e00-\u9fff]", "", s or ""))

    target = max(8.0, float(target_duration_sec) or 20.0)
    # ~3.4 han/s × ~0.90 cover; leave a short fade tail after speech
    min_han = int(target * 3.35)
    max_han = int(target * 3.9)
    cur = scrub_video_meta_sentences((script or "").strip())
    base = scrub_video_meta_sentences((base_script or "").strip())
    if product_display:
        cur = filter_product_commercial_script(cur)
        base = filter_product_commercial_script(base)
    if _han(base) > _han(cur) + 6 and _han(cur) < min_han:
        cur = base
    if _han(cur) >= min_han:
        # Never re-admit pre-filter pollution via `or cur`
        return cur if cur else (base or "")

    brand_short = _short_brand(brand)
    pieces: list[str] = []
    for part in re.split(r"(?<=[。！？])", cur):
        p = _ensure_period(part.strip())
        if not p or p in pieces:
            continue
        if product_display and not product_sentence_is_commercial(p):
            continue
        pieces.append(p)

    pad: list[str] = []
    if product_display:
        pad.extend(commercial_product_lines_from_hints(clip_hints, limit=8) or [])
        pad.extend(
            [
                "现场款式型号可对照。",
                "按使用需要咨询对应品类。",
                "细节清楚，好决定要哪几样。",
                "常用型号到店对照挑选。",
                f"{brand_short}在售品类继续过一遍。",
                "工地门店都能用得上。",
                "看中的型号到店直接拿。",
            ]
        )
        for ln in industry_lines or []:
            s = _ensure_period(str(ln).strip())
            if s:
                pad.append(s)
    else:
        pad.extend(
            loading_ops_flow_lines(
                clip_hints,
                brand=brand_short,
                limit=4,
                exclude_keys=set(),
                rng=random.Random(7),
            )
        )
        pad.extend(
            [
                "码放整齐再核对件数。",
                "现场动作清楚，一步步做完。",
                "服务到位，细节不马虎。",
            ]
        )

    seen = set(pieces)
    for raw in pad:
        p = _ensure_period(str(raw).strip())
        if not p or p in seen:
            continue
        if script_has_stock_ban(p) or script_has_camera_direction(p):
            continue
        if product_display and (
            script_has_shipping_claim(p)
            or script_has_packaging_meta(p)
            or script_has_video_meta_speak(p)
            or not product_sentence_is_commercial(p)
        ):
            continue
        if loading_ops and script_has_video_meta_speak(p):
            continue
        pieces.append(p)
        seen.add(p)
        if _han("".join(pieces)) >= min_han:
            break

    if product_display:
        # Final commercial-only gate
        out = filter_product_commercial_script("".join(pieces))
        return out or cur or base or f"本期{brand_short}带来一组在售好物。"

    out = "".join(pieces) if pieces else (cur or base or f"跟着{brand_short}现场走一遍。")
    if script_is_telegram_choppy(out):
        return cur or base or out
    return out


def _short_brand(brand: str) -> str:
    b = (brand or "品牌").strip()
    # Prefer an explicit short name in full-width parentheses for any customer.
    if "（" in b and "）" in b:
        inner = b[b.find("（") + 1 : b.find("）")].strip()
        if inner:
            return inner
    return b[:12] if len(b) > 12 else b


def _theme_bank(theme: str) -> list[str]:
    t = (theme or "default").strip()
    for key, lines in _THEME_LINES.items():
        if key != "default" and key in t:
            return list(lines)
    return list(_THEME_LINES["default"])


def _est_chars(lines: list[str]) -> int:
    return sum(len(re.sub(r"\s+", "", ln)) for ln in lines)


def _ok_line(text: str) -> bool:
    t = (text or "").strip()
    if not t or t.startswith("本期主题"):
        return False
    if any(x in t for x in _INTERJECTIONS):
        return False
    if any(x in t for x in _TECH):
        return False
    if script_has_stock_ban(t):
        return False
    return True


def clip_description_hints_as_lines(hints: list[str] | None, *, limit: int = 3) -> list[str]:
    """Speakable body lines from clip hints — never raw vision/composition caption.

    Prefer commercial product-use mapping. Fall back to `to_spoken_line` salvage.
    Do **not** dump long CV captions into VO (user: 文案≠描述画面风格/构图).
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in commercial_product_lines_from_hints(hints, limit=max(limit * 2, 4)):
        key = re.sub(r"\s+", "", line)
        if not key or key in seen or not _ok_line(line):
            continue
        seen.add(key)
        out.append(line if line.endswith(("。", "！", "？")) else line + "。")
        if len(out) >= limit:
            return out
    for h in hints or []:
        spoken = to_spoken_line(str(h))
        if not spoken:
            continue
        key = re.sub(r"\s+", "", spoken)
        if key in seen or not _ok_line(spoken):
            continue
        seen.add(key)
        out.append(spoken)
        if len(out) >= limit:
            break
    return out


def _clause_from_hint(text: str) -> str:
    """Turn a vision caption into a mid-sentence action clause (no trailing period).

    Rejects composition/meta captions so loading VO does not recite CV prose.
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    # Semantic label codes (warehouse/loading/truck) are not speakable Chinese.
    if re.fullmatch(r"[A-Za-z0-9_./\-+\s]+", raw):
        return ""
    if script_has_video_meta_speak(raw) or script_has_camera_direction(raw):
        commercial = commercial_product_lines_from_hints([raw], limit=1)
        if commercial:
            return re.sub(r"[。！？!?]+$", "", commercial[0])
        return ""
    s = re.sub(r"\s+", "", raw)
    s = s.strip("，,。．.!！？?")
    if not s:
        return ""
    # drop vision-caption lead-ins
    for pref in ("画面中", "视频中", "镜头里", "视频展示了", "画面展示了", "画面主体为", "画面主体是"):
        if s.startswith(pref):
            s = s[len(pref) :]
    # Strip leftover latin tokens so VO never says "warehouse,loading"
    s = re.sub(r"[A-Za-z][A-Za-z0-9_./\-]*", "", s)
    s = re.sub(r"[，,]{2,}", "，", s).strip("，,")
    if not s or not re.search(r"[\u4e00-\u9fff]", s):
        return ""
    # Still meta after strip?
    if script_has_video_meta_speak(s) or len(s) > 40:
        commercial = commercial_product_lines_from_hints([raw], limit=1)
        if commercial:
            return re.sub(r"[。！？!?]+$", "", commercial[0])
        return ""
    return s[:36]


def script_is_telegram_choppy(text: str) -> bool:
    """True when copy is a run of ultra-short dry sentences (not human VO)."""
    compact = re.sub(r"\s+", "", str(text or "").strip())
    if not compact:
        return False
    parts = [p for p in re.split(r"[。！？!?]+", compact) if p]
    if len(parts) < 2:
        return False
    short = sum(1 for p in parts if len(re.sub(r"[，,、；;：:]", "", p)) <= 8)
    # majority ultra-short periods = telegram style
    return short >= max(2, int(0.6 * len(parts)))


def loading_ops_flow_lines(
    hints: list[str] | None,
    *,
    brand: str = "品牌",
    limit: int = 3,
    exclude_keys: set[str] | None = None,
    rng: random.Random | None = None,
) -> list[str]:
    """Stitch warehouse/loading captions into continuous process sentences (#179 style)."""
    brand_short = _short_brand(brand)
    pick = rng or random.Random(0)
    banned = exclude_keys or set()
    clauses: list[str] = []
    for h in hints or []:
        c = _clause_from_hint(h)
        if not c or c in clauses or script_has_stock_ban(c) or script_has_oral_filler(c):
            continue
        clauses.append(c)
        if len(clauses) >= 4:
            break
    if not clauses:
        clauses = [
            "工作人员正忙碌地装载货物",
            "每一件配件都仔细核对",
            "车门敞开向内码放",
        ]
    open_prefixes = [
        f"跟着{brand_short}的真实现场走一遍，",
        f"这一段跟着{brand_short}现场走一遍，",
        f"先走进{brand_short}作业现场，",
        f"把{brand_short}装运过程说清楚，",
        f"先到{brand_short}现场，把过程听清楚，",
    ]
    allowed_open = [p for p in open_prefixes if opener_key(p) not in banned]
    if not allowed_open:
        allowed_open = list(open_prefixes)
    pick.shuffle(allowed_open)
    prefix = allowed_open[0]

    lines: list[str] = []
    head = clauses[:2]
    first = prefix + "，".join(head)
    if len(head) == 1:
        first = first + "，过程跟着往下走"
    lines.append(_ensure_period(first))
    rest = clauses[2:]
    if rest:
        mid = "，".join(rest)
        lines.append(_ensure_period(f"随后{mid}，一步都交代清楚"))
    if len(lines) < limit:
        lines.append("要哪一类跟我们说一声。")
    return lines[: max(1, int(limit))]


def clip_description_hints(plan_or_clips: Any, *, limit: int = 3) -> list[str]:
    clips = getattr(plan_or_clips, "clips", None) or plan_or_clips or []
    out: list[str] = []
    for c in clips:
        candidates: list[str] = []
        if hasattr(c, "description"):
            candidates.append(str(getattr(c, "description", "") or ""))
        if hasattr(c, "scene"):
            candidates.append(str(getattr(c, "scene", "") or ""))
        if isinstance(c, dict):
            candidates.extend(
                [
                    str(c.get("description") or ""),
                    str(c.get("scene") or ""),
                    str(c.get("caption") or ""),
                ]
            )
        for raw in candidates:
            d = _clean_hint(raw)
            if d and d not in out:
                out.append(d)
                break
        if len(out) >= limit:
            break
    return out


def _clean_hint(raw: str) -> str:
    """Drop path-like / catalog metadata that sounds bad when spoken."""
    d = (raw or "").strip()
    if not d:
        return ""

    chunks = re.split(r"[；;|\n]+", d)
    scored: list[tuple[int, str]] = []
    for ch in chunks:
        ch = ch.strip()
        if not ch:
            continue
        if re.search(r"片段\s*\d|秒共|\d+\.\d+\s*-\s*\d+|竖屏|实拍业务素材|素材[:：]|分类[:：]|文件[:：]", ch):
            continue
        if re.fullmatch(r"[\w./\\-]+\.(mp4|mov|mxf|jpg|png)", ch, flags=re.I):
            continue
        ch = re.sub(r"\b[\w./\\-]+\.(mp4|mov|mxf|jpg|png)\b", "", ch, flags=re.I)
        ch = re.sub(r"\d{6,}", "", ch)
        ch = re.sub(r"\s+", " ", ch).strip(" ，,.;；：:")
        if len(ch) < 6:
            continue
        han = len(re.findall(r"[\u4e00-\u9fff]", ch))
        if han < 4:
            continue
        score = han * 2 + min(len(ch), 40)
        if any(token in ch for token in ("画面", "现场", "人物", "产品", "服务", "过程", "细节")):
            score += 12
        scored.append((score, ch))

    if not scored:
        d = re.sub(r"^(分类|素材|片段|文件|路径)\s*[:：]\s*", "", d)
        d = re.sub(r"[；;]\s*(分类|素材|片段|文件|竖屏|实拍业务素材)[^；;]*", "", d)
        d = re.sub(r"片段\s*\d[\d.\-–—秒共\s]*", "", d)
        d = re.sub(r"\s+", " ", d).strip(" ，,.;；")
        han = len(re.findall(r"[\u4e00-\u9fff]", d))
        if han < 6 or len(d) < 8:
            return ""
        return d[:48]

    scored.sort(key=lambda x: -x[0])
    return scored[0][1][:48]


def _srt_ts(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_from_narration_segments(
    segments: list[Any],
    *,
    video_duration_sec: float | None = None,
    bottom_dual_line: bool = True,
    max_chars_per_line: int = 14,
    tail_trim_seconds: float = 0.12,
    inter_sentence_gap_seconds: float = 0.08,
    forbid_title: str | None = None,
) -> str:
    """Build SRT from TTS segment timings (index/text/duration_sec).

    HARD RULE (旁白字幕锁):
    - Cue end tracks **spoken** audio only — never the silence after speech.
    - Prefer absolute ``start_sec``/``end_sec`` when present (oneshot bed).
    - Inter-sentence gap is blank screen, not an extension of the previous cue.
    """

    def _wrap_dual(text: str) -> str:
        t = text.replace("\n", "").strip()
        if not bottom_dual_line or len(t) <= max_chars_per_line:
            return t
        mid = (len(t) + 1) // 2
        break_at = mid
        for i in range(mid, max(2, mid - 5), -1):
            if t[i - 1] in " ，,、；;":
                break_at = i
                break
        line1 = t[:break_at].strip(" ，,、；;")
        line2 = t[break_at:].strip(" ，,、；;")
        return f"{line1}\n{line2}" if line2 else line1

    def _seg_fields(seg: Any) -> tuple[str, float, float | None, float | None]:
        if hasattr(seg, "text"):
            text = str(seg.text or "").strip()
            dur = float(getattr(seg, "duration_sec", 0) or 0)
            st = getattr(seg, "start_sec", None)
            en = getattr(seg, "end_sec", None)
        elif isinstance(seg, dict):
            text = str(seg.get("text") or "").strip()
            dur = float(seg.get("duration_sec") or 0)
            st = seg.get("start_sec")
            en = seg.get("end_sec")
        else:
            return "", 0.0, None, None
        start_abs = float(st) if st is not None else None
        end_abs = float(en) if en is not None else None
        return text, dur, start_abs, end_abs

    lines: list[str] = []
    t = 0.0
    idx = 1
    trim = max(0.0, float(tail_trim_seconds))
    gap = max(0.0, float(inter_sentence_gap_seconds))
    for seg in segments:
        text, dur, start_abs, end_abs = _seg_fields(seg)
        if not text:
            continue
        if text.startswith("本期主题"):
            if start_abs is not None and end_abs is not None:
                t = max(t, end_abs)
            else:
                t += max(dur, 0.0) + gap
            continue
        if forbid_title:
            text = scrub_title_from_spoken(text, forbid_title)
        if not text:
            if start_abs is not None and end_abs is not None:
                t = max(t, end_abs)
            else:
                t += max(dur, 0.0) + gap
            continue

        if start_abs is not None and end_abs is not None and end_abs > start_abs:
            start = start_abs
            end = max(start + 0.05, end_abs - trim)
            advance_to = end_abs
        else:
            if dur <= 0.05:
                continue
            start = t
            end = t + max(0.05, dur - trim)
            advance_to = end + gap

        if video_duration_sec and start >= video_duration_sec:
            break
        if video_duration_sec:
            end = min(end, video_duration_sec)
        display = _wrap_dual(text)
        from engine.pack.text_sanitize import subtitle_display_text

        # CJK: strip punctuation/spaces per lock. Thai/Latin keeps readable spaces.
        display = subtitle_display_text(display, keep_newlines=True)
        if not display:
            t = advance_to
            continue
        lines.append(str(idx))
        lines.append(f"{_srt_ts(start)} --> {_srt_ts(end)}")
        lines.append(display)
        lines.append("")
        t = advance_to
        idx += 1
    if not lines and video_duration_sec:
        from engine.pack.publish import build_subtitle_srt

        return build_subtitle_srt("精彩内容", duration_sec=min(6.0, video_duration_sec))
    return "\n".join(lines).strip() + ("\n" if lines else "")


def tighten_srt_to_voiceover(
    srt_body: str,
    voiceover: Any,
    *,
    tail_trim_seconds: float = 0.12,
    min_cue_sec: float = 0.35,
) -> str:
    """Post-pass: clamp each cue end to audible speech on the VO bed.

    Guarantees the hard rule even when segment math drifts: after speech stops,
    the caption must clear (句间静音 = 空屏，不得拖字).
    """
    from pathlib import Path

    from engine.pack.tts import _speech_spans_by_silence, probe_audio_duration

    wav = Path(voiceover)
    if not srt_body.strip() or not wav.is_file():
        return srt_body
    spans = _speech_spans_by_silence(wav, min_silence=0.06)
    if not spans:
        return srt_body
    vo_dur = probe_audio_duration(wav)
    trim = max(0.0, float(tail_trim_seconds))

    def _parse_ts(ts: str) -> float:
        hh, mm, rest = ts.strip().split(":")
        ss, ms = rest.split(",")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0

    blocks = [c.strip() for c in srt_body.strip().split("\n\n") if c.strip()]
    out: list[str] = []
    for block in blocks:
        blines = block.splitlines()
        if len(blines) < 2 or "-->" not in blines[1]:
            out.append(block)
            continue
        left, right = [p.strip() for p in blines[1].split("-->")]
        start = _parse_ts(left)
        end = _parse_ts(right)
        # Must match ready_gate._check_align window (end+0.05). A looser
        # end+0.25 pulls in the *next* utterance's span → speech_end too late →
        # no clamp → READY_GATE overhang fails on already-"tightened" SRTs.
        overlapping = [(s, e) for s, e in spans if e > start + 0.02 and s < end + 0.05]
        if overlapping:
            speech_end = max(e for _, e in overlapping)
            new_end = min(end, speech_end - trim)
        else:
            new_end = start + min_cue_sec
        new_end = max(start + 0.05, new_end)
        if vo_dur > 0:
            new_end = min(new_end, max(0.05, vo_dur - 0.02))
        blines[1] = f"{_srt_ts(start)} --> {_srt_ts(new_end)}"
        out.append("\n".join(blines))

    parsed: list[tuple[list[str], float, float]] = []
    for block in out:
        blines = block.splitlines()
        if len(blines) < 2 or "-->" not in blines[1]:
            parsed.append((blines, 0.0, 0.0))
            continue
        left, right = [p.strip() for p in blines[1].split("-->")]
        parsed.append((blines, _parse_ts(left), _parse_ts(right)))
    for i in range(len(parsed) - 1):
        blines, start, end = parsed[i]
        nstart = parsed[i + 1][1]
        if nstart > 0 and end > nstart - 0.02:
            end = max(start + 0.05, nstart - 0.02)
            blines[1] = f"{_srt_ts(start)} --> {_srt_ts(end)}"
            parsed[i] = (blines, start, end)
    return "\n\n".join("\n".join(b[0]) for b in parsed).strip() + "\n"
