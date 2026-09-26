"""`sheets.py` 报的引用和核对的数，是不是真能抓住「数错了却不报错」。

这一项要守住的是一条已经发生过的交付事故：跨 Sheet 搬一列公式，引用跟着搬过去
没变，每一行读的都是别行的数。文件能打开、公式栏对、合计也可能对，`cheese recalc`
照样说「没有算不出来的格」——因为每个公式都算得出来，只是算的不是那一行。

所以这里只从命令行调它，看它报什么：引用表里有没有那个跨 Sheet 的坐标、指到空格
的引用有没有被点出来、核对关键结果时对不对得上、共享公式的其余格有没有被漏掉。
夹具按 xlsx 的真实部件手写——改写过的值、共享公式、错误值这些形状，用 openpyxl
生成不出来（它自己不算数），而脚本读的就是这些字节。
"""

from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.domain.agent.skills import _NATIVE_SKILL_SRC

SHEETS = _NATIVE_SKILL_SRC / "documents" / "scripts" / "sheets.py"

DECLARATION = "<?xml version='1.0' encoding='UTF-8' standalone='yes'?>\n"
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# 这几种 MIME 串本身就比一行长，拼 XML 时只能从中间断开——断点是拼接用的，
# 拼出来的字节和一行写完一模一样。
OFFICE_MIME = "application/vnd.openxmlformats-officedocument"

CONTENT_TYPES = (
    DECLARATION
    + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" '
    f'ContentType="{OFFICE_MIME}.spreadsheetml.sheet.main+xml"/>'
    + "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        f'ContentType="{OFFICE_MIME}.spreadsheetml.worksheet+xml"/>'
        for i in (1, 2)
    )
    + "</Types>"
)

ROOT_RELS = (
    DECLARATION + f'<Relationships xmlns="{REL}">'
    f'<Relationship Id="rId1" Type="{DOC_REL}/officeDocument" '
    'Target="xl/workbook.xml"/>'
    "</Relationships>"
)


def text(cell: str, body: str) -> str:
    """一个写着文字的格——用来当旁边的标签，脚本靠它和引用对不上号。"""
    return f'<c r="{cell}" t="inlineStr"><is><t>{body}</t></is></c>'


def number(cell: str, body: str) -> str:
    return f'<c r="{cell}"><v>{body}</v></c>'


def formula(cell: str, text_: str, cached: str | None) -> str:
    """一个公式格。`cached` 是重算之后存进来的结果，没重算过就没有这一段。"""
    stored = "" if cached is None else f"<v>{cached}</v>"
    return f'<c r="{cell}"><f>{text_}</f>{stored}</c>'


def shared(cell: str, text_: str, cached: str | None) -> str:
    """共享公式写在这一块的第一个格上。"""
    stored = "" if cached is None else f"<v>{cached}</v>"
    return f'<c r="{cell}"><f t="shared" ref="B2:B4" si="0">{text_}</f>{stored}</c>'


def follower(cell: str, cached: str | None) -> str:
    """共享公式的其余格：只有 id，公式文本在别的格上。"""
    stored = "" if cached is None else f"<v>{cached}</v>"
    return f'<c r="{cell}"><f t="shared" si="0"/>{stored}</c>'


def row(index: int, *cells: str) -> str:
    return f'<row r="{index}">{"".join(cells)}</row>'


def workbook_rels(count: int) -> str:
    return (
        DECLARATION
        + f'<Relationships xmlns="{REL}">'
        + "".join(
            f'<Relationship Id="rId{i}" Type="{DOC_REL}/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, count + 1)
        )
        + "</Relationships>"
    )


def workbook(names: list[str]) -> str:
    entries = "".join(
        f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>'
        for i, name in enumerate(names, start=1)
    )
    return (
        f'{DECLARATION}<workbook xmlns="{MAIN}" xmlns:r="{DOC_REL}">'
        f"<sheets>{entries}</sheets></workbook>"
    )


def worksheet(body: str) -> str:
    return (
        f'{DECLARATION}<worksheet xmlns="{MAIN}">'
        f"<sheetData>{body}</sheetData></worksheet>"
    )


