#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate typeset 速影 Studio sales contract PDF (A4, Chinese) — v1.1 layout."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import Align, WrapMode, XPos, YPos

FONT_CANDIDATES = [
    Path.home() / "Library/Fonts/NotoSansSC.ttf",
    Path("/Library/Fonts/Arial Unicode.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
]
FONT = next((p for p in FONT_CANDIDATES if p.is_file()), None)
if FONT is None:
    raise SystemExit("No CJK-capable TTF found")

OUT = Path.home() / "Desktop" / "速影Studio-软件销售及技术服务合同.pdf"
VERSION = "v1.4.3"
TODAY = date.today().isoformat()

MARGIN = 17.0
PAGE_W = 210.0
BODY = 9.6
SMALL = 8.2
H_ART = 12.5
H_SEC = 10.8
LINE = 5.15
LINE_T = 4.95


def clean(text: str) -> str:
    """Normalize spaces for Chinese contract body; keep underscore blanks intact."""
    import re

    parts = re.split(r"(_{2,})", text)
    out: list[str] = []
    for p in parts:
        if re.fullmatch(r"_+", p):
            out.append(p)
            continue
        p = p.replace("\u00a0", " ").replace("\u3000", " ")
        # collapse runs of plain spaces
        p = re.sub(r"[ \t]{2,}", " ", p)
        # Chinese typography: no space between Han and ASCII digits
        p = re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\d])", "", p)
        p = re.sub(r"(?<=[\d]) +(?=[\u4e00-\u9fff])", "", p)
        # no space after fullwidth open / before fullwidth close & punctuation
        p = re.sub(r"(?<=[（【「《]) +", "", p)
        p = re.sub(r" +(?=[）】」》，。；：、])", "", p)
        # （1） 正文 → （1）正文
        p = re.sub(r"(（\d+）) +", r"\1", p)
        # leading/trailing
        out.append(p)
    return "".join(out)


# Prefer breaking after these (full phrase remains readable)
_BREAK_AFTER = set("，。；：、）】」》；,.;:!?%…—")


def wrap_cjk(pdf: FPDF, text: str, max_w: float) -> str:
    """Greedy wrap at punctuation; every line MUST measure <= max_w (strict)."""
    text = clean(text)
    if not text:
        return text

    def width(s: str) -> float:
        return float(pdf.get_string_width(s))

    if width(text) <= max_w:
        return text

    lines: list[str] = []
    buf = ""
    for ch in text:
        if ch == "\n":
            if buf:
                lines.append(buf)
            buf = ""
            continue
        trial = buf + ch
        if width(trial) <= max_w:
            buf = trial
            continue
        # Cannot place ch on current line.
        if not buf:
            # single glyph wider than line — hard emit
            lines.append(ch)
            continue
        best = -1
        start = max(0, len(buf) - 48)
        for j in range(len(buf) - 1, start - 1, -1):
            if buf[j] in _BREAK_AFTER or buf[j] == " ":
                best = j
                break
        if best >= 0:
            lines.append(buf[: best + 1].rstrip())
            rest = buf[best + 1 :].lstrip()
            buf = rest + ch
            # rest+ch might still be too long — handled on subsequent iterations? rest is shorter than buf so trail OK with overflow next loop if needed
            while width(buf) > max_w and len(buf) > 1:
                # forced mid-break (rare)
                # leave one char room; binary split
                cut = len(buf) - 1
                while cut > 1 and width(buf[:cut]) > max_w:
                    cut -= 1
                lines.append(buf[:cut])
                buf = buf[cut:]
        else:
            lines.append(buf)
            buf = ch
    if buf:
        # if still too long (shouldn't), force split
        while width(buf) > max_w and len(buf) > 1:
            cut = len(buf) - 1
            while cut > 1 and width(buf[:cut]) > max_w:
                cut -= 1
            lines.append(buf[:cut])
            buf = buf[cut:]
        if buf:
            lines.append(buf)

    # Pull trailing punctuation-only lines onto previous when possible.
    fixed: list[str] = []
    for ln in lines:
        if not ln:
            continue
        if fixed and all(c in _BREAK_AFTER or c.isspace() for c in ln):
            candidate = fixed[-1] + ln.strip()
            if width(candidate) <= max_w:
                fixed[-1] = candidate
            else:
                # Keep with previous line anyway and accept; safer than orphan punct.
                # Shorten previous by moving last segment instead:
                prev = fixed[-1]
                # re-break: put last 12 chars of prev + punct on new line if needed
                if len(prev) > 12:
                    fixed[-1] = prev[:-12]
                    next_ln = prev[-12:] + ln.strip()
                    if width(next_ln) <= max_w:
                        fixed.append(next_ln)
                    else:
                        fixed.append(prev[-12:])
                        if width(ln.strip()) <= max_w:
                            fixed.append(ln.strip())
                else:
                    fixed.append(ln.strip())
        else:
            fixed.append(ln)
    return "\n".join(fixed)


