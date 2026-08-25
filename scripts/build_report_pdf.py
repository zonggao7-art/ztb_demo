# -*- coding: utf-8 -*-
"""将可行性分析报告 Markdown 渲染为规范 PDF（fpdf2，中文字体 + 页眉页脚 + 页码）。"""
import re
from fpdf import FPDF, FontFace

MD = r"D:\DEMO\zhaotoubiao_demo\docs\Milvus与MySQL数据库阿里云迁移可行性分析报告_20260813.md"
OUT = r"D:\DEMO\zhaotoubiao_demo\docs\Milvus与MySQL数据库阿里云迁移可行性分析报告_20260813.pdf"

FONT_SONG = "C:/Windows/Fonts/simsun.ttc"
FONT_SONG_B = "C:/Windows/Fonts/simsunb.ttf"
FONT_HEI = "C:/Windows/Fonts/simhei.ttf"

PAGE_W = 210
LM, RM, TM, BM = 20, 18, 24, 20
CONTENT_W = PAGE_W - LM - RM  # 172

PRIMARY = (26, 58, 107)
HEAD_BG = (26, 58, 107)
GRAY = (120, 120, 120)
BAR = (180, 200, 225)


class Report(FPDF):
    def header(self):
        if self.page_no() <= 1:
            return
        self.set_font("song", "", 8)
        self.set_text_color(*GRAY)
        self.set_y(11)
        self.cell(CONTENT_W / 2, 5, "Milvus 与 MySQL 数据库阿里云迁移可行性分析报告", align="L")
        self.cell(CONTENT_W / 2, 5, "ZTB-MIG-FS-20260813", align="R")
        self.ln(3)
        self.set_draw_color(200, 200, 200)
        self.line(LM, self.get_y(), PAGE_W - RM, self.get_y())
        self.set_y(TM)

    def footer(self):
        self.set_y(-14)
        self.set_font("song", "", 8)
        self.set_text_color(*GRAY)
        self.cell(0, 10, "第 %d 页 / 共 {nb} 页" % self.page_no(), align="C")


def write_rich(pdf, text, size, h, base="song", bold="hei"):
    text = text.replace("`", "")
    parts = text.split("**")
    for i, part in enumerate(parts):
        if part == "":
            continue
        pdf.set_font(bold if i % 2 == 1 else base, "", size)
        pdf.write(h, part)


def parse_md(text):
    lines = text.split("\n")
    blocks = []
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].strip()
        if s == "":
            i += 1
            continue
        if re.match(r"^-{3,}$", s):
            blocks.append({"kind": "hr"})
            i += 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            blocks.append({"kind": "h", "level": len(m.group(1)), "text": m.group(2).strip()})
            i += 1
            continue
        if s.startswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            rows = [r for r in rows if not all(re.match(r"^:?-{2,}:?$", c) for c in r)]
            if rows:
                blocks.append({"kind": "table", "rows": rows})
            continue
        if s.startswith(">"):
            q = []
            while i < n and lines[i].strip().startswith(">"):
                q.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append({"kind": "quote", "text": " ".join(q)})
            continue
        if re.match(r"^[-*]\s+", s):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            blocks.append({"kind": "ul", "items": items})
            continue
        if re.match(r"^\d+\.\s+", s):
            items = []
            while i < n and re.match(r"^\d+\.\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s+", "", lines[i].strip()))
                i += 1
            blocks.append({"kind": "ol", "items": items})
            continue
        blocks.append({"kind": "p", "text": s})
        i += 1
    return blocks


