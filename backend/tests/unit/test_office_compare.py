"""Two versions of an Office file, compared as a reader compares them.

A reviewer looking at a delivered report, workbook or deck asks three different
questions: which paragraphs were rewritten and how, which cells changed and from
what, which slides are new or different. A byte comparison answers none of them;
these assert that each is answered, and that formatting-only edits are named
rather than reported as "nothing changed".
"""

from __future__ import annotations

import io
import zipfile

from app.domain.documents.compare import compare

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, text in parts.items():
            z.writestr(name, text)
    return buffer.getvalue()


def document(*paragraphs: str | tuple[str, bool]) -> bytes:
    body = ""
    for item in paragraphs:
        text, bold = item if isinstance(item, tuple) else (item, False)
        look = "<w:rPr><w:b/></w:rPr>" if bold else ""
        body += f"<w:p><w:r>{look}<w:t>{text}</w:t></w:r></w:p>"
    return _zip(
        {
            "[Content_Types].xml": TYPES,
            "word/document.xml": (
                f"<w:document {W}><w:body>{body}</w:body></w:document>"
            ),
        }
    )


def workbook(cells: dict[str, str | tuple[str, str]], sheet: str = "汇总") -> bytes:
    rows: dict[str, list[str]] = {}
    for address, cell in cells.items():
        row = "".join(c for c in address if c.isdigit())
        if isinstance(cell, tuple):
            value, formula = cell
            xml = f'<c r="{address}"><f>{formula}</f><v>{value}</v></c>'
        else:
            xml = f'<c r="{address}" t="inlineStr"><is><t>{cell}</t></is></c>'
        rows.setdefault(row, []).append(xml)
    data = "".join(f'<row r="{r}">{"".join(c)}</row>' for r, c in rows.items())
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    return _zip(
        {
            "[Content_Types].xml": TYPES,
            "xl/workbook.xml": (
                f'<workbook xmlns="{main}" xmlns:r="{REL}"><sheets>'
                f'<sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
            "xl/_rels/workbook.xml.rels": (
                f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" '
                'Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
            ),
            "xl/worksheets/sheet1.xml": (
                f'<worksheet xmlns="{main}"><sheetData>{data}</sheetData></worksheet>'
            ),
        }
    )


def deck(*slides: tuple[str, str]) -> bytes:
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    parts = {"[Content_Types].xml": TYPES}
    ids, rels = "", ""
    for n, (title, text) in enumerate(slides, start=1):
        ids += f'<p:sldId id="{255 + n}" r:id="rId{n}"/>'
        rels += f'<Relationship Id="rId{n}" Type="slide" Target="slides/slide{n}.xml"/>'
        shape = (
            '<p:sp><p:nvSpPr><p:nvPr><p:ph type="{kind}"/></p:nvPr></p:nvSpPr>'
            "<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>"
        )
        parts[f"ppt/slides/slide{n}.xml"] = (
            f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree>'
            + shape.format(kind="title", text=title)
            + shape.format(kind="body", text=text)
            + "</p:spTree></p:cSld></p:sld>"
        )
    parts["ppt/presentation.xml"] = (
        f'<p:presentation xmlns:p="{p}" xmlns:r="{REL}"><p:sldIdLst>{ids}'
        "</p:sldIdLst></p:presentation>"
    )
    parts["ppt/_rels/presentation.xml.rels"] = (
        f'<Relationships xmlns="{PKG}">{rels}</Relationships>'
    )
    return _zip(parts)


def test_a_rewritten_paragraph_shows_the_words_that_changed():
    old = document("3.2 实验结果", "完成全部任务的有 96 人。", "3.3 讨论")
    new = document("3.2 实验结果", "完成全部任务的有 98 人。", "3.3 讨论")

    result = compare(old, new, "报告.docx")

    assert result is not None and result["changed"] == 1
    (row,) = [r for r in result["rows"] if r["op"] != "same"]
    assert row["op"] == "changed"
    assert {"op": "delete", "text": "96"} in row["pieces"]
    assert {"op": "insert", "text": "98"} in row["pieces"]


def test_added_and_removed_paragraphs_are_told_apart():
    old = document("引言", "旧的一段", "结论")
    new = document("引言", "结论", "附录")

    rows = compare(old, new, "报告.docx")["rows"]

    assert {"op": "removed", "before": 2, "after": None, "text": "旧的一段"} in rows
    assert {"op": "added", "before": None, "after": 3, "text": "附录"} in rows


def test_a_formatting_only_change_is_named_not_hidden():
    old = document("3.1 方法", "正文")
    new = document(("3.1 方法", True), "正文")

    result = compare(old, new, "报告.docx")

    assert result["changed"] == 0
    assert result["identical"] is False
    assert result["formatting"] == [{"after": 1, "text": "3.1 方法"}]


def test_changed_cells_carry_what_they_held_before():
    old = workbook({"A1": "组别", "B2": "40", "C2": ("31", "SUM(D2:E2)")})
    new = workbook({"A1": "组别", "B2": "41", "C2": ("33", "SUM(D2:F2)")})

    result = compare(old, new, "问卷汇总.xlsx")

    (sheet,) = result["sheets"]
    assert sheet["name"] == "汇总"
    cells = {c["address"]: c for c in sheet["cells"]}
    assert set(cells) == {"B2", "C2"}
    assert cells["B2"]["before"] == "40" and cells["B2"]["after"] == "41"
    assert cells["B2"]["formula"] is False
    assert cells["C2"]["formula"] is True


def test_a_value_a_formula_computes_differently_is_still_a_change():
    old = workbook({"C5": ("96", "SUM(C2:C4)")})
    new = workbook({"C5": ("95", "SUM(C2:C4)")})

    (cell,) = compare(old, new, "问卷汇总.xlsx")["sheets"][0]["cells"]

    assert (cell["before"], cell["after"], cell["formula"]) == ("96", "95", False)


def test_slides_are_marked_new_or_changed_in_reading_order():
    old = deck(("研究问题", "字段越少，完成率越高吗"), ("局限", "样本只来自一所学校"))
    new = deck(
        ("研究问题", "报名表单的字段越少，完成率越高吗"),
        ("实验结果", "完成 98 / 120"),
        ("局限", "样本只来自一所学校"),
    )

    slides = compare(old, new, "答辩.pptx")["slides"]

    assert [(s["op"], s["after"], s["title"]) for s in slides] == [
        ("changed", 1, "研究问题"),
        ("added", 2, "实验结果"),
        ("same", 3, "局限"),
    ]


def test_a_new_file_is_all_added():
    result = compare(None, document("第一段", "第二段"), "新报告.docx")

    assert result["new_file"] is True
    assert [r["op"] for r in result["rows"]] == ["added", "added"]


def test_a_file_that_cannot_be_read_has_no_comparison():
    assert compare(b"not a zip", b"not a zip either", "报告.docx") is None
    assert compare(None, b"x", "notes.txt") is None