@pytest.fixture
def book(tmp_path: Path):
    """一张明细表加上一张按位置搬过去、类别没对上的统计表。

    预览那一行读到的是消息流的数——这是那次事故的形状：公式一行一错，合计还偏偏是对的。
    """

    def build(sheets: list[tuple[str, str]], name: str = "汇总.xlsx") -> Path:
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("[Content_Types].xml", CONTENT_TYPES)
            z.writestr("_rels/.rels", ROOT_RELS)
            z.writestr("xl/workbook.xml", workbook([n for n, _ in sheets]))
            z.writestr("xl/_rels/workbook.xml.rels", workbook_rels(len(sheets)))
            for i, (_n, body) in enumerate(sheets, start=1):
                z.writestr(f"xl/worksheets/sheet{i}.xml", worksheet(body))
        return path

    return build


DETAIL = (
    row(1, text("A1", "单号"), text("B1", "类别"), text("C1", "金额"))
    + row(2, text("A2", "SO-1001"), text("B2", "消息流"), number("C2", "57400"))
    + row(3, text("A3", "SO-1002"), text("B3", "预览"), number("C3", "30800"))
    + row(4, text("A4", "SO-1003"), text("B4", "办公文件"), number("C4", "12600"))
)

#: 统计表按「预览、办公文件、消息流」排，公式却按位置抄了明细表的第 2、3、4 行。
STATS = (
    row(1, text("A1", "类别"), text("B1", "含税"))
    + row(2, text("A2", "预览"), formula("B2", "明细!C2*1.1", "63140"))
    + row(3, text("A3", "办公文件"), formula("B3", "明细!C3*1.1", "33880"))
    + row(4, text("A4", "消息流"), formula("B4", "明细!C4*1.1", "13860"))
    + row(5, text("A5", "合计"), formula("B5", "SUM(B2:B4)", "110880"))
)

#: 同一张统计表，每一行读的是明细里标着同一类别的那一行。
STATS_ALIGNED = (
    row(1, text("A1", "类别"), text("B1", "含税"))
    + row(2, text("A2", "预览"), formula("B2", "明细!C3*1.1", "33880"))
    + row(3, text("A3", "办公文件"), formula("B3", "明细!C4*1.1", "13860"))
    + row(4, text("A4", "消息流"), formula("B4", "明细!C2*1.1", "63140"))
    + row(5, text("A5", "合计"), formula("B5", "SUM(B2:B4)", "110880"))
)


def sheets(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SHEETS), *[str(a) for a in args]],
        capture_output=True,
        text=True,
    )


def ran(*args: str | Path) -> str:
    done = sheets(*args)
    assert done.returncode == 0, done.stderr or done.stdout
    return done.stdout


def rejected(*args: str | Path) -> str:
    """核对没通过退出 1；参数或文件有问题退出 2。两种都不是 0。"""
    done = sheets(*args)
    assert done.returncode != 0, f"应当报错，却通过了：{done.stdout}"
    return done.stdout + done.stderr


def test_refs_names_the_cell_each_formula_reads(book):
    """引用表要写出坐标，跨 Sheet 的那一格也要写成 明细!C2，不能只写 C2。"""
    out = ran("refs", book([("明细", DETAIL), ("统计", STATS_ALIGNED)]))
    assert "明细!C3" in out
    assert "统计!B2" in out


def test_refs_reads_against_the_labels(book):
    """预览那一行读到明细第 2 行，而明细第 2 行的类别是消息流——这就是错位本身。"""
    done = sheets("refs", book([("明细", DETAIL), ("统计", STATS)]), "--json")
    entries = {row["cell"]: row for row in json.loads(done.stdout)["formulas"]}
    assert entries["统计!B2"]["reads"] == ["明细!C2"]
    assert entries["统计!B2"]["reads_blank"] == []
    # 搬过去之后 B2 那一行对不上标签，但公式本身没有任何地方报错。
    assert entries["统计!B5"]["reads"] == ["统计!B2", "统计!B3", "统计!B4"]


def test_refs_points_out_a_reference_that_lands_on_an_empty_cell(book):
    """行号多错一位就读到没数据的那一行，这个形状要点出来。"""
    stats = row(1, text("A1", "类别"), text("B1", "含税")) + row(
        2, text("A2", "消息流"), formula("B2", "明细!C5*1.1", "0")
    )
    out = rejected("refs", book([("明细", DETAIL), ("统计", stats)]))
    assert "其中是空格" in out
    assert "明细!C5" in out


def test_refs_fails_when_a_row_reads_another_labels_row(book):
    """预览那一行读明细第 2 行，而「预览」在明细第 3 行。

    这是能从文件本身证明的错位，要让命令失败。
    """
    done = sheets("refs", book([("明细", DETAIL), ("统计", STATS)]))
    assert done.returncode == 1, done.stdout
    assert "标签对不上" in done.stdout
    assert "「预览」在那张表的第 3 行" in done.stdout


