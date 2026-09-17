"""`office.py` 改一份 Word，改动之外的一切都不许动。

#1086 把这件事列为必须先测量的一项：解包、改 XML、重新打包之后 Word 打开是否完全正常。
手工验过一次不足以守住它——这个脚本的失败模式全是安静的。文件能打开、文字提取正确、
退出码为 0，而交付出去的文档多改了一处，或者丢了一段原本的修订历史。

所以这里从命令行调它，只看行为：改动落在哪几处、别的部件的字节有没有变、拒绝全部修订
能不能逐字回到原文。构造夹具时不用 python-docx——Word 的部件是普通的 XML，手写一份反倒
能把 run 被拆开、带 rsid、带加粗这些真实文档才有的形状固定下来。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.domain.agent.skills import _NATIVE_SKILL_SRC

OFFICE = _NATIVE_SKILL_SRC / "documents" / "scripts" / "office.py"

pytest.importorskip(
    "lxml", reason="office.py 用 lxml 保住命名空间前缀，沙箱里由 uv run --with 取用"
)

DECLARATION = "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def text_run(rsid: str, text: str, bold: bool = False) -> str:
    space = ' xml:space="preserve"' if text != text.strip() else ""
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r w:rsidR="{rsid}">{rpr}<w:t{space}>{text}</w:t></w:r>'


def paragraph(rsid: str, *runs: str) -> str:
    return f'<w:p w:rsidR="{rsid}">{"".join(runs)}</w:p>'


#: 正文。三处「旧的说法」：标题后的第一段里一处，下一段一处，表格里一处。
#: 第一处刻意被拆在三个 run 里，中间那个是加粗的——真实文档里一句话被拼写检查和
#: 格式切换拆开是常态，而拆开正是定位最容易错的地方。末段后面那句里「甲方」出现两次，
#: 用来盯住「同一段里的第二处」。
BODY = "".join(
    [
        paragraph("00AA0001", text_run("00AA0001", "第三季度预算说明")),
        paragraph(
            "00AA0002",
            text_run("00AA0002", "本季度预算按 "),
            text_run("00AA0003", "旧的说法", bold=True),
            text_run("00AA0004", " 编制，合计 120 万元。"),
        ),
        paragraph(
            "00AA0005", text_run("00AA0005", "下一段提到旧的说法，用来测试多处匹配。")
        ),
        "<w:tbl><w:tr>"
        f"<w:tc>{paragraph('00AA0006', text_run('00AA0006', '差旅'))}</w:tc>"
        f"<w:tc>{paragraph('00AA0007', text_run('00AA0007', '旧的说法'))}</w:tc>"
        "</w:tr></w:tbl>",
        paragraph("00AA0008", text_run("00AA0008", "末段。")),
        paragraph("00AA0009", text_run("00AA0009", "附注：甲方甲方各付一半。")),
    ]
)

DOCUMENT = f"{DECLARATION}<w:document {W}><w:body>{BODY}</w:body></w:document>"

#: 排版住在别的部件里，这一份在这里只为了证明改正文不会碰到它。
STYLES = (
    f'{DECLARATION}<w:styles {W}><w:style w:styleId="Normal" w:type="paragraph">'
    '<w:name w:val="Normal"/></w:style></w:styles>'
)

OPENXML = "http://schemas.openxmlformats.org"

CONTENT_TYPES = (
    f'{DECLARATION}<Types xmlns="{OPENXML}/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.'
    'openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml"'
    ' ContentType="application/vnd.openxmlformats-officedocument.'
    'wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml"'
    ' ContentType="application/vnd.openxmlformats-officedocument.'
    'wordprocessingml.styles+xml"/>'
    "</Types>"
)

RELS = (
    f'{DECLARATION}<Relationships xmlns="{OPENXML}/package/2006/relationships">'
    '<Relationship Id="rId1"'
    f' Type="{OPENXML}/officeDocument/2006/relationships/officeDocument"'
    ' Target="word/document.xml"/>'
    "</Relationships>"
)

PARTS = {
    "[Content_Types].xml": CONTENT_TYPES,
    "_rels/.rels": RELS,
    "word/document.xml": DOCUMENT,
    "word/styles.xml": STYLES,
}


@pytest.fixture
def report(tmp_path: Path) -> Path:
    path = tmp_path / "报告.docx"
    with zipfile.ZipFile(path, "w") as z:
        for name, body in PARTS.items():
            z.writestr(name, body)
    return path


def office(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(OFFICE), *[str(a) for a in args]],
        capture_output=True,
        text=True,
    )


def ran(*args: str | Path) -> str:
    done = office(*args)
    assert done.returncode == 0, done.stderr or done.stdout
    return done.stdout


def refused(*args: str | Path) -> str:
    done = office(*args)
    assert done.returncode != 0, f"应当拒绝，却成功了：{done.stdout}"
    return done.stderr or done.stdout


def part(path: Path, name: str = "word/document.xml") -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode("utf-8")


def visible(path: Path) -> list[str]:
    """每段现在读出来是什么，删除的文字不算——它在文件里，但读者看不见。"""
    body = re.sub(r"<w:del\b.*?</w:del>", "", part(path), flags=re.S)
    return [
        re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<w:p\b.*?</w:p>", body, re.S)
    ]


def test_text_prints_the_paragraphs_with_their_numbers(report: Path):
    """段号是后面每一句报错的唯一坐标，表格里的那一段也得数进去。"""
    out = ran("text", report)
    assert "第三季度预算说明" in out
    assert "本季度预算按 旧的说法 编制，合计 120 万元。" in out
    assert "旧的说法" in out.split("差旅")[1]


def test_occurrence_changes_only_the_one_it_names(report: Path, tmp_path: Path):
    """`--occurrence 2` 是第二处，不是前两处。

    这是最值得守的一条：多改的那一处本身是格式正确的修订，`validate` 因此照样通过，
    而用户拿到的文件里有一处他没要求的改动。
    """
    out = tmp_path / "改后.docx"
    ran(
        "edit",
        report,
        "-o",
        out,
        "--replace",
        "旧的说法=新的说法",
        "--occurrence",
        "2",
        "--author",
        "芝士",
    )
    assert len(re.findall(r"<w:ins\b", part(out))) == 1
    lines = visible(out)
    assert lines[1] == "本季度预算按 旧的说法 编制，合计 120 万元。"
    assert lines[2] == "下一段提到新的说法，用来测试多处匹配。"
    assert lines[4] == "旧的说法"


def test_occurrence_reaches_a_paragraph_inside_a_table(report: Path, tmp_path: Path):
    """第三处在表格单元格里，数的时候不能漏掉它。"""
    out = tmp_path / "改后.docx"
    ran(
        "edit",
        report,
        "-o",
        out,
        "--replace",
        "旧的说法=新的说法",
        "--occurrence",
        "3",
        "--author",
        "芝士",
    )
    lines = visible(out)
    assert lines[1] == "本季度预算按 旧的说法 编制，合计 120 万元。"
    assert lines[2] == "下一段提到旧的说法，用来测试多处匹配。"
    assert lines[4] == "新的说法"


def test_an_occurrence_that_is_not_there_is_refused(report: Path, tmp_path: Path):
    """只有三处的时候要第四处，是提的人算错了，不是让脚本自己挑一处改。"""
    out = tmp_path / "改后.docx"
    said = refused(
        "edit",
        report,
        "-o",
        out,
        "--replace",
        "旧的说法=新的说法",
        "--occurrence",
        "4",
    )
    assert "3 次" in said
    assert not out.exists()


def test_several_matches_without_saying_which_is_refused(report: Path, tmp_path: Path):
    """说不清改哪一处时停下来，比挑一处改了再告诉用户要好。"""
    out = tmp_path / "改后.docx"
    said = refused("edit", report, "-o", out, "--replace", "旧的说法=新的说法")
    assert "出现了 3 次" in said
    assert not out.exists()


def test_all_takes_every_occurrence(report: Path, tmp_path: Path):
    """`--all` 要改到全部三处，一处在表格里。"""
    out = tmp_path / "改后.docx"
    ran("edit", report, "-o", out, "--delete", "旧的说法", "--all", "--author", "芝士")
    assert len(re.findall(r"<w:del\b", part(out))) == 3
    lines = visible(out)
    assert lines[1] == "本季度预算按  编制，合计 120 万元。"
    assert lines[2] == "下一段提到，用来测试多处匹配。"
    assert lines[4] == ""


def test_all_takes_two_matches_standing_in_the_same_paragraph(
    report: Path, tmp_path: Path
):
    """同一段里出现两次，两次都要删掉。

    删除的文字从可读正文里消失，下一次查找因此从更短的正文上继续。这一步很容易被当成
    可能死循环而提前收手，代价是 `--all` 静默地只改了第一处——脚本还会报「1 处」，
    读的人得自己发现这个数不对。
    """
    out = tmp_path / "改后.docx"
    said = ran(
        "edit", report, "-o", out, "--delete", "甲方", "--all", "--author", "芝士"
    )
    assert "2 处" in said
    assert visible(out)[6] == "附注：各付一半。"


def test_rejecting_every_revision_gets_the_original_back(report: Path, tmp_path: Path):
    """交付前那一步的不变量：拒绝全部修订之后，正文与原文档逐字相同。"""
    out = tmp_path / "改后.docx"
    ran(
        "edit",
        report,
        "-o",
        out,
        "--replace",
        "旧的说法=新的说法",
        "--all",
        "--author",
        "芝士",
    )
    said = ran("validate", out, "--base", report)
    assert "逐字相同" in said
    assert "校验通过" in said


def test_everything_the_edit_did_not_touch_stays_byte_for_byte(
    report: Path, tmp_path: Path
):
    """「改一句话不动排版」只有在别的部件一个字节都没变时才成立。"""
    out = tmp_path / "改后.docx"
    ran(
        "edit",
        report,
        "-o",
        out,
        "--replace",
        "旧的说法=新的说法",
        "--occurrence",
        "1",
        "--author",
        "芝士",
    )
    with zipfile.ZipFile(report) as before, zipfile.ZipFile(out) as after:
        assert after.namelist() == before.namelist()
        for name in before.namelist():
            if name == "word/document.xml":
                continue
            assert after.read(name) == before.read(name), name


def test_the_xml_declaration_survives_the_rewrite(report: Path, tmp_path: Path):
    """Word 每个部件都写 standalone="yes"；写回时丢掉它是改动之外的改动。"""
    out = tmp_path / "改后.docx"
    ran(
        "edit",
        report,
        "-o",
        out,
        "--replace",
        "旧的说法=新的说法",
        "--occurrence",
        "1",
        "--author",
        "芝士",
    )
    assert "standalone='yes'" in part(out)[:120]


def test_a_partial_match_leaves_the_text_around_it_with_its_history(
    report: Path, tmp_path: Path
):
    """命中落在一个 run 中间时，剩下的头尾还是原来那段文字，rsid 要跟着留下。

    Word 用 rsid 记住每段文字是哪一次保存写下的，合并两份修改稿时靠它对齐。因为有人
    改了句子中间三个字就把前后文的 rsid 抹掉，等于宣称整句都是这次写的。
    """
    out = tmp_path / "改后.docx"
    ran(
        "edit",
        report,
        "-o",
        out,
        "--replace",
        "季度预算=本季预算",
        "--occurrence",
        "2",
        "--author",
        "芝士",
    )
    body = part(out)
    changed = re.search(
        r"<w:p\b(?:(?!</w:p>).)*?本季预算(?:(?!</w:p>).)*?</w:p>", body, re.S
    )
    assert changed is not None
    # 被切开的那个 run 原本是 00AA0002，头尾两截都还应当带着它。
    assert changed.group(0).count('w:rsidR="00AA0002"') >= 2
    assert "逐字相同" in ran("validate", out, "--base", report)


def test_plain_leaves_no_revision_marks(report: Path, tmp_path: Path):
    """用户说「给我干净的最终版」时，文件里不该留下修订。"""
    out = tmp_path / "改后.docx"
    ran(
        "edit",
        report,
        "-o",
        out,
        "--plain",
        "--replace",
        "旧的说法=新的说法",
        "--all",
    )
    body = part(out)
    assert "<w:ins" not in body
    assert "<w:del" not in body
    assert "旧的说法" not in body
    assert visible(out)[1] == "本季度预算按 新的说法 编制，合计 120 万元。"


def test_the_bold_run_keeps_its_formatting(report: Path, tmp_path: Path):
    """第一处「旧的说法」是加粗的，换掉的文字要接着加粗。"""
    out = tmp_path / "改后.docx"
    ran(
        "edit",
        report,
        "-o",
        out,
        "--plain",
        "--replace",
        "旧的说法=新的说法",
        "--occurrence",
        "1",
    )
    body = part(out)
    new_run = re.search(r"<w:r\b(?:(?!</w:r>).)*?新的说法", body, re.S)
    assert new_run is not None
    assert "<w:b/>" in new_run.group(0)


def test_a_spreadsheet_is_sent_to_openpyxl_instead(tmp_path: Path):
    """Excel 的文字不在这些部件里，改它要用别的办法，不是改得不完整。"""
    book = tmp_path / "预算表.xlsx"
    with zipfile.ZipFile(book, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("xl/workbook.xml", f"{DECLARATION}<workbook/>")
    said = refused("edit", book, "-o", tmp_path / "改后.xlsx", "--replace", "a=b")
    assert "openpyxl" in said


def test_unpack_refuses_a_part_that_escapes_the_directory(tmp_path: Path):
    """别人传来的文件不可信：`../` 的条目会写到解包目录外面去。"""
    bad = tmp_path / "越界.docx"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("../逃出去.xml", "<x/>")
    refused("unpack", bad, "-d", tmp_path / "拆开")
    assert not (tmp_path / "逃出去.xml").exists()


@pytest.mark.skipif(shutil.which("ln") is None, reason="需要能造符号链接")
def test_unpack_refuses_a_symlink_entry(tmp_path: Path):
    """zip 里的符号链接条目解包后会让后续写入落到链接指向的地方。"""
    bad = tmp_path / "链接.docx"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        link = zipfile.ZipInfo("word/document.xml")
        link.create_system = 3
        link.external_attr = (0xA1FF << 16) | 0o120000
        z.writestr(link, "/etc/passwd")
    refused("unpack", bad, "-d", tmp_path / "拆开")
