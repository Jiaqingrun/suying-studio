#!/usr/bin/env python3
"""Analyze TF ad-copy corpus → pattern cards + industry pack seed (全行业通用).

Reads: /Volumes/TF/广告文案/中文广告语-全部.jsonl
Writes:
  configs/pattern_cards/universal-ad-copy.json
  configs/pattern_cards/universal-ad-copy-report.md
  configs/samples/industry/universal-ad-copy/pack.json
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = Path("/Volumes/TF/广告文案/中文广告语-全部.jsonl")
OUT_CARDS = REPO / "configs/pattern_cards/universal-ad-copy.json"
OUT_REPORT = REPO / "configs/pattern_cards/universal-ad-copy-report.md"
OUT_PACK = REPO / "configs/samples/industry/universal-ad-copy/pack.json"

# --- classification rules ---
PATTERN_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("question", "提问开场", re.compile(r"[？?]$|吗[？?]?$|是不是|要不要|能否|可不可以")),
    ("cta", "行动号召", re.compile(r"(预约|咨询|来店|到店|点击|立即|马上|现在|赶紧|欢迎|详聊|体验|联系|下单|抢购|领取)")),
    ("trust", "信任背书", re.compile(r"(官方|正品|安心|放心|保障|专业|认证|口碑|好评|标准|品质)")),
    ("benefit", "利益点", re.compile(r"(省心|省力|省钱|方便|快捷|高效|实用|好用|解决|缓解|提升|更[好坏快省轻松])")),
    ("contrast", "对比反差", re.compile(r"(不用|不再|告别|相比|原来|以前|终于|别再)")),
    ("scene", "场景感受", re.compile(r"(现场|亲眼|跟着|镜头|看得见|细节|一步|环境|氛围|体验)")),
    ("price", "价格促销", re.compile(r"(优惠|折扣|特价|低价|免费|0元|元起|划算|性价比)")),
]

BANNED_IN_OUTPUT = re.compile(
    r"(第一|最好|保证|根治|治愈|疗效|厂家直销|马上发货|安排到店服务|会提前和您讲清楚)"
)

KANA = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\uFF65-\uFF9F]")


def hans_len(s: str) -> int:
    return sum(1 for c in s if "\u4e00" <= c <= "\u9fff")


def load_corpus() -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    with CORPUS.open(encoding="utf-8") as f:
        for row in f:
            row = row.strip()
            if not row:
                continue
            zh = json.loads(row).get("text_zh", "").strip()
            if not zh or zh in seen or KANA.search(zh):
                continue
            seen.add(zh)
            lines.append(zh)
    return lines


def bucket_length(zh: str) -> str:
    n = hans_len(zh)
    if n <= 12:
        return "title"
    if n <= 24:
        return "hook"
    if n <= 48:
        return "vo_line"
    return "long_form"


def classify(zh: str) -> list[str]:
    tags: list[str] = []
    for pid, _name, pat in PATTERN_RULES:
        if pat.search(zh):
            tags.append(pid)
    if not tags:
        tags.append("plain")
    return tags


def clean_for_pack(zh: str, max_hans: int) -> str | None:
    s = re.sub(r"\s+", " ", zh.strip())
    if not s or BANNED_IN_OUTPUT.search(s):
        return None
    if hans_len(s) > max_hans:
        return None
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", s):
        return None
    return s


# Original template lines derived from patterns (NOT copied from corpus)
PATTERN_CARD_TEMPLATES: list[dict] = [
    {
        "id": "pc_question_pain",
        "channel": "hook",
        "structure": "提问 + 轻痛点",
        "slots": ["痛点"],
        "examples": [
            "还在纠结怎么选？先看清需求再说。",
            "总怕选错？先把关键细节问明白。",
            "不知道合不合适？到店聊两句就清楚。",
            "担心踩坑？先把流程问明白。",
            "拿不准？先看现场再决定。",
        ],
    },
    {
        "id": "pc_scene_benefit",
        "channel": "vo_line",
        "structure": "场景 → 可见好处",
        "slots": ["场景", "好处"],
        "examples": [
            "跟着镜头看现场，步骤都在眼前。",
            "先看环境是否整洁，再谈是否合适。",
            "细节摆得清楚，用起来也省心。",
            "画面里能看见的，才说得明白。",
            "现场一目了然，不用空讲概念。",
            "镜头扫过重点，心里更有数。",
        ],
    },
    {
        "id": "pc_benefit_direct",
        "channel": "hook",
        "structure": "品类 + 用途",
        "slots": ["品类", "用途"],
        "examples": [
            "常用款在这，日常用起来顺手。",
            "这类需求常见，按用途挑更省事。",
            "先看用途，再决定要不要入手。",
            "实用款摆齐，需要时好拿。",
            "日常用得上的，才值得细看。",
        ],
    },
    {
        "id": "pc_trust_standard",
        "channel": "vo_line",
        "structure": "标准/流程 + 安心",
        "slots": ["标准点"],
        "examples": [
            "流程讲清楚，每一步都不糊弄。",
            "标准统一，细节看得见。",
            "先沟通再安排，心里更有数。",
            "先把情况问明白，再一起定方案。",
            "步骤有章法，节奏也不赶。",
        ],
    },
    {
        "id": "pc_cta_soft",
        "channel": "cta",
        "structure": "轻收尾 CTA",
        "slots": ["动作"],
        "examples": [
            "有空来店里体验详聊。",
            "想了解细节，欢迎到店看看。",
            "需要再沟通，随时联系我们。",
            "方便时来现场感受一下。",
            "想进一步聊，欢迎来店坐坐。",
        ],
    },
    {
        "id": "pc_contrast_old_new",
        "channel": "hook",
        "structure": "旧麻烦 → 新做法",
        "slots": ["旧麻烦", "新做法"],
        "examples": [
            "不用反复跑，一次把事说清楚。",
            "告别来回折腾，流程更顺一点。",
            "别再瞎猜，先看明白再决定。",
            "少跑冤枉路，先把要点问清。",
            "不用自己琢磨，现场看更直观。",
        ],
    },
    {
        "id": "pc_title_curiosity",
        "channel": "title",
        "structure": "好奇/悬念短标题",
        "slots": ["主题"],
        "examples": [
            "这一步很多人忽略了",
            "先看这里再决定",
            "细节决定体验",
            "现场看一眼就懂",
            "很多人卡在这一步",
            "先搞清这一点",
        ],
    },
    {
        "id": "pc_title_benefit",
        "channel": "title",
        "structure": "利益点短标题",
        "slots": ["利益"],
        "examples": [
            "省心省时的选择",
            "用起来更顺手",
            "流程清楚不绕弯",
            "细节看得见",
            "步骤更清楚",
            "现场好判断",
        ],
    },
    {
        "id": "pc_process_step",
        "channel": "vo_line",
        "structure": "步骤链",
        "slots": ["步骤1", "步骤2"],
        "examples": [
            "先沟通需求，再安排合适方案。",
            "先看现场，再确认细节。",
            "先了解想法，再一起定安排。",
            "先问清楚用途，再推荐合适的。",
            "先看清现状，再谈怎么配合。",
        ],
    },
    {
        "id": "pc_listen_arrange",
        "channel": "vo_line",
        "structure": "倾听 → 安排（服务通用）",
        "slots": [],
        "examples": [
            "先听完想法，再安排合适项目。",
            "先沟通清楚，再一步步来。",
            "先了解需求，再给出建议。",
            "先听您说完，再一起商量安排。",
            "先弄清重点，再往下推进。",
        ],
    },
    {
        "id": "pc_open_welcome",
        "channel": "hook",
        "structure": "来到/欢迎 + 感受",
        "slots": [],
        "examples": [
            "先感受这里怎样为您服务。",
            "进门先看环境，心里有个数。",
            "来到现场，先把样子看清楚。",
            "欢迎来看看，细节都在眼前。",
            "先进来感受一下氛围。",
        ],
    },
    {
        "id": "pc_value_plain",
        "channel": "vo_line",
        "structure": "平实价值句",
        "slots": [],
        "examples": [
            "看得见的地方，才说得实在。",
            "把重点说清楚，比堆词更重要。",
            "实用为主，不绕弯子。",
            "先把事办顺，比说满更重要。",
            "细节到位，体验自然跟上。",
        ],
    },
    {
        "id": "pc_hook_time_save",
        "channel": "hook",
        "structure": "省时省力",
        "slots": [],
        "examples": [
            "少折腾一步，事就顺一点。",
            "流程顺了，时间也省下来。",
            "把麻烦留在门外，进门好办事。",
            "一次说清楚，比来回问更省事。",
            "该问的当面问，别自己瞎琢磨。",
        ],
    },
    {
        "id": "pc_hook_first_look",
        "channel": "hook",
        "structure": "先看再定",
        "slots": [],
        "examples": [
            "先看一眼，心里就有谱。",
            "眼见为实，比听说更踏实。",
            "现场看一眼，比空想靠谱。",
            "先把样子看清，再谈合不合适。",
            "看明白再动手，少返工。",
        ],
    },
    {
        "id": "pc_hook_daily_need",
        "channel": "hook",
        "structure": "日常刚需",
        "slots": [],
        "examples": [
            "日常用得上的，才值得细看。",
            "常用款摆齐，需要时好拿。",
            "这类需求常见，按用途挑更省事。",
            "家里常备款，关键时刻不抓瞎。",
            "实用款在这，顺手就能用上。",
        ],
    },
    {
        "id": "pc_hook_worry",
        "channel": "hook",
        "structure": "顾虑化解",
        "slots": [],
        "examples": [
            "担心不合适？先把细节问明白。",
            "怕踩坑？流程摊开给您看。",
            "拿不准？现场对照着选。",
            "心里没底？先沟通再决定。",
            "犹豫很正常，先把要点搞清楚。",
        ],
    },
    {
        "id": "pc_hook_step",
        "channel": "hook",
        "structure": "步骤提示",
        "slots": [],
        "examples": [
            "第一步先看环境，第二步再细聊。",
            "先了解用途，再推荐合适的。",
            "先问清楚重点，再往下推进。",
            "先把现状摸清，再定怎么配合。",
            "先沟通需求，再安排合适方案。",
        ],
    },
    {
        "id": "pc_hook_store",
        "channel": "hook",
        "structure": "到店感受",
        "slots": [],
        "examples": [
            "进门先感受氛围，再慢慢看细节。",
            "到店坐坐，把想法说清楚。",
            "欢迎来看看，细节都在眼前。",
            "先进来感受一下，再决定要不要。",
            "来到现场，先把重点看清楚。",
        ],
    },
    {
        "id": "pc_hook_guide",
        "channel": "hook",
        "structure": "引导选择",
        "slots": [],
        "examples": [
            "按用途挑，比盲目选更稳。",
            "先看关键参数，再谈要不要。",
            "把需求说清楚，推荐更对症。",
            "不懂没关系，当面讲给您听。",
            "有疑问就问，别憋着不说。",
        ],
    },
    {
        "id": "pc_hook_simple",
        "channel": "hook",
        "structure": "直白开场",
        "slots": [],
        "examples": [
            "今天把重点说清楚。",
            "这条片只讲您关心的。",
            "不绕弯，直接看要点。",
            "先把事说明白，再往下聊。",
            "开门见山，看关键细节。",
        ],
    },
    {
        "id": "pc_hook_try",
        "channel": "hook",
        "structure": "体验邀请",
        "slots": [],
        "examples": [
            "方便时来感受一下。",
            "有空来看看，细节当面聊。",
            "想进一步了解，欢迎来店体验。",
            "亲自看一眼，比光看介绍踏实。",
            "来现场试试手感，再决定也不迟。",
        ],
    },
    {
        "id": "pc_title_scene",
        "channel": "title",
        "structure": "场景短标题",
        "slots": [],
        "examples": [
            "现场一眼看懂",
            "细节摆给你看",
            "进门先看这里",
            "这一步很关键",
            "先看环境再决定",
            "镜头对准重点",
        ],
    },
    {
        "id": "pc_title_plain",
        "channel": "title",
        "structure": "平实短标题",
        "slots": [],
        "examples": [
            "实用为主",
            "流程更清楚",
            "省心一点",
            "当面好判断",
            "先把事办顺",
            "重点讲明白",
        ],
    },
]

# 模式卡外补充的自写 hook（仍非语料复制）
EXTRA_HOOK_LINES: list[str] = [
    "先把关键问题问清，再往下走。",
    "不急着下定，先把细节看清。",
    "现场对照着选，心里更踏实。",
    "把用途说清楚，推荐更对症。",
    "有疑问当面问，别自己猜。",
    "先看再决定，少踩坑。",
    "把流程摊开看，每一步都清楚。",
    "实用款在这，顺手就能用。",
    "进门先看重点，再慢慢细聊。",
    "今天只讲您关心的那点事。",
]


def pick_diverse(candidates: list[str], limit: int, max_hans: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in candidates:
        c = clean_for_pack(s, max_hans)
        if not c or c in seen:
            continue
        # skip lines that look like raw brand dumps (too much latin)
        latin = sum(1 for ch in c if "A" <= ch <= "Z" or "a" <= ch <= "z")
        if latin > 8 and hans_len(c) < 8:
            continue
        seen.add(c)
        out.append(c)
        if len(out) >= limit:
            break
    return out


def synthesize_from_templates(cards: list[dict], channel: str, limit: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for card in cards:
        if card.get("channel") != channel:
            continue
        for ex in card.get("examples") or []:
            s = str(ex).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out[:limit]


def build_pack(cards: list[dict], stats: dict) -> dict:
    """Industry pack uses pattern-card examples only (no corpus verbatim)."""
    hooks = synthesize_from_templates(cards, "hook", 80)
    titles = synthesize_from_templates(cards, "title", 48)
    ctas = synthesize_from_templates(cards, "cta", 24)
    vo_lines = synthesize_from_templates(cards, "vo_line", 48)

    # de-dup while preserving order
    def uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for s in items:
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out

    hooks = uniq(hooks + [s for s in EXTRA_HOOK_LINES if clean_for_pack(s, 24)])
    titles = uniq(titles)
    ctas = uniq(ctas)
    vo_lines = uniq(vo_lines)

    return {
        "id": "universal-ad-copy",
        "name": "全行业通用·广告文案模式包",
        "locale": "zh-CN",
        "semantic_vision_notes": [
            "旁白与标题优先画面可见事实，禁止空垫与保证性承诺。",
            "短句呼吸，一句一意；标题不上口播。",
        ],
        "hooks": hooks,
        "content_themes": ["开场", "利益", "信任", "行动", "default"],
        "theme_rules": [
            {"theme": "开场", "keywords": ["来到", "先看", "进门", "现场", "镜头", "欢迎"]},
            {"theme": "利益", "keywords": ["省心", "方便", "实用", "解决", "细节", "步骤"]},
            {"theme": "信任", "keywords": ["标准", "流程", "沟通", "倾听", "专业", "清楚"]},
            {"theme": "行动", "keywords": ["来店", "详聊", "体验", "咨询", "预约", "联系"]},
        ],
        "pack_to_content": {
            "开场": "开场",
            "利益": "利益",
            "信任": "信任",
            "行动": "行动",
            "default": "default",
        },
        "content_to_pack": {
            "开场": ["开场", "default"],
            "利益": ["利益", "default"],
            "信任": ["信任", "default"],
            "行动": ["行动", "default"],
            "default": ["default", "开场"],
        },
        "template_bindings": {
            "default": "default-vertical",
        },
        "narration_lines": {
            "开场": vo_lines[:8] + [h for h in hooks[:6] if hans_len(h) >= 8],
            "利益": vo_lines[8:16] if len(vo_lines) > 8 else vo_lines,
            "信任": [
                "流程讲清楚，每一步都不糊弄。",
                "标准统一，细节看得见。",
                "先沟通再安排，心里更有数。",
                "先把情况问明白，再一起定方案。",
            ],
            "行动": ctas + [
                "想了解细节，欢迎到店看看。",
                "需要再沟通，随时联系我们。",
            ],
            "default": vo_lines + ctas[:4],
        },
        "title_candidates": titles,
        "pattern_card_ids": [c["id"] for c in cards],
        "meta": {
            "source": "TF 广告文案语料分析（模式卡，非原文复制）",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "corpus_path": str(CORPUS),
            "note": "新客户可在 profile.industry_pack 指向 universal-ad-copy；标题用 title_candidates 或 hooks 短句。",
        },
    }


def main() -> None:
    if not CORPUS.is_file():
        raise SystemExit(f"corpus missing: {CORPUS}")

    corpus = load_corpus()
    len_buckets = Counter(bucket_length(z) for z in corpus)
    tag_counts: Counter[str] = Counter()
    hans_lens = [hans_len(z) for z in corpus]
    short_samples: list[tuple[str, list[str]]] = []

    for zh in corpus:
        tags = classify(zh)
        for t in tags:
            tag_counts[t] += 1
        if bucket_length(zh) in ("title", "hook") and len(short_samples) < 8000:
            short_samples.append((zh, tags))

    cards_doc = {
        "schema": "suying.pattern-cards.v1",
        "id": "universal-ad-copy",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus_stats": {
            "unique_lines": len(corpus),
            "length_buckets": dict(len_buckets),
            "pattern_tags": dict(tag_counts.most_common()),
            "hans_len_median": int(statistics.median(hans_lens)) if hans_lens else 0,
        },
        "cards": PATTERN_CARD_TEMPLATES,
        "usage": "引擎/词池引用 structure+examples 写新句；禁止整句复制语料原文。",
    }

    stats = {"short_samples": short_samples}
    pack = build_pack(PATTERN_CARD_TEMPLATES, stats)

    OUT_CARDS.parent.mkdir(parents=True, exist_ok=True)
    OUT_PACK.parent.mkdir(parents=True, exist_ok=True)
    OUT_CARDS.write_text(json.dumps(cards_doc, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_PACK.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")

    report = f"""# 广告文案语料分析报告 · 全行业通用