def render_heading(pdf, level, text):
    if level == 1 and not re.match(r"^\d+\.", text):
        # 文档主标题
        pdf.ln(4)
        pdf.set_font("hei", "", 21)
        pdf.set_text_color(*PRIMARY)
        pdf.multi_cell(CONTENT_W, 11, text, align="C")
        pdf.ln(3)
        pdf.set_draw_color(*PRIMARY)
        pdf.set_line_width(0.7)
        y = pdf.get_y()
        pdf.line(LM + 25, y, PAGE_W - RM - 25, y)
        pdf.set_line_width(0.2)
        pdf.ln(7)
    elif level == 1:
        pdf.ln(6)
        pdf.set_font("hei", "", 16)
        pdf.set_text_color(*PRIMARY)
        pdf.multi_cell(CONTENT_W, 8, text, align="L")
        pdf.ln(1)
        pdf.set_draw_color(*PRIMARY)
        pdf.set_line_width(0.6)
        y = pdf.get_y()
        pdf.line(LM, y, PAGE_W - RM, y)
        pdf.set_line_width(0.2)
        pdf.ln(3)
    elif level == 2:
        pdf.ln(4)
        pdf.set_font("hei", "", 13)
        pdf.set_text_color(30, 60, 105)
        pdf.multi_cell(CONTENT_W, 6.5, text, align="L")
        pdf.ln(1.5)
    elif level == 3:
        pdf.ln(3)
        pdf.set_font("hei", "", 11.5)
        pdf.set_text_color(40, 70, 120)
        pdf.multi_cell(CONTENT_W, 6, text, align="L")
        pdf.ln(1)
    else:
        pdf.ln(2)
        pdf.set_font("hei", "", 10.5)
        pdf.set_text_color(50, 50, 50)
        pdf.multi_cell(CONTENT_W, 6, text, align="L")
        pdf.ln(1)
    pdf.set_text_color(0, 0, 0)


def render_para(pdf, text):
    pdf.set_font("song", "", 10.5)
    pdf.set_text_color(20, 20, 20)
    pdf.set_x(LM)
    write_rich(pdf, text, 10.5, 6)
    pdf.ln(7)


def render_quote(pdf, text):
    indent = 6
    x0 = LM + 1.5
    y0 = pdf.get_y()
    pdf.set_font("song", "", 9.5)
    pdf.set_text_color(85, 85, 85)
    pdf.set_x(LM + indent)
    write_rich(pdf, text, 9.5, 5.5, base="song", bold="hei")
    y_bottom = pdf.get_y()
    pdf.set_fill_color(*BAR)
    pdf.rect(x0, y0, 0.9, max(y_bottom - y0, 4), "F")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)


def render_list(pdf, items, ordered=False):
    pdf.set_text_color(20, 20, 20)
    for idx, item in enumerate(items):
        pdf.set_font("hei", "", 10)
        pdf.set_x(LM)
        pdf.cell(6, 6, ("%d." % (idx + 1)) if ordered else "\u25cf", align="R")
        pdf.set_font("song", "", 10.5)
        pdf.set_x(LM + 8)
        write_rich(pdf, item, 10.5, 6)
        pdf.ln(6.5)


def render_table(pdf, rows):
    headers = rows[0]
    data = rows[1:]
    pdf.ln(2)
    pdf.set_font("song", "", 8.5)
    pdf.set_text_color(25, 25, 25)
    head_face = FontFace(family="hei", size_pt=8.5, color=(255, 255, 255), fill_color=HEAD_BG)
    def clean(c):
        return c.replace("`", "").replace("**", "")

    with pdf.table(
        borders_layout="ALL",
        width=CONTENT_W,
        text_align="LEFT",
        line_height=5.0,
        padding=1.6,
        headings_style=head_face,
        cell_fill_color=(247, 249, 252),
        cell_fill_mode="ROWS",
    ) as table:
        hrow = table.row()
        for h in headers:
            hrow.cell(clean(h))
        for row in data:
            r = table.row()
            for c in row:
                r.cell(clean(c))
    pdf.ln(3)
    pdf.set_text_color(0, 0, 0)


def main():
    with open(MD, encoding="utf-8") as f:
        blocks = parse_md(f.read())

    pdf = Report(format="A4")
    pdf.add_font("song", "", FONT_SONG)
    pdf.add_font("song", "B", FONT_SONG_B)
    pdf.add_font("hei", "", FONT_HEI)
    pdf.add_font("hei", "B", FONT_HEI)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(True, margin=BM)
    pdf.set_margins(LM, TM, RM)
    pdf.add_page()

    for b in blocks:
        k = b["kind"]
        if k == "hr":
            pdf.ln(2)
            pdf.set_draw_color(180, 180, 180)
            pdf.line(LM, pdf.get_y(), PAGE_W - RM, pdf.get_y())
            pdf.ln(4)
        elif k == "h":
            render_heading(pdf, b["level"], b["text"])
        elif k == "p":
            render_para(pdf, b["text"])
        elif k == "table":
            render_table(pdf, b["rows"])
        elif k == "quote":
            render_quote(pdf, b["text"])
        elif k == "ul":
            render_list(pdf, b["items"], ordered=False)
        elif k == "ol":
            render_list(pdf, b["items"], ordered=True)

    pdf.output(OUT)
    print("PDF written:", OUT)
    print("Pages:", pdf.page_no())


if __name__ == "__main__":
    main()
