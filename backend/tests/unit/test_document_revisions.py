"""面板里那张修订清单，和它按下「接受」之后文件变成什么样。

这一份盯的是平台和房间对「一处修订」的数法必须相同。数法不同的表现不是报错，是用户点了
第 2 条、生效的是第 3 条——所以这里既查清单本身，也查「同一份文档，脚本和这个模块数出来
的条目一样多」。
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.domain.agent.skills import _NATIVE_SKILL_SRC
from app.domain.documents import revisions as rev

OFFICE = _NATIVE_SKILL_SRC / "documents" / "scripts" / "office.py"

pytest.importorskip("lxml", reason="修订解析要用 lxml，它现在是运行时依赖")

DECL = "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
OPENXML = "http://schemas.openxmlformats.org"

DOCUMENT = f"""{DECL}<w:document {W}><w:body>
<w:p><w:r><w:t>合同期限为 30 天，到期自动续约。</w:t></w:r></w:p>
<w:p><w:r><w:t>第二段提到（暂定）两个字。</w:t></w:r></w:p>
</w:body></w:document>"""

CONTENT_TYPES = (
    f'{DECL}<Types xmlns="{OPENXML}/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.'
    'openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml"'
    ' ContentType="application/vnd.openxmlformats-officedocument.'
    'wordprocessingml.document.main+xml"/>'
    "</Types>"
)

RELS = (
    f'{DECL}<Relationships xmlns="{OPENXML}/package/2006/relationships">'
    '<Relationship Id="rId1"'
    f' Type="{OPENXML}/officeDocument/2006/relationships/officeDocument"'
    ' Target="word/document.xml"/>'
    "</Relationships>"
)


@pytest.fixture
def contract(tmp_path: Path) -> Path:
    path = tmp_path / "合同.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/document.xml", DOCUMENT)
    return path


def office(*args: str | Path) -> str:
    done = subprocess.run(
        [sys.executable, str(OFFICE), *[str(a) for a in args]],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr or done.stdout
    return done.stdout


@pytest.fixture
def edited(contract: Path, tmp_path: Path) -> bytes:
    """改两处：一句替换，一处删除。清单因此该有两行。"""
    out = tmp_path / "改后.docx"
    office(
        "edit",
        contract,
        "-o",
        out,
        "--replace",
        "30 天=60 天",
        "--delete",
        "（暂定）",
        "--author",
        "芝士",
    )
    return out.read_bytes()


def texts(raw: bytes) -> list[str]:
    """可读正文，删除掉的文字不算。"""
    import re

    with zipfile.ZipFile(__import__("io").BytesIO(raw)) as z:
        body = z.read("word/document.xml").decode()
    body = re.sub(r"<w:del\b.*?</w:del>", "", body, flags=re.S)
    return [
        re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<w:p\b.*?</w:p>", body, re.S)
    ]


def test_a_replacement_is_one_row_and_carries_both_sides(edited: bytes):
    """用户要判断的是「这处改动要不要」，所以新旧文字在同一行上。"""
    rows = rev.revisions_in(edited, "合同.docx")
    assert [r.kind for r in rows] == ["replace", "delete"]
    assert (rows[0].removed, rows[0].added) == ("30 天", "60 天")
    assert (rows[1].removed, rows[1].added) == ("（暂定）", "")


def test_every_row_says_whose_change_it_is(edited: bytes):
    """别人的修订也列出来，所以每一行必须带作者——不带就等于替用户做了决定。"""
    rows = rev.revisions_in(edited, "合同.docx")
    assert {r.author for r in rows} == {"芝士"}
    assert all("author" in r.as_dict() for r in rows)


def test_the_panel_and_the_script_count_the_same_rows(edited: bytes):
    """数法不同的表现不是报错，是用户点第 2 条、生效第 3 条。"""
    from io import BytesIO  # noqa: PLC0415 - only this test needs it

    here = Path(__import__("tempfile").mkdtemp()) / "改后.docx"
    here.write_bytes(edited)
    from_script = json.loads(office("revisions", here, "--json"))
    from_panel = [r.as_dict() for r in rev.revisions_in(edited, "合同.docx")]
    assert [r["number"] for r in from_script] == [r["number"] for r in from_panel]
    assert [r["kind"] for r in from_script] == [r["kind"] for r in from_panel]
    assert [r["added"] for r in from_script] == [r["added"] for r in from_panel]
    assert BytesIO(edited).read(2) == b"PK"


def test_accepting_one_row_leaves_the_other_alone(edited: bytes):
    """逐条处理的全部意义：动第一处，第二处必须还是一处待处理的修订。

    第二处是删除，所以它的文字本来就不在可读正文里——它在 `<w:delText>` 里，等人决定。
    「没被动过」要查的是它还在文件里、还在清单上，不是正文读不到它。
    """
    made, left = rev.decide(edited, "合同.docx", accept=[1], reject=[])
    assert texts(made)[0] == "合同期限为 60 天，到期自动续约。"
    assert "（暂定）" in made.decode("utf-8", "ignore")
    assert [(r.kind, r.removed) for r in left] == [("delete", "（暂定）")]


def test_rejecting_a_row_puts_the_original_words_back(edited: bytes):
    """拒绝一处替换要把原文还回来，不是留一段空白。"""
    made, left = rev.decide(edited, "合同.docx", accept=[], reject=[1])
    assert texts(made)[0] == "合同期限为 30 天，到期自动续约。"
    assert [r.kind for r in left] == ["delete"]


def test_accepting_a_deletion_takes_the_words_with_it(edited: bytes):
    """接受删除等于文字真的走了，而不是还躺在文件里不显示。"""
    made, left = rev.decide(edited, "合同.docx", accept=[2], reject=[])
    assert texts(made)[1] == "第二段提到两个字。"
    assert "（暂定）" not in made.decode("utf-8", "ignore")
    assert [r.kind for r in left] == ["replace"]


def test_handling_everything_leaves_a_document_with_no_revisions(edited: bytes):
    """全处理完之后文件里不该再有修订标记，否则用户下载的还是一份「待定稿」。"""
    made, left = rev.decide(edited, "合同.docx", accept=[1, 2], reject=[])
    assert left == []
    body = made.decode("utf-8", "ignore")
    assert "<w:ins" not in body
    assert "<w:del" not in body


def test_a_row_number_the_listing_never_showed_is_refused(edited: bytes):
    """序号对不上说明面板拿的是另一份文档；照着改会动到别的地方。"""
    with pytest.raises(rev.RevisionsFailed, match="一共 2 处"):
        rev.decide(edited, "合同.docx", accept=[5], reject=[])


def test_deciding_nothing_is_refused(edited: bytes):
    """没点任何一条却写回文件，等于无声地改了一遍用户的文档。"""
    with pytest.raises(rev.RevisionsFailed):
        rev.decide(edited, "合同.docx", accept=[], reject=[])


def test_a_document_with_no_revisions_lists_nothing(contract: Path):
    """没有修订时清单是空的，不是报错。"""
    assert rev.revisions_in(contract.read_bytes(), "合同.docx") == []


def test_a_spreadsheet_has_no_revisions_to_offer(tmp_path: Path):
    """Excel 不带 Word 那种修订，面板不该给出一张假清单。"""
    book = tmp_path / "预算表.xlsx"
    with zipfile.ZipFile(book, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("xl/workbook.xml", f"{DECL}<workbook/>")
    with pytest.raises(rev.RevisionsUnsupported):
        rev.revisions_in(book.read_bytes(), "预算表.xlsx")


def test_something_that_is_not_a_package_is_refused(tmp_path: Path):
    """用户可能指着一个坏文件，报一句话比抛栈好。"""
    with pytest.raises(rev.RevisionsFailed):
        rev.revisions_in(b"not a zip at all", "坏文件.docx")
