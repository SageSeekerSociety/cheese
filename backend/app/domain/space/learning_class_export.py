"""学情报告的三种文件：PDF、Excel、Word（§ learning analysis, step 3）。

输入是 ``SpaceLearningClassService.report`` 给的那份整体数据 —— 那里已经只剩全班整
体、代号与遮蔽过的原话，这里不再读库，只排版。三种格式排的是同一份内容：先把报告
摆成一列「块」（标题、段落、表格），PDF 与 Word 照着这一列写，Excel 每一节一张表。

- PDF：``pymupdf``（已有依赖）的 Story，HTML 进、分页的 PDF 出，中文字体它自带。
- Excel：``openpyxl``。
- Word：``python-docx``。
"""

from __future__ import annotations

import html
import io
from dataclasses import dataclass, field
from datetime import datetime

from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from app.domain.space.learning_insight_analysis import LOCAL_ZONE

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

NOT_ANALYSED = "不足 3 人，不分析"


@dataclass
class Table:
    title: str
    header: list[str]
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Section:
    title: str
    paragraphs: list[str] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)


def _pct(value: float | None) -> str:
    return f"{round(value * 100)}%" if value is not None else "—"


def _num(value: object) -> str:
    return "—" if value is None else str(value)


def _time(ms: int | None) -> str:
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, LOCAL_ZONE).strftime("%m-%d %H:%M")


def sections(report: dict) -> tuple[str, list[str], list[Section]]:
    """(标题, 开头几段说明, 各节)。"""
    title = f"「{report['spaceName']}」学情分析报告"
    generated: datetime = report["generatedAt"]
    lead = [
        f"生成于 {generated.astimezone(LOCAL_ZONE):%Y-%m-%d %H:%M}，"
        f"纳入分析的成员 {report['members']} 人。",
        "这份报告只含全班整体，不含任何成员的姓名或个人结果；摘录只标本空间固定代号，"
        "对话里的姓名、学号、手机号、邮箱已遮掉。任何一组不足 3 人时不做分析。",
    ]
    out: list[Section] = []
    if report.get("tasks") is not None:
        table = Table(
            title="题目完成情况",
            header=["题目", "未领", "进行中", "已提交", "已通过"],
        )
        for task in report["tasks"]:
            if not task["analysed"]:
                table.rows.append([task["name"], NOT_ANALYSED, "", "", ""])
                continue
            table.rows.append(
                [
                    task["name"],
                    _num(task["notStarted"]),
                    _num(task["inProgress"]),
                    _num(task["submitted"]),
                    _num(task["passed"]),
                ]
            )
        out.append(Section(title="题目完成情况（按题汇总）", tables=[table]))
    points = report["points"]
    if report.get("includePoints"):
        summary = Table(
            title="知识点", header=["知识点", "涉及人数", "卡住人数", "卡住率"]
        )
        wrong = Table(title="常见误解", header=["知识点", "误解", "人数"])
        for point in points:
            if not point["analysed"]:
                summary.rows.append([point["name"], NOT_ANALYSED, "", ""])
                continue
            summary.rows.append(
                [
                    point["name"],
                    _num(point["involved"]),
                    _num(point["stuck"]),
                    _pct(point["stuckRate"]),
                ]
            )
            for m in point["misconceptions"]:
                wrong.rows.append([point["name"], m["label"], str(m["people"])])
        out.append(
            Section(
                title="各知识点卡住的人数和常见误解",
                paragraphs=["「卡住」= AI 判断为不稳或薄弱；人数不足 3 人的误解不列。"],
                tables=[summary, wrong],
            )
        )
    if report.get("includeAdvice"):
        advice = Table(title="讲课建议", header=["知识点", "讲课建议"])
        for point in points:
            if point.get("advice"):
                advice.rows.append([point["name"], point["advice"]])
        out.append(
            Section(
                title="讲课建议",
                paragraphs=[] if advice.rows else ["还没有生成讲课建议。"],
                tables=[advice] if advice.rows else [],
            )
        )
    if report.get("includeExcerpts"):
        quotes = Table(
            title="匿名对话摘录", header=["知识点", "误解", "代号", "题目", "对话"]
        )
        for point in points:
            for m in point["misconceptions"]:
                for excerpt in m.get("excerpts") or []:
                    said = "\n".join(
                        (f"成员 {msg['code']}" if msg["role"] == "member" else "芝士")
                        + f"（{_time(msg['at'])}）：{msg['text']}"
                        for msg in excerpt["messages"]
                    )
                    quotes.rows.append(
                        [
                            point["name"],
                            m["label"],
                            f"成员 {excerpt['code']}",
                            excerpt["taskName"],
                            said,
                        ]
                    )
        out.append(
            Section(
                title="匿名对话摘录",
                paragraphs=["每个误解最多 3 段，出自不同的人；只标代号。"]
                + ([] if quotes.rows else ["没有可摘录的对话。"]),
                tables=[quotes] if quotes.rows else [],
            )
        )
    return title, lead, out


