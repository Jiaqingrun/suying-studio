#!/usr/bin/env python3
"""Render 速影销售合同 Markdown → A4 PDF（左对齐，禁止中文两端对齐拉空格）。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

SRC = Path(__file__).resolve().parents[2] / "docs/legal/SALES_CONTRACT_速影Studio.zh-CN.md"
OUT_TEMPLATE = Path.home() / "Desktop" / "速影Studio-软件许可及技术服务合同.pdf"
OUT_FORMAL = Path.home() / "Desktop" / "速影Studio-软件许可及技术服务合同-正式签署版.pdf"
FONT_PATH = Path.home() / "Library/Fonts/NotoSansSC.ttf"
FONT = "NotoSansSC"

INK = HexColor("#1A1A1A")
MUTED = HexColor("#5C564E")
LINE = HexColor("#D9D0C4")
ACCENT = HexColor("#8B3A1A")
HEAD_BG = HexColor("#2C241E")
CELL_ALT = HexColor("#FAF6F0")
QUOTE_BG = HexColor("#F3EDE3")

_CJK = r"\u4e00-\u9fff"
_NUM = "壹贰叁肆伍陆柒捌玖拾一二三四五六七八九十两零百千万亿"
_UNIT = "份条项款年月日人台套次本章节卷本号"
_CANNOT_START = "，。；：、）】》」』！？,.;:)]}%°"
_CANNOT_END = "（【《「『([{“‘"
BODY_WIDTH = 170 * mm
QUOTE_WIDTH = 158 * mm
LI_WIDTH = 160 * mm

_ATOM_RE = re.compile(
    # 强制“整体”token，避免折行把标记符号切开
    r"\*\*[^*\n]+\*\*"  # **加粗内容**
    r"|_{2,}"  # 一串下划线
    # 金额/量词类：数字+量词不拆
    r"|一式(?:\*\*)?[" + _NUM + r"]+(?:\*\*)?份"
    r"|各执(?:\*\*)?[" + _NUM + r"]+(?:\*\*)?份"
    r"|(?:\*\*)?[" + _NUM + r"]+(?:\*\*)?[" + _UNIT + r"]"
    r"|第?\d+(?:\.\d+)*条?"
    r"|[A-Za-z][A-Za-z0-9_\-]*"
    r"|v?\d+\.\d+(?:\.\d+)*"
    r"|\s+"
    r"|."
)


def register_font() -> None:
    pdfmetrics.registerFont(TTFont(FONT, str(FONT_PATH)))


def tighten_cjk_ascii(text: str) -> str:
    """去掉中文与数字/英文之间可被拉长的空格，并粘住数字+量词。"""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(rf"(?<=[{_CJK}])[ \t]+(?=[\w\(（【《「])", "", text)
    text = re.sub(rf"(?<=[\w\)）】》」%])[ \t]+(?=[{_CJK}])", "", text)
    text = re.sub(r"[ \t]+\*\*", "**", text)
    text = re.sub(r"\*\*[ \t]+", "**", text)
    text = re.sub(rf"(?<=[{_NUM}\d])[ \t]+(?=[{_UNIT}])", "", text)
    text = re.sub(rf"(?<=[{_CJK}])[ \t]+(?=[，。；：、）】》」』！？,.;:)\]}}%°])", "", text)
    text = re.sub(rf"(?<=[（【《「『(\[{{“‘])[ \t]+(?=[{_CJK}\w])", "", text)
    return text.strip()


def _visible_atom(atom: str) -> str:
    # markdown delimiter strips for width/kinsoku decisions
    if atom == "**":
        return ""
    if atom.startswith("**") and atom.endswith("**") and len(atom) >= 4:
        return atom[2:-2]
    if atom.startswith("*") and atom.endswith("*") and len(atom) >= 2:
        # 单星斜体（避免把 **...** 再处理一遍）
        if not (atom.startswith("**") or atom.endswith("**")):
            return atom[1:-1]
    return atom


def _visible_text(atoms: list[str]) -> str:
    return "".join(_visible_atom(a) for a in atoms)


def wrap_cjk_lines(text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    """按显示宽度折行：数字+量词不拆；闭标点不出现在行首。"""
    if not text:
        return [""]
    atoms = [m.group(0) for m in _ATOM_RE.finditer(text)]

    def width_of(parts: list[str]) -> float:
        return pdfmetrics.stringWidth(_visible_text(parts), font_name, font_size)

    def first_char(parts: list[str]) -> str:
        vis = _visible_text(parts)
        return vis[0] if vis else ""

    def last_char(parts: list[str]) -> str:
        vis = _visible_text(parts)
        return vis[-1] if vis else ""

    def take_first_content(parts: list[str]) -> list[str]:
        moved: list[str] = []
        while parts:
            atom = parts.pop(0)
            moved.append(atom)
            if _visible_atom(atom):
                break
        return moved

    def take_last_content(parts: list[str]) -> list[str]:
        moved: list[str] = []
        while parts:
            atom = parts.pop()
            moved.insert(0, atom)
            if _visible_atom(atom):
                break
        return moved

    lines: list[list[str]] = []
    current: list[str] = []
    for atom in atoms:
        trial = current + [atom]
        if current and width_of(trial) > max_width:
            lines.append(current)
            current = [atom]
        else:
            current = trial
    if current:
        lines.append(current)

    changed = True
    for _ in range(32):
        if not changed:
            break
        changed = False
        i = 1
        while i < len(lines):
            if first_char(lines[i]) in _CANNOT_START:
                moved = take_first_content(lines[i])
                lines[i - 1].extend(moved)
                if not _visible_text(lines[i]):
                    lines.pop(i)
                changed = True
                continue
            i += 1
        i = 0
        while i < len(lines) - 1:
            if last_char(lines[i]) in _CANNOT_END and len(_visible_text(lines[i])) > 1:
                moved = take_last_content(lines[i])
                lines[i + 1] = moved + lines[i + 1]
                if not _visible_text(lines[i]):
                    lines.pop(i)
                    changed = True
                    continue
                changed = True
            i += 1

    return ["".join(part) for part in lines if _visible_text(part)]


def fitted_paragraph(plain: str, style: ParagraphStyle, max_width: float) -> Paragraph:
    lines = wrap_cjk_lines(tighten_cjk_ascii(plain), style.fontName, style.fontSize, max_width)
    html = "<br/>".join(md_inline(line) for line in lines) or "&nbsp;"
    return Paragraph(html, style)


def md_inline(text: str) -> str:
    text = tighten_cjk_ascii(text)
    text = re.sub(rf"(?<=[{_CJK}])[ \t]+(?=\*\*)", "", text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(rf"(?<=[{_CJK}])[ \t]+(?=<)", "", text)
    return text


def split_md_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def is_sep_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells)


def load_body(formal: bool = False) -> str:
    raw = SRC.read_text(encoding="utf-8")
    if formal:
        raw = re.sub(r"\n(?:>.*\n)+", "\n", raw, count=1)
    cut = re.search(r"^## 使用提示", raw, re.M)
    if cut:
        raw = raw[: cut.start()].rstrip() + "\n"
    return raw


def build_styles() -> dict[str, ParagraphStyle]:
    return {
        "cover": ParagraphStyle(
            "cover",
            fontName=FONT,
            fontSize=18,
            leading=26,
            alignment=TA_CENTER,
            textColor=HEAD_BG,
            spaceAfter=6,
        ),
        "h1": ParagraphStyle(
            "h1",
            fontName=FONT,
            fontSize=13,
            leading=20,
            textColor=HEAD_BG,
            spaceBefore=14,
            spaceAfter=8,
            alignment=TA_LEFT,
        ),
        "h2": ParagraphStyle(
            "h2",
            fontName=FONT,
            fontSize=11,
            leading=16,
            textColor=ACCENT,
            spaceBefore=10,
            spaceAfter=6,
            alignment=TA_LEFT,
        ),
        "body": ParagraphStyle(
            "body",
            fontName=FONT,
            fontSize=9.5,
            leading=15.5,
            alignment=TA_LEFT,
            textColor=INK,
            spaceBefore=2,
            spaceAfter=4,
            splitLongWords=0,
        ),
        "quote": ParagraphStyle(
            "quote",
            fontName=FONT,
            fontSize=8.5,
            leading=13,
            textColor=MUTED,
            leftIndent=6,
            alignment=TA_LEFT,
            spaceBefore=1,
            spaceAfter=1,
            splitLongWords=0,
        ),
        "li": ParagraphStyle(
            "li",
            fontName=FONT,
            fontSize=9.5,
            leading=15,
            textColor=INK,
            alignment=TA_LEFT,
            splitLongWords=0,
        ),
        "th": ParagraphStyle(
            "th",
            fontName=FONT,
            fontSize=8,
            leading=12,
            textColor=white,
            alignment=TA_LEFT,
            splitLongWords=0,
        ),
        "td": ParagraphStyle(
            "td",
            fontName=FONT,
            fontSize=8,
            leading=12,
            textColor=INK,
            alignment=TA_LEFT,
            splitLongWords=0,
        ),
    }


def is_sign_table(rows: list[list[str]]) -> bool:
    blob = "".join(c for row in rows[:2] for c in row)
    return "盖章" in blob and "甲方" in blob and "乙方" in blob


def make_sign_block(styles: dict[str, ParagraphStyle]) -> Table:
    """甲乙双方左右分栏，各自预留盖章与签字行，互不挤占。"""
    title_s = ParagraphStyle(
        "sign_title",
        parent=styles["h2"],
        spaceBefore=0,
        spaceAfter=2,
        alignment=TA_LEFT,
    )
    hint_s = ParagraphStyle(
        "sign_hint",
        parent=styles["quote"],
        leftIndent=0,
        alignment=TA_CENTER,
        textColor=MUTED,
        spaceBefore=0,
        spaceAfter=0,
    )
    line_s = ParagraphStyle(
        "sign_line",
        parent=styles["body"],
        spaceBefore=4,
        spaceAfter=8,
        splitLongWords=0,
    )
    col_w = 82 * mm

    def party(title: str) -> list:
        return [
            Paragraph(f"<b>{title}</b>", title_s),
            Paragraph("（盖章处）", hint_s),
            Spacer(1, 40 * mm),
            Paragraph("授权代表签字：________________", line_s),
            Paragraph("职务：________________", line_s),
            Paragraph("日期：______年______月______日", line_s),
        ]

    tbl = Table(
        [[party("甲方（盖章）"), "", party("乙方（盖章）")]],
        colWidths=[col_w, 6 * mm, col_w],
    )
    tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (0, 0), CELL_ALT),
                ("BACKGROUND", (2, 0), (2, 0), CELL_ALT),
                ("BOX", (0, 0), (0, 0), 0.6, LINE),
                ("BOX", (2, 0), (2, 0), 0.6, LINE),
                ("LEFTPADDING", (0, 0), (0, 0), 10),
                ("RIGHTPADDING", (0, 0), (0, 0), 10),
                ("LEFTPADDING", (2, 0), (2, 0), 10),
                ("RIGHTPADDING", (2, 0), (2, 0), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ("LEFTPADDING", (1, 0), (1, 0), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ]
        )
    )
    return tbl


def make_table(rows: list[list[str]], styles: dict[str, ParagraphStyle], col_count: int) -> Table:
    usable = 170 * mm
    if col_count <= 0:
        col_count = 1
    if col_count == 2:
        widths = [42 * mm, usable - 42 * mm]
    elif col_count == 3:
        widths = [28 * mm, 36 * mm, usable - 64 * mm]
    elif col_count == 4:
        widths = [22 * mm, 48 * mm, 28 * mm, usable - 98 * mm]
    else:
        widths = [usable / col_count] * col_count

    data = []
    for i, row in enumerate(rows):
        cells = (row + [""] * col_count)[:col_count]
        style = styles["th"] if i == 0 else styles["td"]
        data.append(
            [
                fitted_paragraph(c, style, widths[j] - 8) if c.strip() else Paragraph(" ", style)
                for j, c in enumerate(cells)
            ]
        )

    tbl = Table(data, colWidths=widths, repeatRows=1)
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, LINE),
    ]
    for r in range(1, len(data)):
        if r % 2 == 0:
            cmds.append(("BACKGROUND", (0, r), (-1, r), CELL_ALT))
    tbl.setStyle(TableStyle(cmds))
    return tbl


def emit_list(flow: list, items: list[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> None:
    if not items:
        return
    kind = items[0][0]
    flowable_items = [
        ListItem(
            fitted_paragraph(text, styles["li"], LI_WIDTH),
            leftIndent=6,
            bulletColor=INK,
        )
        for _, text in items
    ]
    flow.append(
        ListFlowable(
            flowable_items,
            bulletType="1" if kind == "num" else "bullet",
            start="1" if kind == "num" else "•",
            leftIndent=10,
            bulletFontName=FONT,
            bulletFontSize=9,
            spaceBefore=2,
            spaceAfter=6,
        )
    )


def parse_flow(md: str, styles: dict[str, ParagraphStyle]) -> list:
    lines = md.splitlines()
    flow: list = []
    i = 0
    para_buf: list[str] = []
    quote_buf: list[str] = []
    list_buf: list[tuple[str, str]] = []

    def flush_para() -> None:
        nonlocal para_buf
        if not para_buf:
            return
        text = "".join(para_buf)
        para_buf = []
        if text.strip():
            flow.append(fitted_paragraph(text, styles["body"], BODY_WIDTH))

    def flush_quote() -> None:
        nonlocal quote_buf
        if not quote_buf:
            return
        inner = [fitted_paragraph(q, styles["quote"], QUOTE_WIDTH) for q in quote_buf if q.strip()]
        quote_buf = []
        if not inner:
            return
        box = Table([[inner]], colWidths=[170 * mm])
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), QUOTE_BG),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("BOX", (0, 0), (-1, -1), 0.4, LINE),
                ]
            )
        )
        flow.append(Spacer(1, 4))
        flow.append(box)
        flow.append(Spacer(1, 6))

    def flush_list() -> None:
        nonlocal list_buf
        if not list_buf:
            return
        chunk: list[tuple[str, str]] = []
        current = list_buf[0][0]
        for kind, text in list_buf:
            if kind != current:
                emit_list(flow, chunk, styles)
                chunk = []
                current = kind
            chunk.append((kind, text))
        emit_list(flow, chunk, styles)
        list_buf = []

    def flush_all() -> None:
        flush_para()
        flush_quote()
        flush_list()

    first_h1 = True
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_all()
            table_lines = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            rows = [split_md_row(t) for t in table_lines]
            rows = [r for r in rows if not is_sep_row(r)]
            if rows:
                flow.append(Spacer(1, 4))
                if is_sign_table(rows):
                    flow.append(make_sign_block(styles))
                else:
                    n = max(len(r) for r in rows)
                    flow.append(make_table(rows, styles, n))
                flow.append(Spacer(1, 8))
            continue

        if stripped == "---":
            flush_all()
            flow.append(Spacer(1, 6))
            flow.append(HRFlowable(width="100%", thickness=0.6, color=LINE, spaceAfter=8))
            i += 1
            continue

        if stripped.startswith("# "):
            flush_all()
            title = stripped[2:].strip()
            if first_h1:
                flow.append(Spacer(1, 8 * mm))
                flow.append(Paragraph(md_inline(title), styles["cover"]))
                flow.append(Spacer(1, 4 * mm))
                first_h1 = False
            else:
                if title.startswith("附件"):
                    flow.append(PageBreak())
                flow.append(Paragraph(md_inline(title), styles["h1"]))
                if title.startswith("附件"):
                    flow.append(HRFlowable(width="100%", thickness=0.8, color=ACCENT, spaceAfter=8))
            i += 1
            continue

        if stripped.startswith("## "):
            flush_all()
            heading = stripped[3:].strip()
            if heading.startswith("附件") or heading == "签署页":
                flow.append(PageBreak())
            flow.append(Paragraph(md_inline(heading), styles["h1"]))
            flow.append(HRFlowable(width="100%", thickness=0.8, color=ACCENT, spaceAfter=8))
            i += 1
            continue

        if stripped.startswith("### "):
            flush_all()
            flow.append(Paragraph(md_inline(stripped[4:].strip()), styles["h2"]))
            i += 1
            continue

        if stripped.startswith(">"):
            flush_para()
            flush_list()
            quote_buf.append(stripped.lstrip("> ").strip())
            i += 1
            continue

        m_check = re.match(r"^- \[[ xX]\] (.+)$", stripped)
        m_ul = re.match(r"^[-*] (.+)$", stripped)
        m_ol = re.match(r"^\d+\. (.+)$", stripped)
        if m_check or m_ul or m_ol:
            flush_para()
            flush_quote()
            if m_check:
                list_buf.append(("bullet", "☐ " + m_check.group(1)))
            elif m_ul:
                list_buf.append(("bullet", m_ul.group(1)))
            else:
                list_buf.append(("num", m_ol.group(1)))
            i += 1
            continue

        if not stripped:
            flush_all()
            i += 1
            continue

        flush_quote()
        flush_list()
        para_buf.append(stripped)
        i += 1

    flush_all()
    return flow


def add_page(canvas, doc, formal: bool) -> None:
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(HEAD_BG)
    canvas.rect(0, h - 12 * mm, w, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont(FONT, 8)
    if formal:
        canvas.drawString(18 * mm, h - 7.5 * mm, "速影 Studio  ·  软件许可及技术服务合同")
        canvas.drawRightString(w - 18 * mm, h - 7.5 * mm, "正式签署版")
    else:
        canvas.drawString(18 * mm, h - 7.5 * mm, "速影 Studio  ·  软件许可及技术服务合同  ·  v2.0.0")
        canvas.drawRightString(w - 18 * mm, h - 7.5 * mm, "模板 · 签署前须律师审阅")

    canvas.setFillColor(HEAD_BG)
    canvas.rect(0, 0, w, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont(FONT, 8)
    canvas.drawString(18 * mm, 5 * mm, "对外权利义务以签署文本为准")
    canvas.drawRightString(w - 18 * mm, 5 * mm, f"第 {doc.page} 页")
    canvas.restoreState()


def render(formal: bool = False, out: Path | None = None) -> Path:
    register_font()
    styles = build_styles()
    md = load_body(formal=formal)
    story = parse_flow(md, styles)
    out_path = out or (OUT_FORMAL if formal else OUT_TEMPLATE)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="速影 Studio 软件许可及技术服务合同",
        author="速影",
        subject="正式签署版合同" if formal else "合同模板 v2.0.0",
    )
    doc.build(
        story,
        onFirstPage=lambda canvas, d: add_page(canvas, d, formal=formal),
        onLaterPages=lambda canvas, d: add_page(canvas, d, formal=formal),
    )
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="渲染速影销售合同 PDF。")
    parser.add_argument("--formal", action="store_true", help="输出正式签署版（去掉模板提示）。")
    parser.add_argument("--out", type=Path, help="自定义输出 PDF 路径。")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out = render(formal=args.formal, out=args.out)
    print(out)


def _assert_wrap() -> None:
    register_font()
    samples = [
        "21.2 本合同一式 **贰** 份，双方各执壹份。电子签章与完整签章扫描件具有同等效力；冲突时以最后签署的纸质原件为准。",
        "21.2本合同一式**贰份**，双方各执壹份。电子签章与完整签章扫描件具有同等效力；冲突时以最后签署的纸质原件为准。",
    ]
    for sample in samples:
        lines = wrap_cjk_lines(tighten_cjk_ascii(sample), FONT, 9.5, BODY_WIDTH)
        for line in lines:
            vis = line.replace("*", "")
            if vis.startswith("份") or vis.startswith("，") or vis.startswith("。") or vis.startswith("；"):
                raise SystemExit(f"bad wrap start: {line!r} in {lines!r}")
            if "贰" in vis and "份" not in vis:
                raise SystemExit(f"split 贰/份: {lines!r}")


if __name__ == "__main__":
    _assert_wrap()
    main()