def test_refs_passes_when_every_row_reads_its_own_label(book):
    """顺序不同没关系，只要每一行读的是同一个类别的那一行。"""
    out = ran("refs", book([("明细", DETAIL), ("统计", STATS_ALIGNED)]))
    assert "0 个标签对不上" in out


def test_differently_labelled_sheets_are_not_accused(book):
    """两张表用的标签体系不同，就无从判断，不能报错位。"""
    stats = row(1, text("A1", "项"), text("B1", "值")) + row(
        2, text("A2", "第一季度"), formula("B2", "明细!C2*1.1", "63140")
    )
    ran("refs", book([("明细", DETAIL), ("统计", stats)]))


def test_a_function_name_is_not_read_as_a_reference(book):
    """LOG10 是字母加数字，写法和一个格一模一样。"""
    stats = row(1, text("A1", "对数"), formula("B2", "LOG10(明细!C2)", "4.75"))
    done = sheets("refs", book([("明细", DETAIL), ("统计", stats)]), "--json")
    entries = {row["cell"]: row for row in json.loads(done.stdout)["formulas"]}
    assert entries["统计!B2"]["reads"] == ["明细!C2"]


def test_a_shared_formula_is_translated_not_skipped(book):
    """共享公式的其余格没有公式文本，但漏掉它们就等于漏掉整块。"""
    stats = (
        row(1, text("A1", "类别"), text("B1", "含税"))
        + row(2, text("A2", "消息流"), shared("B2", "明细!C2*1.1", "63140"))
        + row(3, text("A3", "预览"), follower("B3", "33880"))
        + row(4, text("A4", "办公文件"), follower("B4", "13860"))
    )
    done = sheets("refs", book([("明细", DETAIL), ("统计", stats)]), "--json")
    entries = {row["cell"]: row for row in json.loads(done.stdout)["formulas"]}
    assert entries["统计!B3"]["reads"] == ["明细!C3"]
    assert entries["统计!B4"]["reads"] == ["明细!C4"]


def test_check_compares_against_a_number_computed_outside(book):
    """合计是对的、每行是错的——只有独立算出来的关键结果能抓住它。"""
    path = book([("明细", DETAIL), ("统计", STATS)])
    assert "核对通过" in ran("check", path, "--expect", "统计!B5=110880")
    out = rejected("check", path, "--expect", "统计!B2=33880")
    assert "统计!B2" in out
    assert "63140" in out


def test_check_reports_a_formula_with_no_stored_result(book):
    """没重算过的文件里公式格没有值，先要重算。"""
    stats = row(1, text("A1", "类别"), formula("B2", "明细!C3*1.1", None))
    out = rejected("check", book([("明细", DETAIL), ("统计", stats)]))
    assert "没有缓存值" in out
    assert "cheese recalc" in out


def test_check_reports_a_stored_error_value(book):
    stats = row(
        1,
        text("A1", "类别"),
        '<c r="B2" t="e"><f>明细!C2/0</f><v>#DIV/0!</v></c>',
    )
    out = rejected("check", book([("明细", DETAIL), ("统计", stats)]))
    assert "#DIV/0!" in out


def test_check_without_expectations_still_refuses_an_unrecalculated_file(book):
    """什么都不点名时，它只剩下「有没有算不出来的格」这件事可查。"""
    stats = row(1, formula("B2", "明细!C3*1.1", None))
    out = rejected("check", book([("明细", DETAIL), ("统计", stats)]))
    assert "没有缓存值" in out


def test_sheet_and_range_narrow_what_is_reported(book):
    path = book([("明细", DETAIL), ("统计", STATS_ALIGNED)])
    only_detail = ran("refs", path, "--sheet", "明细")
    assert "统计!B2" not in only_detail
    narrowed = ran("refs", path, "--range", "B2:B2")
    assert "统计!B5" not in narrowed
    assert "统计!B2" in narrowed


def test_a_missing_sheet_is_refused_by_name(book):
    path = book([("明细", DETAIL), ("统计", STATS)])
    assert "没有这个工作表：预算" in rejected("refs", path, "--sheet", "预算")


def test_check_refuses_an_expectation_without_a_sheet(book):
    path = book([("明细", DETAIL), ("统计", STATS)])
    assert "要写上工作表" in rejected("check", path, "--expect", "B5=110880")
