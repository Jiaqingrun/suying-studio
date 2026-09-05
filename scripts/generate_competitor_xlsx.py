#!/usr/bin/env python3
"""Generate styled 速影竞品对比 Excel — real data, polished layout."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, PieChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

CANVAS = Path("/Users/qr/.cursor/projects/Users-qr-QR-dev/canvases/suying-competitor-landscape.canvas.tsx")
ICON = Path("/Users/qr/QR/dev/速影/apps/desktop/src-tauri/icons/icon.png")
OUT = Path.home() / "Desktop" / "速影-全球同类产品对比.xlsx"

C = {
    "navy": "0F2744",
    "navy2": "1A3A5C",
    "accent": "2563EB",
    "accent_soft": "DBEAFE",
    "suying": "DCFCE7",
    "suying_border": "16A34A",
    "hero": "0F2744",
    "hero_text": "FFFFFF",
    "muted": "64748B",
    "bg": "F8FAFC",
    "card": "FFFFFF",
    "zebra": "F1F5F9",
    "border": "CBD5E1",
    "header": "1E3A5F",
    "header_text": "FFFFFF",
    "gold": "FEF3C7",
    "yes": "BBF7D0",
    "no": "F1F5F9",
    "warn": "FEF9C3",
}

FONT = "PingFang SC"

ROW = {
    "group_hdr": 26,
    "col_hdr": 44,
    "data": 50,
    "data_compact": 36,
    "detail": 62,
    "cover_note": 44,
    "cover_kpi": 38,
    "title": 40,
}

MAIN_COL_WIDTHS = {
    "产品": 20, "厂商": 20, "品类": 13, "区域": 15,
    "部署": 9, "网络": 9, "定价模式": 24, "均分": 8,
    "流程完整性": 11, "结构清晰度": 11, "本地独立性": 11,
    "实拍片库混剪": 12, "质量门禁": 10, "发布物料包": 11,
    "发布触达": 10, "多客户/行业": 11,
    "备注": 34, "数据可信度": 12,
}


def _typo(name: str) -> Font:
    """Typography presets — defined after C."""
    presets = {
        "title": F(20, bold=True, color=C["navy"]),
        "hero_title": F(22, bold=True, color=C["hero_text"]),
        "hero_sub": F(12, color="CBD5E1"),
        "section": F(14, bold=True, color=C["navy"]),
        "group_hdr": F(11, bold=True, color=C["hero_text"]),
        "col_hdr": F(11, bold=True, color=C["header_text"]),
        "body": F(11, color="1E293B"),
        "body_bold": F(11, bold=True, color=C["navy"]),
        "body_muted": F(10, color=C["muted"]),
        "score": F(12, bold=True, color=C["navy"]),
        "score_avg": F(13, bold=True, color=C["navy"]),
        "kpi_label": F(10, color=C["muted"]),
        "kpi_value": F(18, bold=True, color=C["navy"]),
        "dep_yes": F(11, bold=True, color="15803D"),
        "dep_no": F(11, color="94A3B8"),
    }
    return presets[name]

CATEGORY_LABEL = {
    "anchor": "基准·速影",
    "cloud_clip": "云端·长转短",
    "cloud_edit": "云端·通用剪辑",
    "local_oss": "本地/开源",
    "cn_enterprise": "国内·矩阵企业",
    "scheduler": "发布调度",
    "pro_nle": "专业NLE",
    "ai_gen": "AI生成(正交)",
}

CATEGORY_COLOR = {
    "anchor": "DCFCE7",
    "local_oss": "E0E7FF",
    "cn_enterprise": "FEE2E2",
    "cloud_clip": "DBEAFE",
    "cloud_edit": "FCE7F3",
    "scheduler": "FEF3C7",
    "pro_nle": "E2E8F0",
    "ai_gen": "F3E8FF",
}

DIMENSIONS = [
    ("completeness", "流程完整性", "入库→生产→审片→物料→发布→触达"),
    ("structure", "结构清晰度", "分层/模块化/可运维"),
    ("localIndep", "本地独立性", "断网可用、数据不出机"),
    ("footageMontage", "实拍片库混剪", "客户自有素材语义选片+日更"),
    ("qualityGate", "质量门禁", "审片/打回/硬规则"),
    ("publishPack", "发布物料包", "多平台文案+字幕+口播"),
    ("reachAssist", "发布触达", "半自动发布+消息提醒"),
    ("multiTenant", "多客户/行业", "租户隔离+行业包"),
]

DEP_LABELS = [
    ("ffmpeg", "FFmpeg"),
    ("whisper", "Whisper"),
    ("localLlm", "本地LLM"),
    ("cloudLlm", "云LLM"),
    ("gpu", "GPU"),
    ("browserAuto", "浏览器自动化"),
    ("desktopApp", "桌面应用"),
    ("subscription", "订阅"),
]

DEPLOY_MAP = {"local": "本地", "cloud": "云端", "hybrid": "混合"}
NETWORK_MAP = {"offline_ok": "可离线", "mostly_offline": "主离线", "online_required": "需联网"}

VENDOR_CLAIM_FLAGS = {
    "chaojizhijian": "部分产能/平台数为厂商宣传，未独立压测验证",
    "yimei": "「1分钟混剪1000条」等为官网表述，实际效果因场景而异",
    "superdir": "案例数据来自厂商官网，非第三方审计",
}

THIN = Side(style="thin", color=C["border"])
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
WRAP = Alignment(wrap_text=True, vertical="center")
TOP_WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)


def F(size=11, bold=False, color="000000", name=FONT):
    return Font(name=name, size=size, bold=bold, color=color)


def fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


def parse_products(text: str) -> list[dict]:
    block_m = re.search(r"const PRODUCTS: Product\[\] = \[(.*?)\n\];", text, re.S)
    if not block_m:
        raise RuntimeError("PRODUCTS block not found")
    block = block_m.group(1)
    chunks = re.split(r"\n  \},\n  \{", block)
    products = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            chunk = chunk.lstrip("\n  {")
        else:
            chunk = "{" + chunk
        if i == len(chunks) - 1:
            chunk = chunk.rstrip("\n  }")

        def g(pattern: str, default: str = "") -> str:
            m = re.search(pattern, chunk, re.S)
            return m.group(1).strip() if m else default

        def gb(pattern: str) -> bool:
            m = re.search(pattern, chunk)
            return m.group(1) == "true" if m else False

        def gi(pattern: str) -> int:
            m = re.search(pattern, chunk)
            return int(m.group(1)) if m else 0

        scores = {k: gi(rf"{k}: (\d+)") for k, _, _ in DIMENSIONS}
        deps = {k: gb(rf"{k}: (true|false)") for k, _ in DEP_LABELS}

        def arr_m(key: str) -> list[str]:
            m = re.search(rf"{key}: \[(.*?)\]", chunk, re.S)
            if not m:
                return []
            return [x.strip().strip('"') for x in m.group(1).split(",") if x.strip().strip('"')]

        products.append({
            "id": g(r'id: "([^"]+)"'),
            "name": g(r'name: "([^"]+)"'),
            "vendor": g(r'vendor: "([^"]+)"'),
            "region": g(r'region: "([^"]+)"'),
            "category": g(r'category: "([^"]+)"'),
            "deploy": g(r'deploy: "([^"]+)"'),
            "network": g(r'network: "([^"]+)"'),
            "pricing": g(r'pricing: "([^"]+)"'),
            "scores": scores,
            "deps": deps,
            "pipeline": arr_m("pipeline"),
            "strengths": arr_m("strengths"),
            "gaps": arr_m("gaps"),
            "notes": g(r'notes: "([^"]+)"'),
        })
    return products


def avg_score(p: dict) -> float:
    return round(sum(p["scores"].values()) / len(p["scores"]), 2)


def style_header_row(ws, row: int, cols: int, height: float | None = None):
    ws.row_dimensions[row].height = height or ROW["col_hdr"]
    for c in range(1, cols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = fill(C["header"])
        cell.font = _typo("col_hdr")
        cell.alignment = CENTER
        cell.border = BORDER


def style_group_header(ws, row: int, c1: int, c2: int, title: str):
    ws.row_dimensions[row].height = ROW["group_hdr"]
    ws.merge_cells(start_row=row, start_column=c1, end_row=row, end_column=c2)
    for c in range(c1, c2 + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = fill(C["navy2"])
        cell.border = BORDER
        if c == c1:
            cell.value = title
            cell.font = _typo("group_hdr")
            cell.alignment = CENTER


def apply_col_widths_by_headers(ws, headers: list[str], width_map: dict[str, float], default: float = 12):
    for i, h in enumerate(headers, 1):
        # 表头可能含换行，用第一行匹配
        key = h.replace("\n", "")
        ws.column_dimensions[get_column_letter(i)].width = width_map.get(key, width_map.get(h, default))


def apply_sheet_defaults(ws, zoom=105):
    ws.sheet_view.showGridLines = False
    ws.sheet_view.zoomScale = zoom
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0


def score_color(val) -> str:
    if val is None:
        return C["no"]
    try:
        v = float(val)
    except (TypeError, ValueError):
        return C["no"]
    if v >= 4.5:
        return "86EFAC"
    if v >= 3.5:
        return "BBF7D0"
    if v >= 2.5:
        return "FEF08A"
    if v >= 1.5:
        return "FED7AA"
    return "FECACA"


def build_cover(wb: Workbook, products: list[dict], today: str):
    ws = wb.active
    ws.title = "封面"
    apply_sheet_defaults(ws, 120)

    # Hero banner rows 1-4
    for r in range(1, 5):
        ws.row_dimensions[r].height = 22 if r < 4 else 8
    for r in range(1, 4):
        for c in range(1, 13):
            ws.cell(r, c).fill = fill(C["hero"])

    ws.merge_cells("A1:G3")
    t = ws["A1"]
    t.value = "速影 vs 全球同类产品\n完整性 · 结构 · 依赖 · 功能  —  横屏对比研究"
    t.font = _typo("hero_title")
    t.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 38
    ws.row_dimensions[2].height = 28
    ws.row_dimensions[3].height = 26

    if ICON.exists():
        img = XLImage(str(ICON))
        img.width = 72
        img.height = 72
        ws.add_image(img, "I1")

    ws.merge_cells("J1:L3")
    meta = ws["J1"]
    meta.value = f"生成日期\n{today}\n共 {len(products)} 款产品"
    meta.font = _typo("hero_sub")
    meta.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True)

    # KPI cards row 5
    local_n = sum(1 for p in products if p["deploy"] != "cloud")
    full_n = sum(1 for p in products if avg_score(p) >= 4.0)
    suying = next(p for p in products if p["id"] == "suying")
    kpis = [
        ("收录产品", str(len(products)), C["accent_soft"]),
        ("本地/混合", str(local_n), "E0E7FF"),
        ("全链≥4.0分", str(full_n), "FEF3C7"),
        ("速影均分", str(avg_score(suying)), C["suying"]),
    ]
    col = 1
    for label, val, bg in kpis:
        ws.merge_cells(start_row=5, start_column=col, end_row=5, end_column=col + 2)
        ws.cell(5, col, label).font = _typo("kpi_label")
        ws.cell(5, col).fill = fill(bg)
        ws.cell(5, col).alignment = Alignment(horizontal="center", vertical="center")
        ws.merge_cells(start_row=6, start_column=col, end_row=6, end_column=col + 2)
        ws.cell(6, col, val).font = _typo("kpi_value")
        ws.cell(6, col).fill = fill(bg)
        ws.cell(6, col).alignment = Alignment(horizontal="center", vertical="center")
        for cc in range(col, col + 3):
            for rr in (5, 6):
                ws.cell(rr, cc).fill = fill(bg)
            ws.cell(5, cc).border = Border(
                top=Side(style="medium", color=C["accent"]),
                left=Side(style="medium" if cc == col else "thin", color=C["border"]),
                right=Side(style="thin", color=C["border"]),
            )
            ws.cell(6, cc).border = Border(
                bottom=Side(style="medium", color=C["border"]),
                left=Side(style="medium" if cc == col else "thin", color=C["border"]),
                right=Side(style="thin", color=C["border"]),
            )
        col += 3
    ws.row_dimensions[5].height = 28
    ws.row_dimensions[6].height = 40
    ws.row_dimensions[7].height = 10
    for c in range(1, 13):
        ws.cell(7, c).fill = fill(C["bg"])

    # Info table
    start = 8
    ws.merge_cells(f"A{start}:L{start}")
    ws.cell(start, 1, "说明与使用须知").font = _typo("section")
    ws.cell(start, 1).fill = fill(C["bg"])
    ws.row_dimensions[start].height = ROW["title"]

    headers = ["项目", "说明"]
    hr = start + 1
    ws.cell(hr, 1, headers[0])
    ws.cell(hr, 2, headers[1])
    style_header_row(ws, hr, 12)
    ws.cell(hr, 1).fill = fill(C["header"])
    ws.merge_cells(f"B{hr}:L{hr}")
    for c in range(2, 13):
        ws.cell(hr, c).fill = fill(C["header"])

    notes = [
        ("用途", "横向对比完整性、架构、依赖与功能；供内部选型，非对外销售材料。"),
        ("评分尺度", "0–5 分研究推导分：0 不具备 → 5 强且同赛道对标。非第三方测评。"),
        ("诚实性", "云产品以官网为准；国内部分企业产能数字标注「厂商宣称」未独立验证。"),
        ("速影事实源", "PRODUCT_PLAN.md · V8_SUYING_APP.md · DEV_LOCK.md · REACH_NON_GOALS.md"),
        ("速影定位", "本机「剪辑 + 发布物料 + 辅助触达」工作室；Local-first；人在回路。"),
        ("速影边界", "Reach 半自动（验证码须人工）；macOS 主交付；G7/GSemanticOps 部分验收待完成。"),
        ("导航", "→ 主对比表（核心）· 可视化（图表）· 评分维度说明 · 选型指南 · 速影事实清单"),
    ]
    for i, (label, text) in enumerate(notes, start=hr + 1):
        bg = C["card"] if i % 2 == 0 else C["zebra"]
        ws.row_dimensions[i].height = ROW["cover_note"]
        ws.cell(i, 1, label).font = _typo("body_bold")
        ws.cell(i, 1).fill = fill(bg)
        ws.cell(i, 1).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.cell(i, 1).border = BORDER
        ws.cell(i, 2, text).font = _typo("body")
        ws.cell(i, 2).fill = fill(bg)
        ws.cell(i, 2).alignment = Alignment(wrap_text=True, vertical="center")
        ws.cell(i, 2).border = BORDER
        ws.merge_cells(f"B{i}:L{i}")
        for c in range(3, 13):
            ws.cell(i, c).fill = fill(bg)
            ws.cell(i, c).border = BORDER

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 92
    for letter in "CDEFGHIJKL":
        if letter != "B":
            ws.column_dimensions[letter].width = 11


# 主表维度表头（换行，节省列宽）
DIM_HDR = {
    "流程完整性": "流程\n完整性",
    "结构清晰度": "结构\n清晰度",
    "本地独立性": "本地\n独立性",
    "实拍片库混剪": "实拍\n混剪",
    "质量门禁": "质量\n门禁",
    "发布物料包": "物料\n包",
    "发布触达": "发布\n触达",
    "多客户/行业": "多客户\n行业",
}


def build_main_table(wb: Workbook, products: list[dict]):
    ws = wb.create_sheet("主对比表")
    apply_sheet_defaults(ws, 95)

    meta_cols = 7
    score_cols = len(DIMENSIONS)
    total = meta_cols + 1 + score_cols + 2

    style_group_header(ws, 1, 1, meta_cols, "基本信息")
    style_group_header(ws, 1, meta_cols + 1, meta_cols + 1, "综合")
    style_group_header(ws, 1, meta_cols + 2, meta_cols + 1 + score_cols, "8 维能力评分（0–5）")
    style_group_header(ws, 1, meta_cols + 2 + score_cols, total, "补充")

    headers = [
        "产品", "厂商", "品类", "区域", "部署", "网络", "定价模式", "均分",
        *[DIM_HDR.get(d[1], d[1]) for d in DIMENSIONS],
        "备注", "可信度",
    ]
    for c, h in enumerate(headers, 1):
        ws.cell(2, c, h)
    style_header_row(ws, 2, total)

    sorted_p = _sort_products(products)
    for ri, p in enumerate(sorted_p, start=3):
        flag = VENDOR_CLAIM_FLAGS.get(p["id"], "")
        cred = "文档核实" if p["id"] == "suying" else ("厂商宣称" if flag else "公开资料")
        row_vals = [
            p["name"], p["vendor"],
            CATEGORY_LABEL.get(p["category"], p["category"]),
            p["region"],
            DEPLOY_MAP.get(p["deploy"], p["deploy"]),
            NETWORK_MAP.get(p["network"], p["network"]),
            p["pricing"], avg_score(p),
            *[p["scores"][d[0]] for d in DIMENSIONS],
            p["notes"], cred,
        ]
        ws.row_dimensions[ri].height = ROW["data"]
        is_suying = p["id"] == "suying"
        cat_bg = CATEGORY_COLOR.get(p["category"], C["zebra"])
        zebra = C["card"] if ri % 2 == 0 else C["zebra"]
        for c, val in enumerate(row_vals, 1):
            cell = ws.cell(ri, c, val)
            cell.border = BORDER
            if meta_cols + 2 <= c <= meta_cols + 1 + score_cols:
                cell.alignment = CENTER
                cell.fill = fill(score_color(val))
                cell.font = _typo("score")
            elif c == meta_cols + 1:
                cell.alignment = CENTER
                cell.fill = fill(score_color(val))
                cell.font = _typo("score_avg")
            elif c == 1:
                cell.font = _typo("body_bold") if is_suying else _typo("body")
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
                cell.fill = fill(C["suying"] if is_suying else zebra)
            elif c == 3:
                cell.fill = fill(cat_bg)
                cell.font = _typo("body")
                cell.alignment = CENTER
            elif c in (2, 7, total - 1, total):
                cell.alignment = Alignment(wrap_text=True, vertical="center")
                cell.font = _typo("body_muted") if c == total else _typo("body")
                if cred == "厂商宣称" and c == total:
                    cell.fill = fill(C["gold"])
                else:
                    cell.fill = fill(C["suying"] if is_suying else zebra)
            else:
                cell.alignment = CENTER
                cell.font = _typo("body")
                cell.fill = fill(C["suying"] if is_suying else zebra)

        if is_suying:
            for c in range(1, total + 1):
                ws.cell(ri, c).border = Border(
                    left=Side(style="medium" if c == 1 else "thin", color=C["suying_border"]),
                    right=Side(style="medium" if c == total else "thin", color=C["suying_border"]),
                    top=Side(style="medium", color=C["suying_border"]),
                    bottom=Side(style="medium", color=C["suying_border"]),
                )

    width_list = [
        20, 20, 13, 15, 9, 9, 24, 8,
        11, 11, 11, 12, 10, 11, 10, 11,
        34, 12,
    ]
    for i, w in enumerate(width_list, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:{get_column_letter(total)}{ws.max_row}"


def build_dims_sheet(wb: Workbook):
    ws = wb.create_sheet("评分维度说明")
    apply_sheet_defaults(ws, 100)
    ws.merge_cells("A1:C1")
    ws["A1"] = "8 维评分定义（研究推导 · 非官方标准）"
    ws["A1"].font = _typo("title")
    ws["A1"].fill = fill(C["bg"])
    ws.row_dimensions[1].height = ROW["title"]

    ws.append(["维度", "分值含义", "速影事实锚点"])
    style_header_row(ws, 2, 3)
    dim_facts = {
        "流程完整性": "入库→生产→审片→物料→发布→触达 全链覆盖程度",
        "结构清晰度": "Tauri+引擎分离 · DEV_LOCK 合同 · 运维可观测",
        "本地独立性": "L18 主盘工作区 · 核心成片不依赖云",
        "实拍片库混剪": "客户 coarse/语义 cliplet 选片 · 非文生视频",
        "质量门禁": "READY_GATE · 审片打回 · L0–L20 硬锁",
        "发布物料包": "publish_pack 四平台文案+字幕+口播",
        "发布触达": "队列半自动+Chrome 摘要 · 验证码须人工 → 4 分",
        "多客户/行业": "行业包+客户目录 · ZERO_FORK",
    }
    for i, (_, label, hint) in enumerate(DIMENSIONS, start=3):
        ws.append([label, hint, dim_facts.get(label, "—")])
        bg = C["card"] if i % 2 else C["zebra"]
        ws.row_dimensions[i].height = 48
        for c in range(1, 4):
            cell = ws.cell(i, c)
            cell.fill = fill(bg)
            cell.border = BORDER
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            cell.font = _typo("body_bold") if c == 1 else _typo("body")
    ws.column_dimensions["A"].width = 15
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 50


def _sort_products(products: list[dict]) -> list[dict]:
    return sorted(products, key=lambda x: (-1 if x["id"] == "suying" else 0, -avg_score(x)))


def build_deps_sheet(wb: Workbook, products: list[dict]):
    ws = wb.create_sheet("依赖矩阵")
    apply_sheet_defaults(ws, 90)
    dep_headers = ["产品", "品类", *[d[1] for d in DEP_LABELS], "依赖摘要"]
    ws.append(dep_headers)
    style_header_row(ws, 1, len(dep_headers), height=38)

    for ri, p in enumerate(_sort_products(products), start=2):
        deps = p["deps"]
        summary = " · ".join(l for k, l in DEP_LABELS if deps.get(k))
        row = [p["name"], CATEGORY_LABEL.get(p["category"], p["category"]),
               *["●" if deps.get(k) else "—" for k, _ in DEP_LABELS], summary]
        ws.append(row)
        bg = C["card"] if ri % 2 else C["zebra"]
        ws.row_dimensions[ri].height = ROW["data_compact"]
        for c, val in enumerate(row, 1):
            cell = ws.cell(ri, c)
            cell.border = BORDER
            if c > 2 and c <= 2 + len(DEP_LABELS):
                cell.alignment = CENTER
                cell.fill = fill(C["yes"] if val == "●" else C["no"])
                cell.font = _typo("dep_yes") if val == "●" else _typo("dep_no")
            else:
                cell.fill = fill(bg)
                cell.font = _typo("body_bold") if c == 1 else _typo("body")
                cell.alignment = Alignment(
                    horizontal="left", vertical="center", wrap_text=True,
                )

    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 15
    for i in range(3, 3 + len(DEP_LABELS)):
        ws.column_dimensions[get_column_letter(i)].width = 10
    ws.column_dimensions[get_column_letter(3 + len(DEP_LABELS))].width = 44
    ws.freeze_panes = "C2"


def build_detail_sheet(wb: Workbook, products: list[dict]):
    ws = wb.create_sheet("产品详情")
    apply_sheet_defaults(ws, 95)
    headers = ["产品", "Pipeline", "优势", "缺口", "备注"]
    ws.append(headers)
    style_header_row(ws, 1, 5, height=38)
    for ri, p in enumerate(_sort_products(products), start=2):
        note = p["notes"]
        if p["id"] in VENDOR_CLAIM_FLAGS:
            note += f"（{VENDOR_CLAIM_FLAGS[p['id']]}）"
        ws.append([
            p["name"], " → ".join(p["pipeline"]),
            " · ".join(p["strengths"]), " · ".join(p["gaps"]), note,
        ])
        bg = C["card"] if ri % 2 else C["zebra"]
        ws.row_dimensions[ri].height = ROW["detail"]
        for c in range(1, 6):
            cell = ws.cell(ri, c)
            cell.fill = fill(C["suying"] if p["id"] == "suying" else bg)
            cell.border = BORDER
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            cell.font = _typo("body_bold") if c == 1 and p["id"] == "suying" else (
                _typo("body_bold") if c == 1 else _typo("body")
            )
    ws.column_dimensions["A"].width = 18
    for col, w in zip("BCDE", [38, 34, 34, 38]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"


def build_suying_facts(wb: Workbook):
    ws = wb.create_sheet("速影事实清单")
    apply_sheet_defaults(ws, 100)
    ws.merge_cells("A1:B1")
    ws["A1"] = "速影 · 可核实能力（项目文档原文归纳）"
    ws["A1"].font = Font(name=FONT, size=16, bold=True, color=C["hero_text"])
    ws["A1"].fill = fill(C["hero"])
    ws["A1"].alignment = CENTER
    ws.row_dimensions[1].height = ROW["title"]
    ws.append(["维度", "事实描述"])
    style_header_row(ws, 2, 2, height=36)
    facts = [
        ("架构", "Tauri 桌面 + Python FastAPI 引擎(8766)；App 编排，引擎跑 FFmpeg/Ollama"),
        ("存储", "权威库 ~/Suying/data；媒体 ~/Movies/速影工作区；Local-first L18 冻结"),
        ("页面", "总览 / 生产 / 规则 / 审片 / 发布 / 消息 / 数据 / 运维 / 设置"),
        ("画幅", "竖屏 1080×1920、横屏 1920×1080 双画幅"),
        ("生产", "片库扫描、主题混剪、BGM、READY 门禁、审片打回重渲"),
        ("物料", "publish_pack：video+封面+抖音/视频号/小红书/快手文案+字幕+口播"),
        ("TTS", "可选本地 F5 clone（L19）；Ollama 旁白润色（L20 生活服务冻结）"),
        ("Reach", "发布队列半自动；Chrome 脱敏未读摘要；验证码/登录必须人工"),
        ("明确不做", "绕风控全自动矩阵、自动过验证码、数字人假脸卖点、软文站群"),
        ("多客户", "产品/行业包/客户实例三层；禁止客户名硬编码(ZERO_FORK)"),
        ("验收状态", "Phase1/2/5 等已完成；G7 触达、GSemanticOps 部分真机待验"),
    ]
    for i, (dim, desc) in enumerate(facts, start=3):
        ws.append([dim, desc])
        bg = C["suying"] if i % 2 else C["card"]
        ws.row_dimensions[i].height = 44
        ws.cell(i, 1).fill = fill(C["accent_soft"])
        ws.cell(i, 1).font = _typo("body_bold")
        ws.cell(i, 1).alignment = CENTER
        ws.cell(i, 1).border = BORDER
        ws.cell(i, 2).fill = fill(bg)
        ws.cell(i, 2).font = _typo("body")
        ws.cell(i, 2).alignment = Alignment(wrap_text=True, vertical="center")
        ws.cell(i, 2).border = BORDER
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 92


VIZ_CHART_COL = "E"
VIZ_CHART_ROW_SPAN = 20  # 每个图表预留行高，避免与下一区块重叠


def _viz_section_title(ws, row: int, title: str, merge_to_col: int = 14):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=merge_to_col)
    cell = ws.cell(row, 1, title)
    cell.font = Font(name=FONT, size=14, bold=True, color=C["navy"])
    cell.fill = fill(C["accent_soft"])
    cell.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    cell.border = Border(
        left=Side(style="medium", color=C["accent"]),
        bottom=Side(style="thin", color=C["border"]),
        top=Side(style="thin", color=C["border"]),
        right=Side(style="thin", color=C["border"]),
    )
    ws.row_dimensions[row].height = 34


def _viz_table_header(ws, row: int, headers: list[str]):
    ws.row_dimensions[row].height = 38
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row, c, h)
        cell.fill = fill(C["header"])
        cell.font = _typo("col_hdr")
        cell.alignment = CENTER
        cell.border = BORDER


def _style_data_row(ws, row: int, cols: int, bg: str, height: int = 30):
    ws.row_dimensions[row].height = height
    for c in range(1, cols + 1):
        ws.cell(row, c).border = BORDER


def build_charts(wb: Workbook, products: list[dict]):
    """可视化：左侧数据表 + 右侧图表，区块间留足行距避免重叠."""
    ws = wb.create_sheet("可视化")
    apply_sheet_defaults(ws, 98)
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:N2")
    h = ws["A1"]
    h.value = "数据可视化\n品类分布 · 标杆均分 · 八维热力 · 速影能力剖面"
    h.font = Font(name=FONT, size=20, bold=True, color=C["hero_text"])
    h.fill = fill(C["hero"])
    h.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=2)
    ws.row_dimensions[1].height = 32
    ws.row_dimensions[2].height = 32
    if ICON.exists():
        img = XLImage(str(ICON))
        img.width = 56
        img.height = 56
        ws.add_image(img, "O1")

    cat_counts: dict[str, int] = {}
    for p in products:
        lbl = CATEGORY_LABEL.get(p["category"], p["category"])
        cat_counts[lbl] = cat_counts.get(lbl, 0) + 1
    sorted_cats = sorted(cat_counts.items(), key=lambda x: -x[1])

    benchmark_ids = [
        "suying", "chaojizhijian", "superdir", "trendcut",
        "opus", "reframe", "descript", "matrixmedia",
    ]
    bench = [p for pid in benchmark_ids for p in products if p["id"] == pid]
    suying_p = next(p for p in products if p["id"] == "suying")

    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 11
    for letter in "EFGHIJKLMN":
        ws.column_dimensions[letter].width = 14

    row = 4  # 内容区起始行（row 3 留白）

    # ── 一、品类分布 + 饼图 ───────────────────────────────────
    _viz_section_title(ws, row, "一、收录产品品类分布")
    row += 1
    cat_hdr = row
    _viz_table_header(ws, cat_hdr, ["品类", "数量"])
    row += 1
    cat_start = row
    for i, (lbl, cnt) in enumerate(sorted_cats, start=cat_start):
        bg = C["card"] if i % 2 == 0 else C["zebra"]
        _style_data_row(ws, i, 2, bg, 30)
        ws.cell(i, 1, lbl).font = _typo("body")
        ws.cell(i, 1).fill = fill(bg)
        ws.cell(i, 1).alignment = Alignment(horizontal="left", vertical="center", indent=1)
        ws.cell(i, 2, cnt).font = _typo("body_bold")
        ws.cell(i, 2).fill = fill(bg)
        ws.cell(i, 2).alignment = CENTER
    cat_end = cat_start + len(sorted_cats) - 1

    pie = PieChart()
    pie.title = "品类占比"
    pie.style = 26
    pie.height = 12
    pie.width = 16
    pie.add_data(
        Reference(ws, min_col=2, min_row=cat_start, max_row=cat_end),
        titles_from_data=False,
    )
    pie.set_categories(Reference(ws, min_col=1, min_row=cat_start, max_row=cat_end))
    pie.dataLabels = DataLabelList()
    pie.dataLabels.showPercent = True
    pie.dataLabels.showCatName = False
    pie.dataLabels.showLegendKey = False
    ws.add_chart(pie, f"{VIZ_CHART_COL}{cat_hdr}")

    row = max(cat_end, cat_hdr + VIZ_CHART_ROW_SPAN) + 2

    # ── 二、标杆均分 + 横向条形图 ─────────────────────────────
    _viz_section_title(ws, row, "二、标杆产品综合均分（8 维平均）")
    row += 1
    t2 = row
    _viz_table_header(ws, t2, ["产品", "均分"])
    row += 1
    data2_start = row
    for j, p in enumerate(bench, start=data2_start):
        bg = C["suying"] if p["id"] == "suying" else (C["card"] if j % 2 == 0 else C["zebra"])
        _style_data_row(ws, j, 2, bg, 32)
        ws.cell(j, 1, p["name"]).font = _typo("body_bold") if p["id"] == "suying" else _typo("body")
        ws.cell(j, 1).fill = fill(bg)
        ws.cell(j, 1).alignment = Alignment(horizontal="left", vertical="center", indent=1)
        av = avg_score(p)
        c2 = ws.cell(j, 2, av)
        c2.font = _typo("score_avg")
        c2.fill = fill(score_color(av))
        c2.alignment = CENTER
    data2_end = data2_start + len(bench) - 1

    bar = BarChart()
    bar.type = "bar"
    bar.title = "综合均分对比（0–5）"
    bar.style = 26
    bar.height = 12
    bar.width = 16
    bar.x_axis.title = "均分"
    bar.add_data(
        Reference(ws, min_col=2, min_row=data2_start, max_row=data2_end),
        titles_from_data=False,
    )
    bar.set_categories(Reference(ws, min_col=1, min_row=data2_start, max_row=data2_end))
    ws.add_chart(bar, f"{VIZ_CHART_COL}{t2}")

    row = max(data2_end, t2 + VIZ_CHART_ROW_SPAN) + 2

    # ── 三、八维热力（全宽表格，无并排图表）────────────────────
    _viz_section_title(ws, row, "三、标杆产品八维能力评分")
    row += 1
    hdr3 = row
    dim_headers = ["产品"] + [DIM_HDR.get(d[1], d[1]) for d in DIMENSIONS]
    ws.row_dimensions[hdr3].height = 46
    for c, ht in enumerate(dim_headers, 1):
        cell = ws.cell(hdr3, c, ht)
        cell.fill = fill(C["header"])
        cell.font = _typo("col_hdr")
        cell.alignment = CENTER
        cell.border = BORDER
        w = 20 if c == 1 else 10
        ws.column_dimensions[get_column_letter(c)].width = max(
            ws.column_dimensions[get_column_letter(c)].width or 0, w,
        )
    row += 1
    data3_start = row
    for j, p in enumerate(bench, start=data3_start):
        _style_data_row(ws, j, len(dim_headers), C["zebra"], 34)
        is_su = p["id"] == "suying"
        row_bg = C["suying"] if is_su else (C["card"] if j % 2 == 0 else C["zebra"])
        ws.cell(j, 1, p["name"]).font = _typo("body_bold") if is_su else _typo("body")
        ws.cell(j, 1).fill = fill(row_bg)
        ws.cell(j, 1).alignment = Alignment(horizontal="left", vertical="center", indent=1)
        for ii, (k, _, _) in enumerate(DIMENSIONS, start=2):
            v = p["scores"][k]
            cell = ws.cell(j, ii, v)
            cell.fill = fill(score_color(v))
            cell.font = _typo("score")
            cell.alignment = CENTER
    data3_end = data3_start + len(bench) - 1
    row = data3_end + 2

    # ── 四、速影八维剖面 + 柱形图 ─────────────────────────────
    _viz_section_title(ws, row, "四、速影 Studio · 八维能力剖面")
    row += 1
    t4 = row
    _viz_table_header(ws, t4, ["维度", "评分"])
    row += 1
    d4_start = row
    short_dims = [DIM_HDR.get(d[1], d[1]).replace("\n", "") for d in DIMENSIONS]
    for i, label in enumerate(short_dims, start=d4_start):
        bg = C["suying"] if i % 2 == 0 else C["accent_soft"]
        _style_data_row(ws, i, 2, bg, 30)
        k = DIMENSIONS[i - d4_start][0]
        v = suying_p["scores"][k]
        ws.cell(i, 1, label).font = _typo("body_bold")
        ws.cell(i, 1).fill = fill(bg)
        ws.cell(i, 1).alignment = Alignment(horizontal="left", vertical="center", indent=1)
        c2 = ws.cell(i, 2, v)
        c2.font = _typo("score")
        c2.fill = fill(score_color(v))
        c2.alignment = CENTER
    d4_end = d4_start + len(DIMENSIONS) - 1

    col_chart = BarChart()
    col_chart.type = "col"
    col_chart.title = "速影 · 八维评分"
    col_chart.style = 26
    col_chart.height = 12
    col_chart.width = 18
    col_chart.y_axis.title = "分"
    col_chart.add_data(
        Reference(ws, min_col=2, min_row=d4_start, max_row=d4_end),
        titles_from_data=False,
    )
    col_chart.set_categories(Reference(ws, min_col=1, min_row=d4_start, max_row=d4_end))
    ws.add_chart(col_chart, f"{VIZ_CHART_COL}{t4}")

    leg = max(d4_end, t4 + VIZ_CHART_ROW_SPAN) + 2
    ws.merge_cells(f"A{leg}:J{leg}")
    ws.cell(
        leg, 1,
        "色阶：≥4.5 深绿  ·  3.5–4.4 浅绿  ·  2.5–3.4 黄  ·  1.5–2.4 橙  ·  <1.5 红"
        "  ｜  图表数据即左侧表格，与「主对比表」同源",
    ).font = _typo("body_muted")
    ws.cell(leg, 1).fill = fill(C["bg"])
    ws.cell(leg, 1).alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[leg].height = 28

    ws.freeze_panes = "A4"


def build_guide(wb: Workbook):
    ws = wb.create_sheet("选型指南")
    apply_sheet_defaults(ws)
    ws.merge_cells("A1:C1")
    ws["A1"] = "场景选型指南（推导结论 · 非绝对排名）"
    ws["A1"].font = Font(name=FONT, size=16, bold=True, color=C["hero_text"])
    ws["A1"].fill = fill(C["hero"])
    ws["A1"].alignment = CENTER
    ws.row_dimensions[1].height = ROW["title"]

    guide = [
        ("需求场景", "更匹配方向", "说明"),
        ("实拍片库日更 + 可签字质量 + 本地交付", "速影 Studio", "全链同构竞品极少；需本机运维 Ollama/引擎"),
        ("长 podcast / webinar 云 repurposing", "Opus Clip / Vizard / Reap", "上传长片→Shorts；质量门禁弱"),
        ("国内矩阵跑量 + 云端协同", "超级编导", "批量混剪+矩阵分发；数据在 vendor 云"),
        ("企业级大规模矩阵 + 私有化", "超级智剪", "价格高；部分指标为厂商宣传，需 POC"),
        ("仅多平台分发、不做生产", "矩媒 / 乌拉 / Buffer", "发布自动化；与速影 Reach 层部分重叠"),
        ("开源可二开全链", "TrendCut Studio", "Node+Vue+Python；依赖 ComfyUI/Playwright"),
        ("单条口播 / 播客精修", "Descript", "转录驱动；非批量日更运营"),
        ("单条视觉 polish", "CapCut / 剪映", "人工创意编辑；免费层厚"),
        ("专业调色 / 精修终点", "DaVinci Resolve", "与速影互补；无运营闭环"),
        ("数字人 / 文生视频", "HeyGen / InVideo", "与速影实拍路径正交"),
    ]
    for i, row in enumerate(guide, start=2):
        for j, val in enumerate(row, 1):
            ws.cell(i, j, val)
        if i == 2:
            style_header_row(ws, i, 3, 32)
        else:
            bg = C["card"] if i % 2 else C["zebra"]
            highlight = "速影" in str(ws.cell(i, 2).value)
            for j in range(1, 4):
                cell = ws.cell(i, j)
                cell.fill = fill(C["suying"] if highlight else bg)
                cell.border = BORDER
                cell.alignment = Alignment(wrap_text=True, vertical="center")
                cell.font = _typo("body_bold") if j == 2 and highlight else (
                    _typo("body_bold") if j == 1 else _typo("body")
                )
            ws.row_dimensions[i].height = 46
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 28
    ws.column_dimensions["C"].width = 52


def build_sources(wb: Workbook):
    ws = wb.create_sheet("数据来源")
    apply_sheet_defaults(ws)
    ws.append(["类型", "来源", "访问 / 路径"])
    style_header_row(ws, 1, 3, height=36)
    sources = [
        ("速影产品", "PRODUCT_PLAN.md", "~/QR/dev/速影/docs/PRODUCT_PLAN.md"),
        ("速影 App", "V8_SUYING_APP.md", "~/QR/dev/速影/docs/V8_SUYING_APP.md"),
        ("速影触达边界", "REACH_NON_GOALS.md", "~/QR/dev/速影/docs/REACH_NON_GOALS.md"),
        ("Canvas 产品库", "suying-competitor-landscape.canvas.tsx", "~/.cursor/projects/.../canvases/"),
        ("Opus Clip", "官网", "https://www.opus.pro/"),
        ("Vizard", "官网", "https://vizard.ai/"),
        ("Descript", "官网", "https://www.descript.com/"),
        ("Reframe", "GitHub", "https://github.com/Prekzursil/Reframe"),
        ("MatrixMedia", "GitHub", "https://github.com/OmnipotentAI/MatrixMedia"),
        ("超级编导", "官网", "https://www.superdir.cn/"),
        ("易媒助手", "官网", "https://www.yimeizhushou.com/"),
    ]
    for row in sources:
        ws.append(list(row))
        r = ws.max_row
        bg = C["card"] if r % 2 else C["zebra"]
        for c in range(1, 4):
            cell = ws.cell(r, c)
            cell.fill = fill(bg)
            cell.border = BORDER
            cell.alignment = TOP_WRAP
            cell.font = _typo("body")
        url = row[2]
        if url.startswith("http"):
            ws.cell(r, 3).hyperlink = url
            ws.cell(r, 3).font = Font(name=FONT, size=11, color=C["accent"], underline="single")
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 30
    ws.column_dimensions["C"].width = 54


def set_tab_colors(wb: Workbook):
    tabs = {
        "封面": "0F2744",
        "主对比表": "2563EB",
        "评分维度说明": "64748B",
        "依赖矩阵": "7C3AED",
        "产品详情": "0891B2",
        "速影事实清单": "16A34A",
        "可视化": "EA580C",
        "选型指南": "CA8A04",
        "数据来源": "475569",
    }
    for sheet in wb.worksheets:
        if sheet.title in tabs:
            sheet.sheet_properties.tabColor = tabs[sheet.title]


def build_workbook(products: list[dict]) -> Workbook:
    wb = Workbook()
    today = date.today().isoformat()
    build_cover(wb, products, today)
    build_dims_sheet(wb)
    build_main_table(wb, products)
    build_deps_sheet(wb, products)
    build_detail_sheet(wb, products)
    build_suying_facts(wb)
    build_charts(wb, products)
    build_guide(wb)
    build_sources(wb)
    set_tab_colors(wb)
    return wb


def validate_workbook(wb: Workbook, products: list[dict]) -> list[str]:
    """生成后自检，返回问题列表."""
    issues: list[str] = []
    if len(products) != 42:
        issues.append(f"产品数异常: {len(products)}")
    ws = wb["主对比表"]
    if ws.max_row != len(products) + 2:
        issues.append(f"主对比表行数 {ws.max_row} != {len(products) + 2}")
    if ws.cell(3, 1).value != "速影 Studio":
        issues.append("主对比表首行应为速影 Studio")
    ws_v = wb["可视化"]
    if len(ws_v._charts) != 3:
        issues.append(f"可视化图表数 {len(ws_v._charts)} != 3")
    sections = []
    for r in range(1, ws_v.max_row + 1):
        v = ws_v.cell(r, 1).value
        if isinstance(v, str) and v.startswith(("一、", "二、", "三、", "四、")):
            sections.append(r)
    if len(sections) != 4:
        issues.append(f"可视化区块数 {len(sections)} != 4")
    elif sections != sorted(sections):
        issues.append("可视化区块顺序错乱")
    # 图表与下一区块标题不得重叠（锚点行 < 下一区块 - 2）
    chart_rows: list[int] = []
    for ch in ws_v._charts:
        anchor = getattr(ch, "anchor", None)
        if anchor is not None and hasattr(anchor, "_from"):
            chart_rows.append(anchor._from.row + 1)
    for i, sec in enumerate(sections[1:], start=1):
        if i - 1 < len(chart_rows) and chart_rows[i - 1] + VIZ_CHART_ROW_SPAN >= sec:
            issues.append(
                f"图表 {i} 可能与区块 {i + 1} 重叠 (chart@{chart_rows[i - 1]}, sec@{sec})",
            )
    ws_c = wb["封面"]
    if ws_c.cell(7, 1).value not in (None, ""):
        issues.append("封面第 7 行应为留白")
    return issues


def main():
    products = parse_products(CANVAS.read_text(encoding="utf-8"))
    wb = build_workbook(products)
    issues = validate_workbook(wb, products)
    if issues:
        print("WARN:", "; ".join(issues))
    wb.save(OUT)
    print(f"Saved: {OUT}  ({len(products)} products)")


if __name__ == "__main__":
    main()
