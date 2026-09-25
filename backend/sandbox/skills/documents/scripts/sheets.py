#!/usr/bin/env python3
"""What a spreadsheet's formulas point at, and whether the numbers came out right.

`cheese recalc` answers one question: did every formula compute? It is the only
check the spreadsheet flow has, and it passes workbooks whose numbers are wrong
in a way nothing reports. Move a block of formulas to another sheet and the
references come along unchanged — they still resolve, they still compute, and
each row now reads a different row's data than the label beside it names. The
file opens, the formula bar is right, the total is right, and every per-row
number is wrong.

So this reads the other two questions, and neither is answerable from the
formula text alone:

  refs   which cells each formula reads, printed under the cell that reads them,
         so the correspondence with the labels can be looked at rather than
         assumed
  check  the delivered numbers against ones computed outside the workbook,
         which is the only thing that catches a formula reading the wrong row

Reads the package itself, so it needs nothing installed:

    python3 sheets.py refs 汇总.xlsx --sheet 统计
    python3 sheets.py check 汇总.xlsx --expect '统计!B5=110880'

Run `check` on the file after `cheese recalc`: it reads the results stored in
the file, and before recalculating there are none to read.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
DOC_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

#: The values Excel stores in a cell it could not compute. They are ordinary
#: text in the file, so nothing downstream treats them as an error unless it
#: asks — the same list `cheese recalc` reports from the other side.
FORMULA_ERRORS = (
    "#DIV/0!",
    "#N/A",
    "#NAME?",
    "#NULL!",
    "#NUM!",
    "#REF!",
    "#VALUE!",
)

#: An A1-style reference, optionally qualified by a sheet. Sheet names may be
#: quoted, and ours are usually Chinese, so the unquoted branch is wide: a tab
#: name may hold almost anything, and assuming `\\w` would drop 明细!C2.
_REF = re.compile(
    r"(?:(?:'((?:[^']|'')+)'|([^'!()\[\]:,+\-*/^&=<>%\s]+))!)?"
    r"(\$?)([A-Za-z]{1,3})(\$?)(\d+)"
)

#: How many cells a range is expanded into before it is reported as a range. A
#: whole-column reference is not something anyone wants printed out.
_EXPAND_LIMIT = 200


class Failed(Exception):
    """Something the caller has to fix. Printed to stderr, exit code 2."""


@dataclass(frozen=True)
class Reference:
    """One cell a formula reads, resolved to a sheet and a coordinate."""

    sheet: str
    cell: str
    external: bool

    @property
    def address(self) -> str:
        return f"{self.sheet}!{self.cell}"


class Book:
    """The parts of a workbook this needs: sheets, their cells, their formulas."""

    def __init__(self, raw: bytes, path: str):
        self.path = path
        try:
            self._zip = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise Failed(f"打不开 {path}：不是一个 .xlsx") from exc
        self._shared = self._read_shared_strings()
        self.order, self.parts = self._read_sheets()
        self.cells: dict[str, dict[str, object]] = {}
        self.formulas: dict[str, dict[str, str]] = {}
        for name, part in zip(self.order, self.parts, strict=True):
            self.formulas[name], self.cells[name] = self._read_sheet(name, part)

    # -- the package -------------------------------------------------------

    def _read(self, name: str) -> ElementTree.Element | None:
        try:
            body = self._zip.read(name)
        except KeyError:
            return None
        try:
            return ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise Failed(f"{self.path} 里的 {name} 读不出来：{exc}") from exc

    def _read_shared_strings(self) -> list[str]:
        root = self._read("xl/sharedStrings.xml")
        if root is None:
            return []
        strings: list[str] = []
        for item in root.findall(f"{MAIN}si"):
            strings.append("".join(t.text or "" for t in item.iter(f"{MAIN}t")))
        return strings

    def _read_sheets(self) -> tuple[list[str], list[str]]:
        """Sheet names in tab order, with the part each one lives in.

        A workbook's tabs point at parts through relationship ids, and the ids
        do not follow the tab order — reading the parts in name order would
        report 「Sheet3」 for what the tab calls something else.
        """
        root = self._read("xl/workbook.xml")
        rels = self._read("xl/_rels/workbook.xml.rels")
        if root is None or rels is None:
            raise Failed(f"{self.path} 里没有工作簿清单，可能不是一个 .xlsx")
        targets = {
            node.get("Id"): node.get("Target")
            for node in rels.findall(f"{REL}Relationship")
        }
        names: list[str] = []
        parts: list[str] = []
        for sheet in root.iter(f"{MAIN}sheet"):
            target = targets.get(sheet.get(f"{DOC_REL}id"))
            if not target:
                continue
            part = "xl/" + target.lstrip("/").removeprefix("xl/")
            names.append(sheet.get("name") or part)
            parts.append(part)
        return names, parts

    def _read_sheet(self, name: str, part: str):
        """The formulas and the stored values of one sheet.

        A formula shared by a block of cells is written once, on the first cell
        of the block; the rest carry an id pointing back at it. Those cells are
        translated here rather than skipped — a checker that goes quiet on part
        of a sheet is worse than no checker, because the part it skipped looks
        checked.
        """
        root = self._read(part)
        formulas: dict[str, str] = {}
        cells: dict[str, object] = {}
        if root is None:
            return formulas, cells

        shared: dict[str, tuple[str, str]] = {}
        pending: list[tuple[str, str]] = []
        for cell in root.iter(f"{MAIN}c"):
            address = cell.get("r")
            if not address:
                continue
            holder = cell.find(f"{MAIN}f")
            if holder is not None:
                text = holder.text or ""
                if holder.get("t") == "shared":
                    ident = holder.get("si") or ""
                    if text:
                        formula = text if text.startswith("=") else f"={text}"
                        shared[ident] = (address, formula)
                        formulas[address] = formula
                    else:
                        pending.append((address, ident))
                elif text:
                    formulas[address] = text if text.startswith("=") else f"={text}"
            cells[address] = self._value(cell)

        for address, ident in pending:
            entry = shared.get(ident)
            if entry is None:
                continue
            anchor, master = entry
            formulas[address] = _shift(
                master,
                _row_of(address) - _row_of(anchor),
                _col_of(address) - _col_of(anchor),
            )
        return formulas, cells

    def _value(self, cell: ElementTree.Element) -> object:
        kind = cell.get("t")
        if kind == "inlineStr":
            node = cell.find(f"{MAIN}is")
            return (
                ""
                if node is None
                else "".join(t.text or "" for t in node.iter(f"{MAIN}t"))
            )
        value = cell.find(f"{MAIN}v")
        text = "" if value is None or value.text is None else value.text
        if kind in ("s", "str", "e"):
            if kind != "s":
                return text
            try:
                return self._shared[int(text)]
            except (ValueError, IndexError):
                return text
        if text == "":
            return None
        try:
            return float(text)
        except ValueError:
            return text

    # -- questions ---------------------------------------------------------

    def references(self, sheet: str, formula: str) -> list[Reference]:
        found: list[Reference] = []
        seen: set[str] = set()
        for match in _REF.finditer(formula):
            if _is_function(formula, match.end()):
                continue
            quoted, bare, _ca, col, _ra, row = match.groups()
            name = quoted.replace("''", "'") if quoted else (bare or sheet)
            tail = formula[match.end() :]
            for cell in _expand(col, row, tail):
                address = f"{name}!{cell}"
                if address in seen:
                    continue
                seen.add(address)
                found.append(Reference(sheet=name, cell=cell, external=name != sheet))
        return found

    def is_blank(self, ref: Reference) -> bool:
        if ref.external and ref.sheet not in self.cells:
            return False
        if ":" in ref.cell:
            return False
        value = self.cells.get(ref.sheet, {}).get(ref.cell)
        return value is None or (isinstance(value, str) and not value.strip())


def _is_function(formula: str, end: int) -> bool:
    """`LOG10` is letters and digits, and reads exactly like a cell."""
    return end < len(formula) and formula[end] == "("


def _expand(col: str, row: str, tail: str) -> list[str]:
    span = re.match(r":\s*(\$?)([A-Za-z]{1,3})(\$?)(\d+)", tail)
    if not span:
        return [f"{col.upper()}{row}"]
    end_col, end_row = span.group(2).upper(), int(span.group(4))
    first_col, first_row = _column_index(col.upper()), int(row)
    last_col, last_row = _column_index(end_col), end_row
    if last_col < first_col or last_row < first_row:
        first_col, last_col = last_col, first_col
        first_row, last_row = last_row, first_row
    if (last_col - first_col + 1) * (last_row - first_row + 1) > _EXPAND_LIMIT:
        return [f"{col.upper()}{row}:{end_col}{end_row}"]
    return [
        f"{_column_letter(c)}{r}"
        for r in range(first_row, last_row + 1)
        for c in range(first_col, last_col + 1)
    ]


def _shift(formula: str, down: int, right: int) -> str:
    """The same formula moved onto another cell.

    A shared formula is written down once, on the block's first cell; every
    other cell of the block reads it moved by the distance between them. Only
    the relative half of a reference moves, which is what the `$` marks.
    """
    if down == 0 and right == 0:
        return formula
    out: list[str] = []
    last = 0
    for match in _REF.finditer(formula):
        if _is_function(formula, match.end()):
            continue
        out.append(formula[last : match.start()])
        quoted, bare, col_abs, col, row_abs, row = match.groups()
        sheet = f"'{quoted}'!" if quoted else (f"{bare}!" if bare else "")
        moved_col = col.upper()
        if not col_abs:
            moved_col = _column_letter(_column_index(col.upper()) + right)
        moved_row = row if row_abs else str(int(row) + down)
        out.append(f"{sheet}{col_abs}{moved_col}{row_abs}{moved_row}")
        last = match.end()
    out.append(formula[last:])
    return "".join(out)


def _row_of(address: str) -> int:
    match = re.search(r"\d+", address)
    return int(match.group()) if match else 0


def _col_of(address: str) -> int:
    match = re.match(r"([A-Za-z]+)", address)
    return _column_index(match.group(1).upper()) if match else 0


def _column_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index


def _column_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _in_bounds(coordinate: str, bounding: str) -> bool:
    match = re.fullmatch(r"([A-Za-z]+)(\d+):([A-Za-z]+)(\d+)", bounding.strip())
    if not match:
        raise Failed(f"--range 要写成 区域 的形状，例如 B2:C9：{bounding}")
    a, b, c, d = match.groups()
    min_col, max_col = sorted((_column_index(a.upper()), _column_index(c.upper())))
    min_row, max_row = sorted((int(b), int(d)))
    cell = re.fullmatch(r"([A-Za-z]+)(\d+)", coordinate)
    if not cell:
        return False
    col, row = _column_index(cell.group(1).upper()), int(cell.group(2))
    return min_col <= col <= max_col and min_row <= row <= max_row


def cmd_refs(args) -> int:
    book = Book(_read_zip(args.workbook), args.workbook)
    sheets = [args.sheet] if args.sheet else book.order
    for name in sheets:
        if name not in book.formulas:
            raise Failed(f"没有这个工作表：{name}")

    rows: list[dict] = []
    for name in sheets:
        for address, formula in book.formulas[name].items():
            if args.range and not _in_bounds(address, args.range):
                continue
            refs = book.references(name, formula)
            rows.append(
                {
                    "cell": f"{name}!{address}",
                    "formula": formula,
                    "reads": [r.address for r in refs],
                    "reads_blank": [r.address for r in refs if book.is_blank(r)],
                }
            )

    if args.json:
        print(json.dumps({"formulas": rows}, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("没有找到公式格。")
        return 0

    for entry in rows:
        print(f"{entry['cell']}  {entry['formula']}")
        print(f"    读到：{'、'.join(entry['reads']) or '（没有引用任何格）'}")
        if entry["reads_blank"]:
            print(f"    其中是空格：{'、'.join(entry['reads_blank'])}")
    blanks = sum(1 for entry in rows if entry["reads_blank"])
    print(f"\n共 {len(rows)} 个公式格；{blanks} 个读到了空格。")
    if blanks:
        print("读到空格常常是行号错位的形状：公式指到了没数据的那一行。")
    print("读到哪些格不等于读对——把这张引用表对着旁边的标签看一遍。")
    return 0


def cmd_check(args) -> int:
    book = Book(_read_zip(args.workbook), args.workbook)

    problems: list[str] = []
    for name in book.order:
        for address, _formula in book.formulas[name].items():
            result = book.cells[name].get(address)
            if isinstance(result, str) and result.strip() in FORMULA_ERRORS:
                problems.append(f"{name}!{address} 算不出来：{result.strip()}")
            elif result is None:
                problems.append(
                    f"{name}!{address} 没有缓存值——这份文件没重算过，先跑 cheese recalc"
                )

    checked = 0
    for expectation in args.expect or []:
        address, want = _parse_expectation(expectation)
        sheet, coordinate = address.rsplit("!", 1)
        if sheet not in book.cells:
            raise Failed(f"没有这个工作表：{sheet}")
        got = book.cells[sheet].get(coordinate)
        checked += 1
        if not _close(got, want, args.tol):
            problems.append(f"{address} 是 {got!r}，应当核对到 {want!r}")

    if problems:
        print("核对没通过：")
        for line in problems:
            print(f"  {line}")
        return 1

    if args.expect:
        print(f"核对通过：{checked} 个关键结果与独立算出的值一致，没有算不出来的格。")
    else:
        print("没有算不出来的格，也没有没重算过的公式格。")
    print("注意：这只说明公式算得出来、你点名的数对得上；公式有没有指到对的行，")
    print("看 sheets.py refs 打出来的引用。")
    return 0


def _read_zip(path: str) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise Failed(f"读不了 {path}：{exc}") from exc


def _parse_expectation(text: str) -> tuple[str, str]:
    if "=" not in text:
        raise Failed(f"--expect 要写成 工作表!格=值 的形状：{text}")
    address, _, want = text.partition("=")
    address = address.strip()
    if "!" not in address:
        raise Failed(f"--expect 要写上工作表：{text}")
    return address, want.strip()


def _close(got, want: str, tol: float) -> bool:
    if got is None:
        return False
    if isinstance(got, str):
        return got.strip() == want
    try:
        return abs(float(got) - float(want)) <= tol
    except (TypeError, ValueError):
        return str(got) == want


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sheets.py", description="看表格里的公式指向哪里，以及数出来对不对"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("refs", help="逐个公式格打印它读到哪些格")
    p.add_argument("workbook")
    p.add_argument("--sheet", help="只看这个工作表")
    p.add_argument("--range", help="只看这个区域，例如 B2:C9")
    p.add_argument("--json", action="store_true", help="按 JSON 输出")
    p.set_defaults(func=cmd_refs)

    p = sub.add_parser("check", help="核对关键结果；算不出来或没重算过也在这里报")
    p.add_argument("workbook")
    p.add_argument(
        "--expect",
        action="append",
        metavar="工作表!格=值",
        help="独立算出来的关键结果，可重复；对不上就报错",
    )
    p.add_argument("--tol", type=float, default=1e-6, help="数值比较的容差")
    p.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Failed as exc:
        print(f"sheets.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
