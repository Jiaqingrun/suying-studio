#!/usr/bin/env python3
"""Render 速影客户向产品介绍 Markdown → A4 PDF（左对齐，禁止中文两端对齐拉空格）。"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

HERE = Path(__file__).resolve().parent
CONTRACT = HERE / "render_sales_contract_pdf.py"
SRC = HERE.parents[1] / "docs/legal/PRODUCT_INTRODUCTION.zh-CN.md"
OUT_DEFAULT = HERE.parents[1] / "docs/legal/速影Studio-产品介绍.pdf"


def _load_contract():
    spec = importlib.util.spec_from_file_location("suying_contract_pdf", CONTRACT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {CONTRACT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_table(mod, rows: list[list[str]], styles: dict, col_count: int) -> Table:
    usable = 170 * mm
    if col_count <= 0:
        col_count = 1
    if col_count == 2:
        widths = [46 * mm, usable - 46 * mm]
    elif col_count == 3:
        widths = [32 * mm, 52 * mm, usable - 84 * mm]
    elif col_count == 4:
        widths = [36 * mm, 42 * mm, 46 * mm, usable - 124 * mm]
    elif col_count == 5:
        widths = [30 * mm, 36 * mm, 34 * mm, 34 * mm, usable - 134 * mm]
    else:
        widths = [usable / col_count] * col_count

    data = []
    for i, row in enumerate(rows):
        cells = (row + [""] * col_count)[:col_count]
        style = styles["th"] if i == 0 else styles["td"]
        data.append(
            [
                mod.fitted_paragraph(c, style, widths[j] - 8) if c.strip() else Paragraph(" ", style)
                for j, c in enumerate(cells)
            ]
        )

    tbl = Table(data, colWidths=widths, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), mod.HEAD_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), mod.white),
        ("FONTNAME", (0, 0), (-1, -1), mod.FONT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.3, mod.LINE),
    ]
    for r in range(1, len(data)):
        if r % 2 == 0:
            cmds.append(("BACKGROUND", (0, r), (-1, r), mod.CELL_ALT))
    tbl.setStyle(TableStyle(cmds))
    return tbl


def add_page(mod, canvas, doc) -> None:
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(mod.HEAD_BG)
    canvas.rect(0, h - 12 * mm, w, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(mod.white)
    canvas.setFont(mod.FONT, 8)
    canvas.drawString(18 * mm, h - 7.5 * mm, "速影 Studio  ·  产品介绍")
    canvas.drawRightString(w - 18 * mm, h - 7.5 * mm, "对照本机代码与权威库  ·  非正式合同")

    canvas.setFillColor(mod.HEAD_BG)
    canvas.rect(0, 0, w, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(mod.white)
    canvas.setFont(mod.FONT, 8)
    canvas.drawString(18 * mm, 5 * mm, "权利义务以已签署合同为准")
    canvas.drawRightString(w - 18 * mm, 5 * mm, f"第{doc.page}页")
    canvas.restoreState()


def render(out: Path | None = None) -> Path:
    mod = _load_contract()
    mod.register_font()
    styles = mod.build_styles()
    md = SRC.read_text(encoding="utf-8")
    original = mod.make_table

    def _patched(rows, styles_arg, col_count):
        return make_table(mod, rows, styles_arg, col_count)

    mod.make_table = _patched
    try:
        story = mod.parse_flow(md, styles)
    finally:
        mod.make_table = original

    out_path = out or OUT_DEFAULT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="速影 Studio 产品介绍",
        author="速影",
        subject="客户向产品介绍 · 版本0.8.50 · 非正式合同",
    )
    doc.build(
        story,
        onFirstPage=lambda canvas, d: add_page(mod, canvas, d),
        onLaterPages=lambda canvas, d: add_page(mod, canvas, d),
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="渲染速影客户向产品介绍 PDF。")
    parser.add_argument("--out", type=Path, help="自定义输出PDF路径。")
    args = parser.parse_args()
    out = render(out=args.out)
    print(out)


if __name__ == "__main__":
    main()