def render(report: dict, fmt: str) -> bytes:
    title, lead, parts = sections(report)
    if fmt == "pdf":
        return _pdf(title, lead, parts)
    if fmt == "xlsx":
        return _xlsx(title, lead, parts)
    if fmt == "docx":
        return _docx(title, lead, parts)
    raise ValueError(fmt)


def _pdf(title: str, lead: list[str], parts: list[Section]) -> bytes:
    import pymupdf  # deferred-import: start-up cost, a native library paid only per PDF

    def esc(text: str) -> str:
        return html.escape(text).replace("\n", "<br/>")

    body = [f"<h1>{esc(title)}</h1>"]
    body += [f"<p class='lead'>{esc(p)}</p>" for p in lead]
    for part in parts:
        body.append(f"<h2>{esc(part.title)}</h2>")
        body += [f"<p>{esc(p)}</p>" for p in part.paragraphs]
        for table in part.tables:
            head = "".join(f"<th>{esc(h)}</th>" for h in table.header)
            rows = "".join(
                "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>"
                for row in table.rows
            )
            body.append(f"<table><tr>{head}</tr>{rows}</table>")
    css = (
        "body{font-size:10pt;} h1{font-size:16pt;} h2{font-size:13pt;margin-top:12pt;}"
        "p.lead{color:#555;} table{border-collapse:collapse;width:100%;margin:6pt 0;}"
        "th,td{border:1px solid #bbb;padding:3pt;text-align:left;vertical-align:top;}"
        "th{background:#f0f0f0;}"
    )
    story = pymupdf.Story(html="".join(body), user_css=css)
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    page = pymupdf.paper_rect("a4")
    frame = page + (40, 40, -40, -40)
    more = True
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(frame)
        story.draw(device)
        writer.end_page()
    writer.close()
    return out.getvalue()


def _xlsx(title: str, lead: list[str], parts: list[Section]) -> bytes:
    book = Workbook()
    cover = book.active
    assert cover is not None
    cover.title = "说明"
    cover.append([title])
    cover["A1"].font = Font(bold=True, size=14)
    for line in lead:
        cover.append([line])
    cover.column_dimensions["A"].width = 100
    used: set[str] = {"说明"}
    for part in parts:
        for table in part.tables:
            name = table.title[:31]
            while name in used:
                name = name[:29] + "_2"
            used.add(name)
            sheet = book.create_sheet(name)
            sheet.append(table.header)
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            for row in table.rows:
                sheet.append(row)
            for index, _ in enumerate(table.header, start=1):
                letter = sheet.cell(row=1, column=index).column_letter
                sheet.column_dimensions[letter].width = 18 if index == 1 else 30
            for row in sheet.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
        if not part.tables:
            name = part.title[:31]
            if name not in used:
                used.add(name)
                sheet = book.create_sheet(name)
                for line in part.paragraphs:
                    sheet.append([line])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _docx(title: str, lead: list[str], parts: list[Section]) -> bytes:
    doc = Document()
    doc.add_heading(title, level=1)
    for line in lead:
        doc.add_paragraph(line)
    for part in parts:
        doc.add_heading(part.title, level=2)
        for line in part.paragraphs:
            doc.add_paragraph(line)
        for table in part.tables:
            grid = doc.add_table(rows=1, cols=len(table.header))
            grid.style = "Table Grid"
            for cell, text in zip(grid.rows[0].cells, table.header, strict=True):
                cell.text = text
            for row in table.rows:
                cells = grid.add_row().cells
                for cell, text in zip(cells, row, strict=True):
                    cell.text = text
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
