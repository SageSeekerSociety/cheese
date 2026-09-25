"""Regenerate the standard templates next to this file.

    uv run --with python-docx --with python-pptx --with openpyxl \
        python3 backend/scripts/build_document_templates.py

The templates are committed as the files this writes, so the backend needs none
of these libraries at runtime. Placeholders are written 【像这样】: one run each,
so `office.py edit --replace` and `template.py fill` find them whole.
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from pptx import Presentation
from pptx.dml.color import RGBColor as PColor
from pptx.util import Emu, Inches
from pptx.util import Pt as PPt

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "app" / "domain" / "documents" / "templates"
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
FONT = "Microsoft YaHei"


def _base_document(title: str) -> Document:
    doc = Document()
    for section in doc.sections:
        section.page_width, section.page_height = Mm(210), Mm(297)
        section.left_margin = section.right_margin = Mm(25)
        section.top_margin = section.bottom_margin = Mm(22)
        footer = section.footer.paragraphs[0]
        footer.text = "【单位名称】 · 内部资料"
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(11)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    normal.paragraph_format.line_spacing = 1.4
    normal.paragraph_format.space_after = Pt(6)
    for name, size in (("Title", 24), ("Heading 1", 16), ("Heading 2", 13)):
        style = doc.styles[name]
        style.font.name = FONT
        style.font.size = Pt(size)
        style.font.color.rgb = ACCENT
        style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    doc.add_paragraph(title, style="Title")
    return doc


def _meta_table(doc: Document, rows: list[tuple[str, str]]) -> None:
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Light Grid Accent 1"
    for i, (key, value) in enumerate(rows):
        table.cell(i, 0).text = key
        table.cell(i, 1).text = value


def report() -> None:
    doc = _base_document("【报告标题】")
    _meta_table(
        doc, [("编写人", "【姓名】"), ("日期", "【日期】"), ("版本", "【版本】")]
    )
    doc.add_heading("摘要", 1)
    doc.add_paragraph("【用三到五句话概括报告的结论与建议。】")
    doc.add_heading("一、背景与目标", 1)
    doc.add_paragraph("【说明这项工作的来由、要回答的问题和衡量标准。】")
    doc.add_heading("二、主要发现", 1)
    doc.add_heading("2.1 【发现一】", 2)
    doc.add_paragraph("【发现一的依据与数据，注明来源。】")
    doc.add_heading("2.2 【发现二】", 2)
    doc.add_paragraph("【发现二的依据与数据，注明来源。】")
    doc.add_heading("三、数据概览", 1)
    table = doc.add_table(rows=3, cols=3)
    table.style = "Light Grid Accent 1"
    for c, head in enumerate(("指标", "数值", "来源")):
        table.cell(0, c).text = head
    for r in (1, 2):
        for c in range(3):
            table.cell(r, c).text = "【】"
    doc.add_heading("四、结论与建议", 1)
    doc.add_paragraph("【结论】", style="List Number")
    doc.add_paragraph("【建议】", style="List Number")
    doc.add_heading("参考来源", 1)
    doc.add_paragraph("【文件名 / 页码 / 链接】", style="List Bullet")
    doc.save(HERE / "report.docx")


def proposal() -> None:
    doc = _base_document("【方案名称】")
    _meta_table(doc, [("提出人", "【姓名】"), ("日期", "【日期】"), ("状态", "草案")])
    doc.add_heading("一、要解决的问题", 1)
    doc.add_paragraph("【现状、痛点与影响范围。】")
    doc.add_heading("二、目标与范围", 1)
    doc.add_paragraph("【做什么、不做什么、成功的标准。】")
    doc.add_heading("三、方案设计", 1)
    doc.add_paragraph("【方案要点，一段一件事。】")
    doc.add_heading("四、实施计划", 1)
    table = doc.add_table(rows=4, cols=4)
    table.style = "Light Grid Accent 1"
    for c, head in enumerate(("阶段", "内容", "负责人", "时间")):
        table.cell(0, c).text = head
    for r in range(1, 4):
        for c in range(4):
            table.cell(r, c).text = "【】"
    doc.add_heading("五、资源与风险", 1)
    doc.add_paragraph("【需要的资源】", style="List Bullet")
    doc.add_paragraph("【主要风险与应对】", style="List Bullet")
    doc.save(HERE / "proposal.docx")


def weekly() -> None:
    doc = _base_document("【项目名称】周报")
    _meta_table(doc, [("周期", "【起止日期】"), ("填写人", "【姓名】")])
    doc.add_heading("本周完成", 1)
    doc.add_paragraph("【完成的事项，附链接或成果名】", style="List Bullet")
    doc.add_heading("进行中", 1)
    doc.add_paragraph("【事项 · 进度 · 预计完成】", style="List Bullet")
    doc.add_heading("问题与需要的支持", 1)
    doc.add_paragraph("【问题，以及需要谁在什么时候做什么】", style="List Bullet")
    doc.add_heading("下周计划", 1)
    doc.add_paragraph("【计划事项】", style="List Bullet")
    doc.save(HERE / "weekly.docx")


def _deck(title: str, subtitle: str, pages: list[tuple[str, list[str]]], name: str):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    cover = prs.slides.add_slide(prs.slide_layouts[0])
    cover.shapes.title.text = title
    cover.placeholders[1].text = subtitle
    for heading, bullets in pages:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.18))
        bar.fill.solid()
        bar.fill.fore_color.rgb = PColor(0x1F, 0x4E, 0x79)
        bar.line.fill.background()
        slide.shapes.title.text = heading
        body = slide.placeholders[1].text_frame
        body.text = bullets[0]
        for line in bullets[1:]:
            body.add_paragraph().text = line
        for paragraph in body.paragraphs:
            for run in paragraph.runs:
                run.font.size = PPt(20)
    for slide in prs.slides:
        slide.shapes.title.width = Emu(prs.slide_width - Inches(1))
    prs.save(HERE / name)


def project_deck() -> None:
    _deck(
        "【项目名称】汇报",
        "【汇报人】 · 【日期】",
        [
            ("目标与进展概览", ["【本阶段目标】", "【完成度：x / y】"]),
            ("关键成果", ["【成果一】", "【成果二】", "【成果三】"]),
            ("问题与风险", ["【问题 · 影响 · 应对】"]),
            ("下一步计划", ["【计划事项 · 负责人 · 时间】"]),
            ("需要的支持", ["【需要谁、做什么、什么时候】"]),
        ],
        "project-deck.pptx",
    )


def research_deck() -> None:
    _deck(
        "【调研主题】",
        "调研汇报 · 【日期】",
        [
            ("调研问题与方法", ["【要回答的问题】", "【资料来源与方法】"]),
            ("主要发现", ["【发现一（来源）】", "【发现二（来源）】"]),
            ("对比与分析", ["【对象 A 与 B 的差异】"]),
            ("结论与建议", ["【结论】", "【建议】"]),
            ("参考来源", ["【文件名 / 页码 / 链接】"]),
        ],
        "research-deck.pptx",
    )


def analysis_book() -> None:
    wb = Workbook()
    data = wb.active
    data.title = "数据"
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F4E79")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    data.append(["类别", "指标一", "指标二", "备注"])
    for i in range(1, 6):
        data.append([f"【类别{i}】", 0, 0, ""])
    for row in data.iter_rows(min_row=1, max_row=6, max_col=4):
        for cell in row:
            cell.border = border
            if cell.row == 1:
                cell.font, cell.fill = head, fill
                cell.alignment = Alignment(horizontal="center")
            elif cell.column in (2, 3):
                cell.number_format = "#,##0.00"
    data.column_dimensions["A"].width = 16
    data.column_dimensions["D"].width = 30
    summary = wb.create_sheet("汇总")
    summary.append(["统计项", "指标一", "指标二"])
    summary.append(["合计", "=SUM(数据!B2:B6)", "=SUM(数据!C2:C6)"])
    summary.append(["平均", "=AVERAGE(数据!B2:B6)", "=AVERAGE(数据!C2:C6)"])
    summary.append(["最大", "=MAX(数据!B2:B6)", "=MAX(数据!C2:C6)"])
    summary.append(["指标二 / 指标一", "", '=IF(B2=0,"",C2/B2)'])
    for row in summary.iter_rows(min_row=1, max_row=5, max_col=3):
        for cell in row:
            cell.border = border
            if cell.row == 1:
                cell.font, cell.fill = head, fill
            elif cell.column > 1:
                cell.number_format = "#,##0.00"
    summary.column_dimensions["A"].width = 18
    chart = BarChart()
    chart.type = "col"
    chart.grouping = "clustered"
    chart.title = "各类别指标"
    # openpyxl writes both axes as deleted unless told otherwise; Excel then
    # draws no axis and the editor draws the labels on top of each other.
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.x_axis.axPos = "b"
    chart.y_axis.majorGridlines = None
    chart.width, chart.height = 16, 8
    values = Reference(data, min_col=2, max_col=3, min_row=1, max_row=6)
    chart.add_data(values, titles_from_data=True)
    chart.set_categories(Reference(data, min_col=1, min_row=2, max_row=6))
    summary.add_chart(chart, "E2")
    notes = wb.create_sheet("说明")
    notes.append(["数据来源", "【文件名 / 表名 / 链接】"])
    notes.append(["口径", "【指标的定义与单位】"])
    notes.append(["更新时间", "【日期】"])
    notes.column_dimensions["A"].width = 12
    notes.column_dimensions["B"].width = 50
    wb.save(HERE / "analysis.xlsx")


if __name__ == "__main__":
    report()
    proposal()
    weekly()
    project_deck()
    research_deck()
    analysis_book()
    print("templates written to", HERE)