class ContractPDF(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4", unit="mm")
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(MARGIN, 16, MARGIN)
        # fpdf defaults multi_cell to JUSTIFY which inflates spaces between CJK/Latin.
        self.add_font("cn", "", str(FONT))
        self.add_font("cn", "B", str(FONT))
        # reduce horizontal cell padding so measured wrap ≈ drawn width
        self.c_margin = 0.5
        self.total_pages_placeholder = True

    def mc(
        self,
        text: str,
        h: float | None = None,
        *,
        bold: bool = False,
        size: float | None = None,
        align: Align = Align.L,
        border: int | str = 0,
        fill: bool = False,
        w: float | None = None,
    ) -> None:
        """Left-aligned text. Pre-wrap strictly; draw each line with cell (no re-wrap)."""
        if size is not None or bold:
            self.set_font("cn", "B" if bold else "", size or BODY)
        width = w if w is not None else self.epw
        lh = h if h is not None else LINE_T
        # cell/multi_cell usable width ≈ w - 2*c_margin
        usable = max(20.0, width - 2 * float(self.c_margin) - (1.2 if border else 0) - 0.2)
        lines = [ln for ln in wrap_cjk(self, text, usable).split("\n") if ln != ""]
        if not lines:
            return

        # Verify strict fit; re-split any offender.
        safe: list[str] = []
        for ln in lines:
            if self.get_string_width(ln) <= usable + 0.01:
                safe.append(ln)
            else:
                safe.extend(
                    x for x in wrap_cjk(self, ln, usable * 0.98).split("\n") if x != ""
                )

        if border or fill:
            self.set_x(self.l_margin)
            self.multi_cell(
                width,
                lh,
                "\n".join(safe),
                border=border,
                align=align,
                fill=fill,
                wrapmode=WrapMode.CHAR,
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )
            return

        for ln in safe:
            self._need(lh + 0.2)
            self.set_x(self.l_margin)
            self.cell(width, lh, ln, border=0, align=align, fill=False)
            self.ln(lh)

    def header(self) -> None:
        if self.page_no() <= 1:
            return
        self.set_font("cn", "", 7.8)
        self.set_text_color(95, 95, 95)
        self.set_xy(MARGIN, 8.5)
        self.cell(118, 5.5, "速影 Studio 软件销售及技术服务合同", align=Align.L)
        self.cell(self.epw - 118, 5.5, f"{VERSION}  ·  第 {self.page_no()} 页", align=Align.R)
        self.set_draw_color(150, 150, 150)
        self.set_line_width(0.22)
        self.line(MARGIN, 14.8, PAGE_W - MARGIN, 14.8)
        self.set_text_color(0, 0, 0)
        self.set_y(17.5)

    def footer(self) -> None:
        self.set_y(-11)
        self.set_draw_color(190, 190, 190)
        self.set_line_width(0.15)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.set_y(-9.5)
        self.set_font("cn", "", 7.2)
        self.set_text_color(115, 115, 115)
        self.cell(
            0,
            4.5,
            f"合同模板 · 正式用章前请双方审阅并填写空白项  |  {TODAY}  |  {VERSION}",
            align=Align.C,
        )
        self.set_text_color(0, 0, 0)

    def _need(self, h: float) -> None:
        if self.get_y() + h > self.page_break_trigger:
            self.add_page()

    def title_block(self) -> None:
        self.ln(8)
        self.set_font("cn", "B", 11)
        self.set_text_color(80, 80, 80)
        self.cell(0, 6, "合同模板", align=Align.C, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(0, 0, 0)
        self.ln(1)
        self.set_font("cn", "B", 18)
        self.multi_cell(
            self.epw, 9, "速影 Studio", align=Align.C, new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )
        self.set_font("cn", "B", 15.5)
        self.multi_cell(
            self.epw,
            8,
            "软件销售及技术服务合同",
            align=Align.C,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(1.5)
        self.set_draw_color(35, 35, 35)
        self.set_line_width(0.55)
        y = self.get_y()
        self.line(MARGIN + 40, y, PAGE_W - MARGIN - 40, y)
        self.set_line_width(0.18)
        self.line(MARGIN + 40, y + 1.2, PAGE_W - MARGIN - 40, y + 1.2)
        self.ln(4)
        self.set_font("cn", "", 8.4)
        self.set_text_color(70, 70, 70)
        self.multi_cell(
            self.epw,
            4.4,
            f"版本 {VERSION}  ·  生成日期 {TODAY}  ·  一式贰份各执壹份",
            align=Align.C,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.set_text_color(0, 0, 0)
        self.ln(3)

    def note_box(self, lines: list[str]) -> None:
        self._need(22)
        self.set_fill_color(246, 246, 246)
        self.set_draw_color(170, 170, 170)
        self.set_line_width(0.2)
        self.set_font("cn", "", 8.0)
        self.mc("\n".join(lines), 4.1, border=1, fill=True)
        self.ln(3)

    def callout(self, title: str, body_lines: list[str]) -> None:
        """Highlighted SLA / key commitment box."""
        self._need(28)
        self.set_draw_color(60, 60, 60)
        self.set_fill_color(250, 250, 250)
        self.set_line_width(0.35)
        self.set_xy(self.l_margin, self.get_y())
        self.set_font("cn", "B", 9.5)
        self.mc(title + "\n" + "\n".join(body_lines), 4.6, border=1, fill=True)
        self.ln(2.5)

    def meta_line(self, text: str) -> None:
        self.set_font("cn", "", BODY)
        self.cell(0, LINE + 0.2, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def party_block(self, title: str) -> None:
        self._need(40)
        self.set_font("cn", "B", 10.2)
        self.set_fill_color(232, 232, 232)
        self.set_draw_color(160, 160, 160)
        self.cell(self.epw, 6.8, f"  {title}", border=1, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("cn", "", BODY)
        fields = [
            "名称：________________________________________________",
            "统一社会信用代码：____________________________________",
            "住所：________________________________________________",
            "法定代表人/授权代表：________________________________",
            "联系人：______________  电话：______________  邮箱：____________________",
        ]
        for f in fields:
            self.cell(0, LINE, f"  {clean(f)}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2.2)

    def article(self, title: str, min_block_mm: float = 12) -> None:
        """Start an article; optionally keep a minimum block together to avoid orphans."""
        self.ln(1.8)
        self._need(min_block_mm)
        self.set_font("cn", "B", H_ART)
        self.cell(0, 7.0, clean(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(40, 40, 40)
        self.set_line_width(0.32)
        self.line(MARGIN, self.get_y(), PAGE_W - MARGIN, self.get_y())
        self.ln(1.8)

    def section(self, title: str) -> None:
        self.ln(1.2)
        self._need(9)
        self.set_font("cn", "B", H_SEC)
        self.mc(title, LINE)
        self.ln(0.4)

    def p(self, text: str) -> None:
        self.set_font("cn", "", BODY)
        self.mc(text, LINE_T)
        self.ln(0.7)

    def clause(self, lead: str, rest: str) -> None:
        self.set_font("cn", "", BODY)
        self.mc(f"{lead}{rest}", LINE_T)
        self.ln(0.65)

    def bullet(self, text: str) -> None:
        self.set_font("cn", "", BODY)
        self.mc(f"· {text}", LINE_T)
        self.ln(0.35)

    def num(self, n: str, text: str) -> None:
        self.set_font("cn", "", BODY)
        # no space after （n） — tighter Chinese list style
        self.mc(f"{n}{text}", LINE_T)
        self.ln(0.4)

    def table(
        self,
        headers: list[str],
        rows: list[list[str]],
        col_w: list[float],
        head_fill: tuple[int, int, int] = (228, 228, 228),
    ) -> None:
        n = len(headers)
        assert abs(sum(col_w) - self.epw) < 0.6, (sum(col_w), self.epw)

        def measure(cells: list[str], bold: bool) -> float:
            self.set_font("cn", "B" if bold else "", SMALL)
            max_h = LINE_T
            for i, c in enumerate(cells):
                lines = self.multi_cell(
                    col_w[i] - 2.2,
                    LINE_T,
                    clean(c) if c else " ",
                    align=Align.L,
                    wrapmode=WrapMode.CHAR,
                    dry_run=True,
                    output="LINES",
                )
                max_h = max(max_h, len(lines) * LINE_T + 1.6)
            return max_h

        def draw_row(cells: list[str], bold: bool = False, fill: bool = False) -> None:
            h = measure(cells, bold)
            self._need(h + 0.5)
            x0, y0 = self.l_margin, self.get_y()
            self.set_font("cn", "B" if bold else "", SMALL)
            if fill:
                self.set_fill_color(*head_fill)
            self.set_draw_color(145, 145, 145)
            self.set_line_width(0.14)
            x = x0
            for i, c in enumerate(cells):
                style = "DF" if fill else "D"
                self.rect(x, y0, col_w[i], h, style=style)
                self.set_xy(x + 1.0, y0 + 0.8)
                self.multi_cell(
                    col_w[i] - 2.0,
                    LINE_T,
                    clean(c) if c else " ",
                    align=Align.L,
                    wrapmode=WrapMode.CHAR,
                    new_x=XPos.RIGHT,
                    new_y=YPos.TOP,
                )
                x += col_w[i]
            self.set_y(y0 + h)

        draw_row(headers, bold=True, fill=True)
        for r in rows:
            rr = list(r) + [""] * (n - len(r))
            draw_row(rr[:n])
        self.ln(2.0)

    def annex_title(self, title: str, force_new: bool = True) -> None:
        # Avoid cascading near-empty attachment pages when the previous annex is short.
        if force_new or self.get_y() > 200:
            self.add_page()
            self.ln(2)
        else:
            self.ln(6)
        self.set_font("cn", "B", 13.5)
        self.multi_cell(self.epw, 7.5, title, align=Align.C, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(45, 45, 45)
        self.set_line_width(0.4)
        y = self.get_y() + 0.5
        self.line(MARGIN + 25, y, PAGE_W - MARGIN - 25, y)
        self.ln(4.5)

    def sign_page(self) -> None:
        self.add_page()
        self.ln(4)
        self.set_font("cn", "B", 14)
        self.cell(0, 8, "签署页", align=Align.C, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)
        self.set_font("cn", "", BODY)
        self.multi_cell(
            self.epw,
            LINE,
            "（本合同正文及附件一至附件六均为合同不可分割组成部分。签署即表示双方已阅读并同意全部条款。）",
            align=Align.C,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.ln(6)

        col = (self.epw - 10) / 2
        y0 = self.get_y()
        box_h = 72

        def one_party(x: float, name: str) -> None:
            self.set_draw_color(120, 120, 120)
            self.set_line_width(0.35)
            self.rect(x, y0, col, box_h)
            self.set_xy(x + 3, y0 + 4)
            self.set_font("cn", "B", 11)
            self.cell(col - 6, 7, name)
            self.set_font("cn", "", BODY)
            lines = [
                "（盖章）",
                "",
                "授权代表签字：",
                "____________________",
                "",
                "日期：____ 年 ____ 月 ____ 日",
            ]
            yy = y0 + 14
            for line in lines:
                self.set_xy(x + 4, yy)
                self.cell(col - 8, 6, line)
                yy += 7

        one_party(MARGIN, "甲方（客户 / 被许可方）")
        one_party(MARGIN + col + 10, "乙方（供方 / 许可方）")
        self.set_y(y0 + box_h + 10)

        self.set_font("cn", "", 8.5)
        self.set_text_color(90, 90, 90)
        self.multi_cell(
            self.epw,
            4.5,
            "提示：请使用与合同主体一致的公章或合同专用章；骑缝章可加盖在本合同各页右侧。"
            "电子扫描件在双方确认完整签章后与纸质原件具有同等效力（正文 17.2）。",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        self.set_text_color(0, 0, 0)


def build() -> Path:
    pdf = ContractPDF()
    pdf.add_page()
    pdf.title_block()
    pdf.note_box(
        [
            "【文件性质】客户签署用合同模板（中文简体）。价款、主体名称、设备选项等留空处由双方协商后填入。",
            "【效力说明】正式签署前须经双方盖章/签字；律师审阅修改意见以双方书面确认版为准。",
            "【产品边界】以附件一《产品功能清单》为准；对外权利义务以本合同及附件为准。",
        ]
    )

    pdf.meta_line("合同编号：____________________________")
    pdf.meta_line("签约日期：____ 年 ____ 月 ____ 日")
    pdf.meta_line("签约地点：____________________________")
    pdf.ln(2)
    pdf.party_block("甲方（客户 / 被许可方）")
    pdf.party_block("乙方（供方 / 许可方）")

    pdf.section("鉴于")
    pdf.p(
        "甲方拟在本机（macOS）部署和使用乙方「速影 Studio」软件产品，用于将甲方自有实拍视频素材"
        "稳定转换为可发布成片及配套物料，并在人在回路前提下获得多平台辅助发布与消息提醒能力；"
        "乙方具备相应软件交付与远程运维能力。双方在平等、自愿、诚实信用原则下，经协商一致，订立本合同。"
    )

    # 第一条
    pdf.article("第一条  定义")
    for lead, rest in [
        (
            "1.1  软件 / 产品：",
            "指乙方交付的「速影 Studio」（含桌面一体 App 内嵌引擎）及约定范围内的配套脚本、"
            "配置种子、离线模型物化组件与安装说明，具体功能以附件一《产品功能清单》为准。",
        ),
        (
            "1.2  授权设备：",
            "指甲方指定、经乙方核验并签发许可的单台 macOS 计算机（Apple Silicon）。"
            "许可默认绑定该设备，换机须由乙方重新签发许可。",
        ),
        (
            "1.3  买断授权：",
            "在甲方按约付清买断许可价款且许可签发生效后，甲方获得本合同约定授权设备上对本软件的永久使用许可（买断制）。"
            "许可不设年期届满自动锁定，本合同不设年度授权续费义务。为免歧义：买断仅指约定范围内的使用许可持续有效，"
            "不构成软件及源码所有权转让，亦不免除换机或重装时由乙方重新签发设备绑定许可的手续。",
        ),
        ("1.4  成片：", "指软件产出的可进入审片 / READY 的视频文件及其 sidecar 元数据。"),
        ("1.5  发布物料包：", "指审核通过后的视频、封面、各平台文案、字幕、口播等配套文件。"),
        (
            "1.6  半自动发布：",
            "指系统预填物料、打开官方创作者入口或按约定方式推进上传流程；登录、验证码、风控挑战及最终发布确认须由甲方人员亲自处理（见附件一）。",
        ),
        (
            "1.7  消息辅助：",
            "指对本机本人已登录账号的未读消息进行只读摘要展示，并引导至官方页面由甲方人工回复；不包含自动发送回复。",
        ),
        (
            "1.8  工作日：",
            "指中华人民共和国法定工作日（不含法定节假日及休息日）。",
        ),
        (
            "1.9  响应：",
            "指甲方按约定渠道正式报障并提交完整故障信息后，乙方首次给出有效接单反馈"
            "（含工单号、责任人、初步判断或需补充的资料清单）的行为。",
        ),
        (
            "1.10  彻底解决：",
            "指属乙方责任范围内的可复现故障，经修复、配置恢复或许可纠正后，约定功能已恢复到交付基线可用状态。"
            "不包括第三方平台改版、账号封禁、甲方素材瑕疵、甲方硬件故障等乙方无法单方控制的障碍。",
        ),
    ]:
        pdf.clause(lead, rest)

    # 第二条
    pdf.article("第二条  合同标的与范围")
    pdf.clause("2.1  ", "乙方向甲方提供下列标的（可勾选；未勾选不构成交付义务）：")
    pdf.table(
        ["序号", "标的", "是否包含", "说明"],
        [
            ["A", "速影 Studio 软件许可（单机买断）", "□ 是", "见附件一；默认1台授权设备、1个客户配置实例"],
            ["B", "首装部署与配置调优", "□ 是", "远程或现场；第五、六条就绪后启动"],
            ["C", "运维与远程技术支持", "□ 是", "一次性付费，标准服务期3年；含SLA与7.7更新；见第七条"],
            ["D", "行业/客户配置种子", "□ 是", "词池、品牌规则等；不含片库/成片/Cookie/密钥"],
            ["E", "推荐硬件采购代购 / 清单协助", "□ 是", "见附件四；权属与质保按销售票据"],
            ["F", "其他：____________", "□ 是", "________________"],
        ],
        col_w=[12, 50, 22, 92],
    )
    pdf.clause(
        "2.2  交付形态：",
        "macOS 一体 App（内嵌日更引擎）；离线模型与运行时按乙方正式交付路径物化安装。"
        "不包含卖方源码（除非另签源码协议）；也不将客户片库/成片上传至乙方公有云作为默认服务。",
    )
    pdf.clause("2.3  ", "明确不在本合同交付范围内（非目标），包括但不限于：")
    for i, t in enumerate(
        [
            "无人值守全自动多账号矩阵发布、养号、群控、Cookie 池换号；",
            "自动过验证码 / 滑块 / 对抗平台检测、以「反检测浏览器」为卖点的能力；",
            "自动输入并发送私信 / 评论回复（仅摘要 + 跳转官方页人工回复，或软文回复草稿）；",
            "保证任何第三方社交 / 短视频平台的账号安全、流量效果、审核通过率或商业转化；",
            "替甲方撰写与其业务无关的软文站群、违规营销内容；",
            "数字人「假脸」口播或未列明的第三方 SaaS 订阅费用；",
            "非苹果 Silicon 设备或未经验证环境上的运行保证。",
        ],
        1,
    ):
        pdf.num(f"（{i}）", t)
    pdf.clause(
        "2.4  ",
        "甲方理解并确认：辅助发布与消息能力依赖甲方本人已登录的合法账号与官方页面可用性；"
        "平台政策变更、页面改版、限流、封禁等属于第三方风险，不构成乙方违约；"
        "乙方仅负有合理协助排查与适配升级义务（若在服务期内）。",
    )

    # 第三条
    pdf.article("第三条  价款与支付")
    pdf.clause(
        "3.1  ",
        "合同总价（含税 □ 是 / □ 否，税率 ____%）：人民币大写 ____________________ 元整（¥ __________）。",
    )
    pdf.clause("3.2  ", "价款构成（可增减）：")
    pdf.table(
        ["项目", "金额（元）", "备注"],
        [
            ["软件买断许可", "____________", "含 ____ 台授权设备；一次买断，不设年期续费"],
            ["首装部署与培训", "____________", "远程 ____ 次 / 现场 ____ 日"],
            [
                "运维服务（可选）",
                "____________",
                "一次性付清；标准服务期3年（自____年____月____日起计）；含SLA与7.7；不按月计费",
            ],
            ["硬件设备（附件四）", "____________", "以实际采购清单与发票为准"],
            ["其他", "____________", ""],
            ["合计", "____________", ""],
        ],
        col_w=[48, 38, 90],
    )
    pdf.clause("3.3  ", "支付节点（默认；可改）：")
    pdf.num("（1）", "签约后 3 个工作日内支付合同总价的 ____% 作为预付款；")
    pdf.num("（2）", "首装验收通过（第八条）后 ____ 个工作日内支付 ____%；")
    pdf.num(
        "（3）",
        "余款 ____% 于服务期开始后 ____ 日内 / 硬件到货签收后 ____ 日内支付。",
    )
    pdf.clause(
        "3.4  ",
        "甲方逾期付款的，乙方有权暂停交付、暂停许可签发或暂停运维服务，且不承担因此导致的进度延误责任；"
        "每逾期一日，甲方应按应付未付金额的万分之三支付违约金，但总额不超过应付未付金额的 20%。",
    )
    pdf.clause(
        "3.5  ",
        "发票：乙方在收到对应款项后 ____ 个工作日内开具 □ 增值税专用发票 / □ 普通发票。",
    )

    # 第四条
    pdf.article("第四条  知识产权与许可")
    pdf.clause(
        "4.1  软件权利：",
        "速影 Studio 软件及其文档、商标、界面设计、引擎逻辑、默认行业模板与更新包的知识产权归乙方（或其合法权利人）所有。"
        "本合同是使用许可，而非软件所有权转让。",
    )
    pdf.clause(
        "4.2  许可范围：",
        "非独占、不可转许可（除非书面同意）、不可转让；限附件约定数量的授权设备、限甲方内部经营使用；"
        "禁止反向工程、破解许可、拆分出租 SaaS 化对外转售（法律强制性允许的部分除外）。",
    )
    pdf.clause(
        "4.3  甲方内容权利：",
        "甲方提供的公司资料、品牌素材、视频片库、账号运营内容及成片中可识别的甲方经营信息，知识产权与合法性责任均归属甲方。"
        "乙方仅为履行合同之目的在授权设备本地处理上述内容，不得擅自用于其他客户或公开传播。",
    )
    pdf.clause(
        "4.4  成片归属：",
        "在甲方已全额支付对应许可与服务价款的前提下，甲方对使用本软件生成的成片及 publish_pack 享有使用权与经营支配权；"
        "其中由乙方软件自动生成的结构、算法产物本身不转移乙方对软件的知识产权。",
    )
    pdf.clause(
        "4.5  开源与第三方组件：",
        "交付包中含 FFmpeg、Ollama 及模型等第三方组件时，甲方应遵守相应开源/模型许可；乙方按交付说明随附清单。"
        "因甲方违反第三方许可导致的索赔，由甲方自行承担。",
    )
    pdf.clause(
        "4.6  买断效力：",
        "甲方按约付清买断许可价款并完成许可导入后，在授权设备上对本软件的使用许可持续有效，无需按年续签许可，"
        "亦不会仅因“授权年限届满”而锁定功能。买断不包括：源码所有权、无限制跨设备迁移、第三方组件独立费用，"
        "以及双方另行书面约定、不在付费运维范围内的增值模块。"
        "大版本与生产内容的持续性更新权益见 4.8 与第七条 7.7；范围外定制另签补充协议。",
    )
    pdf.clause(
        "4.7  备份与换机：",
        "甲方应自行做好本机数据备份。换机、重装系统需要重新签发许可的，甲方应提前通知乙方；"
        "因账号盗用、设备丢失导致的风险由甲方承担。",
    )
    pdf.clause(
        "4.8  生产模板与内容演进：",
        "成片规则、封面模板、默认配方、行业配置种子，以及特效、声效等特殊内容资源，随产品演进可能持续调整。"
        "甲方仅购买软件买断许可、未采购运维服务的，有权使用交付时点已就位的功能与内容基线；"
        "后续持续性更新权益以第七条付费运维范围为准，买断本身不附带强制更新订阅。",
    )

    # 第五条
    pdf.article("第五条  甲方资料与前期素材义务（首装前提）")
    pdf.section("5.1  公司资料（首装前必须提供）")
    pdf.p(
        "甲方应在约定首装日至少 5 个工作日前，以可编辑电子文档形式向乙方提供真实、完整、合法的公司资料，至少包括："
    )
    for i, t in enumerate(
        [
            "企业法定名称、品牌名称、常用简称与对外口径；",
            "经营时间（成立/开业时间、重要发展阶段节点）；",
            "经营理念 / 使命愿景 / 服务承诺（可公开发布的表述）；",
            "发展历程（重大事件、门店/产线/荣誉等，按时间线）；",
            "生产或经营类型（例如生产制造、连锁服务、门店零售等）；",
            "产品 / 服务类型及功效 / 卖点明细（分条列示，避免虚假夸大与违禁表述）；",
            "合规要求：禁用词、必提资质、行业广告法红线（如有）；",
            "Logo、主色、字体偏好、门店/实景参考（如有）；",
            "目标发布平台账号清单（本人账号）及运营联系人。",
        ],
        1,
    ):
        pdf.num(f"（{i}）", t)
    pdf.p(
        "甲方对前述资料的真实性、合法性、不侵犯第三方权益承担全部责任。因资料虚假、侵权或违禁导致的行政处罚、"
        "平台处罚及第三方索赔，由甲方负责；给乙方造成损失的，甲方应予赔偿。"
    )

    pdf.section("5.2  视频素材交付要求（数据库填充前提）")
    pdf.callout(
        "【素材数量硬性要求】",
        [
            "· 合同生效之日起7个自然日内（一周内）提供不少于300条合格视频素材（入库级）；",
            "· 尽快满足1000条合格视频素材，用于填充数据库；",
            "· 未达1000条时，乙方可完成软件安装，但对稳定日更产能、语义检索与发布闭环试跑不作验收通过承诺。",
        ],
    )
    pdf.clause("（1）拍摄设备与规格（合格素材硬条件）：", "")
    pdf.bullet("须使用手持云台（大疆 DJI OM / Osmo Mobile 系列 G7 或书面确认的同级稳定器）配合手机拍摄；")
    pdf.bullet(f"分辨率须为 1080×1920（竖屏） 或 1920×1080（横屏） 高清规格；")
    pdf.bullet(
        "横屏或竖屏风格一经选定须全库统一，合同期内不可中途变更画幅策略"
        "（变更须书面补充协议，并可能产生额外配置与重建索引费用）。"
    )
    pdf.clause(
        "（2）内容与技术质量：",
        "画面稳定、无明显严重虚焦/黑场/无声废片；素材须为甲方有权使用的实拍内容；"
        "禁止提供盗用、涉黄涉政、侵权音乐未授权等违法内容。不合格素材不计入条数，乙方有权退回并要求补拍。",
    )
    pdf.clause(
        "（3）延迟责任：",
        "因甲方迟延提供资料或合格素材导致的首装延误、产能不达标，不构成乙方违约，首装与验收时间相应顺延；"
        f"迟延超过 30日的，乙方有权按已发生工作量结算并暂停服务。",
    )

    # 第六条
    pdf.article("第六条  部署环境与设备条件")
    pdf.clause(
        "6.1  ",
        "软件运行环境最低要求：苹果 Silicon Mac、足够统一内存与磁盘空间、本机可写的工作区路径、"
        "可安装官方 Google Chrome（消息巡检与受管发布所需）、网络条件满足远程运维（如购买运维服务）。",
    )
    pdf.clause(
        "6.2  推荐硬件：",
        "为保障生产性能与本地媒资存储，双方同意参考附件四《设备购买清单》。是否由乙方代购、权属、保修以附件四及购物凭证为准。"
        "甲方坚持使用低于推荐配置设备的，乙方仅作尽力支持，不对渲染时长、并发能力与稳定性作同等承诺。",
    )
    pdf.clause(
        "6.3  本地优先存储：",
        "默认权威数据与设置位于本机约定目录；媒体工作区位于本机影片/约定路径；外置盘仅为可选路径覆盖，"
        "不作为默认生产硬依赖（双方书面约定外置策略的除外）。",
    )
    pdf.clause(
        "6.4  ",
        "甲方应保证：授权设备电源与休眠策略不影响约定班次内的生产/发布；按乙方指引完成极空间等载体客户端登录"
        "（若采用载体交付）；不擅自删除许可、签名与权威库文件。",
    )

    # 第七条 SLA
    pdf.article("第七条  运维服务级别（SLA）")
    pdf.p(
        "在甲方已购买运维服务且服务期有效、运维价款一次性付清的前提下，乙方提供如下支持。"
        "付费运维有效时长为3年：自双方确认的服务期起算日（含当日）起连续3个自然年届满之日止；"
        "期满未另订新约的，运维与7.7更新权益自动终止。运维服务费为该3年服务期内的一次总价，"
        "不按月拆分计费，亦不因使用频次或月份折算退还。"
        "运维期起算日约定为：____ 年 ____ 月 ____ 日（未填写时默认自首装验收通过日起算）。"
    )
    pdf.callout(
        "【核心服务时效】",
        [
            "· 在线响应：法定工作日内报障通道全天在线可接；乙方收齐完整故障信息后，24小时内完成首次有效响应。",
            "· 彻底解决：对属乙方软件缺陷、配置错误或许可问题的故障，在甲方已充分配合的前提下，",
            "  自首次有效响应时起最晚48小时内彻底解决，使约定功能恢复交付基线可用状态。",
            "· 时限中止与除外（等待甲方配合、第三方平台障碍等）见 7.4；未达标救济见 7.5。",
        ],
    )
    pdf.section("7.1  服务渠道与时间")
    pdf.num("（1）", "报障渠道：________________（微信工作群 / 电话 / 邮件，择一或并列）；")
    pdf.num(
        "（2）",
        "服务时间：法定工作日全天在线接单与响应（不限于白天固定班次）；"
        "法定节假日及休息日可登记报障，响应与解决时限原则上自下一工作日开始起算（紧急故障乙方尽量提前处理）；",
    )
    pdf.num(
        "（3）",
        "远程运维：经甲方事先授权，乙方可通过安全方式登录授权设备进行诊断、升级、许可导入；操作留痕。",
    )
    pdf.section("7.2  响应时效")
    pdf.p(
        "甲方按约定渠道提交完整故障信息（现象、发生时间、截图或日志、是否可复现）后，"
        "乙方应在24小时内完成首次有效响应（接单反馈）。"
    )
    pdf.section("7.3  解决时效")
    pdf.p(
        "对属于乙方软件缺陷、配置错误或许可问题的故障，在甲方已按要求提供配合信息的前提下，"
        "乙方承诺自首次有效响应时起最晚48小时内彻底解决，恢复约定功能可用。"
        "若故障根因属第三方平台、甲方硬件/素材或合同范围外需求，不适用本款时限，乙方仍应在合理范围内协助诊断并书面说明原因。"
    )

    pdf.section("7.4  时效中止与除外")
    pdf.p("下列情形中止计算 7.2 / 7.3 时限，原因消除后继续计算：")
    for i, t in enumerate(
        [
            "等待甲方补充日志、复现步骤、账号登录、现场配合；",
            "第三方平台故障、接口/页面改版、账号被限制或封禁；",
            "甲方硬件损坏、磁盘满、系统崩溃、误删数据、未按文档操作；",
            "电力、网络、不可抗力；",
            "甲方要求的定制开发、范围外需求；",
            "法定节假日及休息日已登记但尚未进入下一工作日计时窗口的一般问题。",
        ],
        1,
    ):
        pdf.num(f"（{i}）", t)

    pdf.section("7.5  SLA 未达标的救济（服务期延长）")
    pdf.p(
        "因乙方原因导致 7.2 或 7.3 连续两个自然月内合计超时超过3次的，甲方有权要求等长度延长运维服务期"
        "（以双方书面确认的超时累计时长为准）。因运维价款为服务期内一次性总价，双方确认：不适用运维费减免、退还、"
        "月度折算或按比例冲抵；除本款约定的服务期延长外，甲方不得以 SLA 迟延为由主张其他费用补偿。"
        "双方确认上述救济为针对 SLA 时效义务的唯一救济，不影响因故意或重大过失造成人身/财产直接损失时的法定权利。"
    )
    pdf.section("7.6  缺陷修复与常规补丁")
    pdf.p(
        "服务期内，乙方可对软件进行缺陷修复与安全更新；覆盖升级按乙方正式交付路径执行，"
        "甲方不得要求以非正式热补文件作为验收标准。功能定制开发另议。"
    )
    pdf.section("7.7  付费运维包含的内容更新权益")
    pdf.p(
        "在运维服务有效且价款一次性付清期间，除 7.1–7.6 支持义务外，甲方还获得下列持续性更新权益"
        "（均为不定期发布，乙方有权根据产品路线决定节点与清单，不设固定日历、不承诺每次必有更新）："
    )
    pdf.num(
        "（1）",
        "生产规则更新：成片规则、默认配方、封面模板、行业配置种子等相关生产规则与模板的持续不定期更新；",
    )
    pdf.num(
        "（2）",
        "速影大版本更新：速影 Studio 主程序/引擎的大版本正式更新，经乙方正式交付路径部署至授权设备；",
    )
    pdf.num(
        "（3）",
        "特殊内容更新：特效、声效及其他乙方提供的特殊成片资源包的不定期更新（以乙方当期发布清单为准）；",
    )
    pdf.num(
        "（4）",
        "交付与限制：上述更新经正式路径提供安装或物化后即完成履约；不含甲方定制素材版权采购、"
        "平台官方资源强制适配保证，亦不含合同范围外的一对一专项定制。更新不必满足甲方全部审美偏好。",
    )
    pdf.p(
        "运维服务期满后，甲方可继续使用买断许可下已安装软件及已落地内容，"
        "但不再享有 7.7 项下新的生产规则、大版本与特殊内容更新权益；已付一次性运维费不予退还。"
        "若双方另订新的3年服务期与价款，须另行一次性结清后再生效。"
    )

    # 第八条
    pdf.article("第八条  交付、培训与验收")
    pdf.section("8.1  首装交付物（资料与环境就绪后）")
    for i, t in enumerate(
        [
            "可在授权设备启动的速影 Studio 及相关许可导入；",
            "基础路径与客户配置就绪（片库、输出、词池/品牌规则按附件）；",
            "健康检查通过（应用可启动、引擎健康、本地依赖按交付清单就位）；",
            "操作说明或一次远程操作培训（时长约 ____ 小时）。",
        ],
        1,
    ):
        pdf.num(f"（{i}）", t)
    pdf.section("8.2 验收标准（全部满足则首装验收通过）")
    for i, t in enumerate(
        [
            "软件可启动且许可状态正常；",
            "甲方提供的不少于1000条合格素材完成扫描入库（或甲方书面同意暂缓）；",
            "在甲方素材与配置条件下完成约定试产成片共____条；"
            "经甲方抽检确认达到「可进审片、可发布」标准；主观调色偏好不构成不合格；",
            "半自动发布链路完成一次演练（登录与验证码由甲方完成）；"
            "消息摘要能力完成一次可读检查（若购买对应模块）；",
            "双方签署《首装验收确认单》（附件五）。",
        ],
        1,
    ):
        pdf.num(f"（{i}）", t)
    pdf.clause(
        "8.3  ",
        "甲方在收到验收通知后 5 个工作日内未反馈书面异议的，视为验收通过。"
        "异议应具体、可复现；与主观审美有关之争议，以双方预签的黄金样片 / 质量锁口径为准。",
    )
    pdf.clause(
        "8.4  试运行：",
        "验收后可设 ____ 日试运行。试运行期内甲方应以工单形式反馈缺陷；属软件缺陷的由乙方免费修复。",
    )

    # 第九条
    pdf.article("第九条  双方权利义务")
    pdf.section("9.1  甲方")
    for i, t in enumerate(
        [
            "按约付款、提供真实资料与合格素材；",
            "合法运营平台账号，自行承担内容合规与广告法责任；",
            "保护本机账号安全，不向无关第三方泄露远程协助凭证；",
            "不超出许可范围使用软件；",
            "对发布内容、标题、功效宣称进行上线前人工审核。",
        ],
        1,
    ):
        pdf.num(f"（{i}）", t)
    pdf.section("9.2  乙方")
    for i, t in enumerate(
        [
            "按附件一提供软件功能与首装服务；",
            "对服务期内约定故障按 SLA 响应；",
            "对在运维过程中知悉的甲方经营秘密保密；",
            "不以「保证上热门 / 保证封号为零 / 保证全自动无人值守」作虚假宣传。",
        ],
        1,
    ):
        pdf.num(f"（{i}）", t)

    # 第十–十七
    pdf.article("第十条  保密")
    pdf.clause(
        "10.1  ",
        "双方对合同价款、技术细节、账号信息、经营数据承担保密义务，保密期限为合同终止后 3 年"
        "（商业秘密在秘密性存续期间持续有效）。",
    )
    pdf.clause(
        "10.2  除外：",
        "已公开信息、法定披露、对方书面同意、为履行合同必须告知的员工/分包（须同等保密）。",
    )

    pdf.article("第十一条  数据安全与个人信息")
    pdf.clause(
        "11.1  ",
        "默认处理发生在甲方授权设备本地；消息巡检仅处理脱敏摘要与官方链接，不擅自外传完整会话。",
    )
    pdf.clause(
        "11.2  ",
        "若甲方启用可选外部通知通道（如 ntfy 等），应自行评估数据出境与内容边界，并仅发送脱敏信息。",
    )
    pdf.clause(
        "11.3  ",
        "乙方远程运维应最小必要访问；任务完成后不得无故保留甲方片库拷贝。甲方可要求运维结束后确认删除临时传输包。",
    )

    pdf.article("第十二条  陈述、保证与免责")
    pdf.clause("12.1  ", "乙方保证：其有权许可软件；交付物不含故意设置的恶意后门。")
    pdf.clause(
        "12.2  不保证：",
        "不间断运行、完全无缺陷、兼容未来一切 macOS 或平台改版后的自动可用；不保证任何营销效果 KPI。",
    )
    pdf.clause(
        "12.3  平台与账号风险：",
        "启用自动/半自动上传、视觉辅助点击等能力前，甲方确认已阅读风险并以书面或产品内确认方式接受；"
        "因平台处罚产生的损失由甲方自行承担，但因乙方故意或重大过失导致的除外。",
    )
    pdf.clause(
        "12.4  内容合规：",
        "成片与文案的最终发布责任在甲方；乙方工具生成内容仅供辅助，甲方必须人工审核。",
    )

    pdf.article("第十三条  违约责任与责任限制")
    pdf.clause("13.1  ", "一方违约的，应赔偿对方因此遭受的直接损失。")
    pdf.clause(
        "13.2  责任上限：",
        "除故意、重大过失、人身损害、或侵犯对方知识产权/保密义务外，乙方在本合同项下的累计赔偿责任不超过"
        "甲方就引起索赔的该 12 个月期间已向乙方实际支付的软件许可费与运维费总额（不含硬件进货成本）。",
    )
    pdf.clause(
        "13.3  ",
        "任何一方均不对对方的利润损失、数据间接损失、商誉损失、第三方平台流量损失等间接损害负责，法律禁止排除的除外。",
    )
    pdf.clause("13.4  ", "甲方逾期提供资料/素材或硬件不到位导致的损失，由甲方自行承担。")

    pdf.article("第十四条  不可抗力")
    pdf.p(
        "因自然灾害、战争、政府行为、大规模停电断网、第三方云计算/应用商店/短视频平台全国性故障等不可抗力导致不能履行的，"
        "受影响一方在及时通知后可部分或全部免除责任，但应尽力减损。付款义务仅在不可抗力直接阻止支付渠道时暂缓。"
    )

    # Force articles 15–17 onto a dedicated page to avoid sparse orphans.
    if pdf.get_y() > 90:
        pdf.add_page()
    pdf.article("第十五条  期限、变更与终止")
    pdf.clause(
        "15.1  ",
        "软件买断许可自价款付清且许可生效之日起按本合同持续有效；"
        "运维服务期限（如采购）自 ____ 年 ____ 月 ____ 日起算，连续3年届满之日止"
        "（与第七条一致；因7.5等延长服务期的，以书面延长后的届满日为准）。"
        "本合同文本其他条款自签字盖章之日起生效。",
    )
    pdf.clause("15.2  ", "协议变更须书面（含双方确认的电子扫描件）方为有效。")
    pdf.clause(
        "15.3  ",
        "一方严重违约且在收到书面催告后 15 日内未改正的，守约方有权解除合同。",
    )
    pdf.clause(
        "15.4  ",
        "合同终止后：甲方应停止超出许可范围的使用；已付清的软件买断许可费不予退还（因乙方根本违约导致解除的除外）；"
        "硬件按已交付实事结算；乙方协助在合理范围内导出甲方数据（工时费另议）。",
    )

    pdf.article("第十六条  适用法律与争议解决")
    pdf.clause("16.1  ", "适用中华人民共和国法律（不含冲突规范）。")
    pdf.clause(
        "16.2  ",
        "因本合同引起的争议，双方先协商；协商不成的，提交 ________________ 人民法院诉讼，"
        "或提交 ________________ 仲裁委员会仲裁（二者择一填写，不可同时约定）。",
    )

    pdf.article("第十七条  其他")
    pdf.clause(
        "17.1  ",
        "本合同附件为本合同不可分割组成部分，解释顺序：特别约定手写（签章确认）＞本合同正文＞附件。",
    )
    pdf.clause(
        "17.2  ",
        "本合同一式贰份，双方各执壹份，具有同等法律效力。电子签章与打印扫描件（完整签章）具有同等效力，"
        "若不一致以最后签署的纸质原件为准。",
    )
    pdf.clause("17.3  反商业贿赂：", "双方不得向对方人员输送不正当利益。")
    pdf.clause(
        "17.4  通知：",
        "以合同载明邮箱/工作群送达，发送成功视为送达（除非退信）。",
    )
    pdf.ln(2)
    pdf.set_font("cn", "B", BODY)
    pdf.cell(0, LINE, "（正文完，以下为附件）", align=Align.C, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # —— 附件一 ——
    pdf.annex_title("附件一  产品功能清单（对照已实现能力）")
    pdf.p("本附件描述交付基线能力。未勾选模块不交付。甲方使用范围以实际采购模块为准。")
    pdf.section("A. Studio 混剪工作台")
    pdf.table(
        ["功能", "说明"],
        [
            ["本机 App 控制台", "总览、生产、规则、审片、发布、消息、数据、运维、设置等页面"],
            ["引擎启停与健康", "App 内启动/停止本地引擎"],
            ["片库监视与入库", "扫描甲方实拍素材，形成可生产索引"],
            ["任务生产", "按条数/日历/自动开跑等策略产出竖屏/横屏成片（画幅按配置隔离）"],
            ["质量门禁", "虚焦/模糊等质量策略；审片与 READY 流程"],
            ["旁白与字幕", "脚本/TTS、旁白字幕对齐；可选本地音色方案（以实际装机为准）"],
            ["规则实验室", "标题、字幕/旁白语言、音色、语速、音量、画面质量等冻结到任务"],
            ["品牌与词池", "可导入客户词池、品牌样式、视频规则配置种子"],
            ["本地向量化", "默认关闭，可在运维页开启后增量补齐（用于语义选片等）"],
            ["备份/清理/同步辅助", "本机权威库与工作区运维能力（按装机版本）"],
        ],
        col_w=[42, 134],
    )
    pdf.section("B. Pack 发布物料")
    pdf.table(
        ["功能", "说明"],
        [
            ["发布物料包", "审核通过后生成视频、封面、多平台文案、字幕、口播等"],
            ["多平台文案适配", "抖音 / 视频号 / 小红书 / 快手等视频向文案变体（以装机为准）"],
        ],
        col_w=[42, 134],
    )
    pdf.section("C. Reach 辅助触达（人在回路）")
    pdf.table(
        ["功能", "说明"],
        [
            ["发布队列", "从物料包预填，打开官方创作者流程"],
            ["手动半自动", "人点发布，风险最低的默认方式"],
            ["即时批量发布", "勾选本人账号、单机单槽串行；登录/验证码停人"],
            ["定时/自动任务", "窗口内随机排期（审计可追溯）；可与生产闭环联动（以装机为准）"],
            ["账号与 Chrome 配置", "本机独立用户目录，串行换号；非 Cookie 池"],
            ["消息巡检", "定时只读未读摘要；点「去回复」跳转官方页人工回复"],
            ["通知", "App / 系统通知等（可选外部通道）"],
        ],
        col_w=[42, 134],
    )
    pdf.section("D. Content 软文辅助（若采购）")
    pdf.table(
        ["功能", "说明"],
        [
            ["品牌内容生成与审核流", "事实约束、平台变体；验证码停人"],
            ["软文与视频会话隔离", "独立浏览器配置与消息域"],
        ],
        col_w=[42, 134],
    )
    pdf.section("E. 明确不包含（再次确认）")
    pdf.p(
        "自动私信回复、反检测对抗、多账号并发群控、保证平台不处罚、保证营销 KPI、源码交付（另议）。"
    )
    pdf.p("甲方确认已阅读并理解本附件能力边界：____________________（签章）")

    # 附件二
    pdf.annex_title("附件二  甲方应提交资料清单（首装）")
    items = [
        "□  营业执照扫描件（或统一社会信用代码）",
        "□  公司介绍：经营时间、理念、发展历程",
        "□  生产/经营类型说明",
        "□  产品/服务类型及功效/卖点明细表",
        "□  广告法/行业合规禁用词与必备资质说明",
        "□  Logo 与视觉规范",
        "□  目标平台与本人账号清单、运营联系人",
        "□  画幅策略确认：□ 统一竖屏 1080×1920      □ 统一横屏 1920×1080",
    ]
    for t in items:
        pdf.p(t)
    pdf.ln(3)
    pdf.p("提交日期：____________          提交人：____________")

    # 附件三（紧接附件二，避免单独成页过空）
    pdf.annex_title("附件三  视频素材验收标准", force_new=False)
    pdf.table(
        ["项", "标准"],
        [
            ["一周目标", "合同生效7日内（一周内）≥300条合格素材"],
            ["填充数据库", "尽快满足1000条合格视频素材入库"],
            ["稳定器", "手持云台：大疆 G7（或书面同级）"],
            ["分辨率", "1080×1920 或 1920×1080"],
            ["画幅", "全库统一，合同期不可擅自变更"],
            ["权属", "甲方有权使用的实拍；可提供拍摄承诺函"],
            ["不合格", "严重虚焦、黑场、花屏、错误分辨率、画幅混用、侵权内容等"],
        ],
        col_w=[36, 140],
    )
    pdf.p("合格条数以双方清点单或系统入库成功数为准。")

    # 附件四
    pdf.annex_title("附件四  设备购买清单（推荐 / 可选采购）")
    pdf.p(
        "下列为生产环境推荐配置。甲方可自行采购或委托乙方代购。代购时货权、保修、发票抬头以实际订单为准；"
        "价格随市场浮动，以采购当日报价单为附件补页。"
    )
    pdf.section("方案甲 — 主机（二选一）")
    pdf.table(
        ["选项", "型号与配置", "数量", "备注"],
        [
            [
                "A1",
                "MacBook Pro M4 Max，统一内存 128GB，存储 1TB 或 8TB（下单确定）",
                "1",
                "推荐重生产/高并发负载",
            ],
            [
                "A2",
                "MacBook Pro M5，统一内存 48GB，存储 1TB 或 8TB（下单确定）",
                "1",
                "平衡性能与成本",
            ],
        ],
        col_w=[14, 102, 16, 44],
    )
    pdf.p(
        "甲方勾选：□ A1（存储：□ 1TB / □ 8TB）    □ A2（存储：□ 1TB / □ 8TB）    "
        "□ 甲方自备主机（型号：____________）"
    )
    pdf.section("方案乙 — 存储（二选一）")
    pdf.table(
        ["选项", "设备组合", "数量", "用途建议"],
        [
            ["B1", "极空间 T2s + 梵想 SSD 790 固态硬盘 4TB × 2", "1 套", "高速载体与高性能固态存储"],
            ["B2", "极空间 Z4s + 8TB HDD × 4（机械硬盘）", "1 套", "大容量媒资与备份扩展"],
        ],
        col_w=[14, 102, 16, 44],
    )
    pdf.p("甲方勾选：□ B1    □ B2    □ 甲方自备存储（说明：________________________）")
    pdf.section("方案丙 — 拍摄（甲方自备，强制符合附件三）")
    pdf.table(
        ["设备", "要求"],
        [
            ["手持云台", "大疆 G7（DJI OM 系列对应型号）或同级"],
            ["拍摄手机", "可稳定输出 1080×1920 / 1920×1080"],
        ],
        col_w=[36, 140],
    )
    pdf.section("到货与验收")
    pdf.num("（1）", "外观完好、包装配件齐全、通电识别正常；")
    pdf.num("（2）", "序列号登记于本附件或进货单；")
    pdf.num("（3）", "硬件质保以厂商政策为准，乙方向甲方转交保修凭证。")

    # 附件五
    pdf.annex_title("附件五  首装验收确认单（模板）")
    pdf.table(
        ["检查项", "结果", "备注"],
        [
            ["App 可启动、许可正常", "□ 通过    □ 整改", ""],
            ["引擎健康检查", "□ 通过    □ 整改", ""],
            ["公司资料已导入/规则可加载", "□ 通过    □ 整改", ""],
            ["合格素材入库条数", "实际 ____ 条（≥1000 可验收产能）", ""],
            ["试产成片 ____ 条", "□ 通过    □ 整改", ""],
            ["半自动发布演练", "□ 通过    □ 整改", ""],
            ["消息摘要演练（如适用）", "□ 通过    □ 整改", ""],
            ["SLA 渠道已建立", "□ 通过    □ 整改", ""],
        ],
        col_w=[55, 72, 49],
    )
    pdf.p("验收结论：□ 通过      □ 有条件通过（整改期限：____）      □ 不通过")
    pdf.ln(5)
    pdf.p("甲方签字：____________      乙方签字：____________      日期：____________")

    # 附件六
    pdf.annex_title("附件六  特别约定（手写区）")
    pdf.p("下列特别约定经双方签章后优先于正文一般条款（解释顺序见第 17.1 条）：")
    pdf.ln(2)
    for i in range(1, 6):
        pdf.p(f"{i}.  ______________________________________________________________________________")
        pdf.ln(1.8)
    pdf.ln(6)
    pdf.p("双方签章：甲方 ______________                乙方 ______________")

    # 签署页放最后
    pdf.sign_page()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"Wrote {path}")
    print(f"size={path.stat().st_size} bytes")