生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}

## 语料规模

- 去重中文句：**{len(corpus)}** 条
- 来源：`{CORPUS}`

## 长度分布

| 桶 | 条数 | 用途 |
|----|------|------|
| title (≤12字) | {len_buckets.get('title', 0)} | 片上标题 |
| hook (13–24字) | {len_buckets.get('hook', 0)} | 开场/短钩子 |
| vo_line (25–48字) | {len_buckets.get('vo_line', 0)} | 单句旁白 |
| long_form (>48字) | {len_buckets.get('long_form', 0)} | 只抽结构，不当口播 |

## 模式标签命中（可重叠）

"""
    for tag, n in tag_counts.most_common():
        report += f"- `{tag}`: {n}\n"

    report += f"""
## 产出物

1. 模式卡：`configs/pattern_cards/universal-ad-copy.json`（{len(PATTERN_CARD_TEMPLATES)} 张）
2. 行业包：`configs/samples/industry/universal-ad-copy/pack.json`
   - hooks: {len(pack['hooks'])} 条
   - title_candidates: {len(pack.get('title_candidates', []))} 条
   - narration_lines 主题: {', '.join(pack['narration_lines'].keys())}

## 接入速影

1. 新客户 `profile.sample.json` 设置 `"industry_pack": "universal-ad-copy"`
2. 或从本包复制 hooks / narration_lines 到客户 `keyword-pack.json`
3. 生成时走「模式卡槽位 + 近窗去重」，禁止整句背语料

## 说明

- 写入行业包的句子为**模式卡自写示例句**，不是语料原文复制。
- 长邮件/产品描述（long_form）未整句入库，仅用于统计。
"""
    OUT_REPORT.write_text(report, encoding="utf-8")
    print(json.dumps({
        "corpus": len(corpus),
        "cards": len(PATTERN_CARD_TEMPLATES),
        "hooks": len(pack["hooks"]),
        "titles": len(pack.get("title_candidates", [])),
        "out_cards": str(OUT_CARDS),
        "out_pack": str(OUT_PACK),
        "out_report": str(OUT_REPORT),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
