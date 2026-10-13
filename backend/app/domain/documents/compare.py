"""Two versions of an Office file, compared the way a reader compares them.

`text.py` answers "what changed in the words" as a unified diff of paragraphs,
which is the right shape for a list of changes and the wrong one for reading a
report side by side. This module answers the three questions the 改动 tab and
the artifact page ask of the three kinds of Office file:

- a Word document: which paragraphs were added, removed or rewritten, and the
  words inside a rewritten one; and the paragraphs whose words stayed while
  their formatting changed, so "only the formatting changed" names where;
- a workbook: which cells changed, what they held before, and whether what
  changed is a formula or a value;
- a deck: which slides were added, removed or changed, in reading order.

Paragraph text comes from the same skill script `text.py` and the revision
panel read (`revisions.script`): what counts as a paragraph's live text has one
answer. A file that cannot be read gives None, and the caller falls back to
what it already says about it.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from app.domain.documents.revisions import package, script

WORD_SUFFIXES = (".docx",)
SHEET_SUFFIXES = (".xlsx", ".xlsm")
SLIDE_SUFFIXES = (".pptx",)

#: Past this many paragraphs, slides or changed cells the answer stops growing;
#: the reader is told how many there were.
MAX_ROWS = 3000
MAX_CELLS = 2000

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
_WML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

#: Attributes that change on every save without anything a reader could see.
_NOISE = re.compile(r"\s(?:w:)?rsid\w*=\"[^\"]*\"|\sw14:\w+=\"[^\"]*\"")

_TOKEN = re.compile(r"[A-Za-z0-9_]+|\s+|.", re.S)


def kind_of(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in WORD_SUFFIXES:
        return "word"
    if suffix in SHEET_SUFFIXES:
        return "sheet"
    if suffix in SLIDE_SUFFIXES:
        return "slide"
    return None


def compare(old: bytes | None, new: bytes, path: str) -> dict | None:
    """The comparison for this file, or None when it is not one of the three
    kinds or either side cannot be read. `old` None means the file is new."""
    kind = kind_of(path)
    try:
        if kind == "word":
            return _word(old, new, path)
        if kind == "sheet":
            return _sheet(old, new)
        if kind == "slide":
            return _slides(old, new)
    except Exception:  # noqa: BLE001 — an unreadable file has no comparison
        return None
    return None


# ---------------------------------------------------------------- words


def pieces(before: str, after: str) -> list[dict]:
    """The words of a rewritten paragraph: kept, taken out and put in.

    Latin words and numbers move as one token, every other character as its
    own, so a changed figure reads as one replacement and Chinese text compares
    character by character."""
    a, b = _TOKEN.findall(before), _TOKEN.findall(after)
    out: list[dict] = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append({"op": "equal", "text": "".join(a[i1:i2])})
            continue
        if i2 > i1:
            out.append({"op": "delete", "text": "".join(a[i1:i2])})
        if j2 > j1:
            out.append({"op": "insert", "text": "".join(b[j1:j2])})
    return out


def _paragraphs(raw: bytes, path: str) -> list[tuple[str, str]]:
    """(text, formatting) of every body paragraph, in reading order."""
    office = script()
    opened, temporary = package(raw, path, office)
    try:
        paragraph_tag, run_tag, _text, rpr_tag = office._tags("word")
        root = opened.elements("word/document.xml")
        rows: list[tuple[str, str]] = []
        for paragraph in root.iter(paragraph_tag):
            text = office.paragraph_text(paragraph, "word")
            look = [
                office.ET.tostring(p) for p in paragraph.findall(f"{{{_WML}}}pPr")
            ] + [
                office.ET.tostring(r)
                for run in paragraph.iter(run_tag)
                for r in run.findall(rpr_tag)
            ]
            rows.append(
                (text, _NOISE.sub("", b"".join(look).decode("utf-8", "replace")))
            )
        return rows
    finally:
        temporary.unlink(missing_ok=True)


def _word(old: bytes | None, new: bytes, path: str) -> dict:
    after = _paragraphs(new, path)
    before = _paragraphs(old, path) if old is not None else []
    rows: list[dict] = []
    formatting: list[dict] = []
    a = [text for text, _ in before]
    b = [text for text, _ in after]
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            for offset in range(i2 - i1):
                i, j = i1 + offset, j1 + offset
                rows.append(
                    {"op": "same", "before": i + 1, "after": j + 1, "text": b[j]}
                )
                if before[i][1] != after[j][1] and b[j].strip():
                    formatting.append({"after": j + 1, "text": b[j]})
            continue
        paired = min(i2 - i1, j2 - j1) if op == "replace" else 0
        for offset in range(paired):
            i, j = i1 + offset, j1 + offset
            rows.append(
                {
                    "op": "changed",
                    "before": i + 1,
                    "after": j + 1,
                    "pieces": pieces(a[i], b[j]),
                }
            )
        for i in range(i1 + paired, i2):
            rows.append({"op": "removed", "before": i + 1, "after": None, "text": a[i]})
        for j in range(j1 + paired, j2):
            rows.append({"op": "added", "before": None, "after": j + 1, "text": b[j]})
    changed = sum(1 for r in rows if r["op"] != "same")
    return {
        "kind": "word",
        "new_file": old is None,
        "identical": changed == 0 and not formatting,
        "changed": changed,
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "formatting": formatting[:MAX_ROWS],
    }


def paragraph_texts(raw: bytes, path: str) -> list[str]:
    return [text for text, _ in _paragraphs(raw, path)]


# ---------------------------------------------------------------- cells


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, str]:
    folder, name = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", name + ".rels")
    if rels_name not in z.namelist():
        return {}
    root = ET.fromstring(z.read(rels_name))
    out: dict[str, str] = {}
    for rel in root.iter(f"{{{_PKG_REL}}}Relationship"):
        key, target = rel.get("Id"), rel.get("Target")
        if key and target and rel.get("TargetMode") != "External":
            out[key] = posixpath.normpath(posixpath.join(folder, target))
    return out


def sheet_cells(raw: bytes) -> dict[str, dict[str, tuple[str, str | None]]]:
    """Every non-empty cell of every sheet: address → (shown value, formula)."""
    with zipfile.ZipFile(BytesIO(raw)) as z:
        names = set(z.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for item in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(
                f"{{{_MAIN}}}si"
            ):
                shared.append("".join(t.text or "" for t in item.iter(f"{{{_MAIN}}}t")))
        book = ET.fromstring(z.read("xl/workbook.xml"))
        targets = _rels(z, "xl/workbook.xml")
        out: dict[str, dict[str, tuple[str, str | None]]] = {}
        for sheet in book.iter(f"{{{_MAIN}}}sheet"):
            part = targets.get(sheet.get(f"{{{_REL}}}id") or "")
            if not part or part not in names:
                continue
            cells: dict[str, tuple[str, str | None]] = {}
            for cell in ET.fromstring(z.read(part)).iter(f"{{{_MAIN}}}c"):
                address = cell.get("r")
                if not address:
                    continue
                formula_node = cell.find(f"{{{_MAIN}}}f")
                formula = formula_node.text if formula_node is not None else None
                value_node = cell.find(f"{{{_MAIN}}}v")
                value = value_node.text if value_node is not None else None
                shape = cell.get("t")
                if shape == "s" and value is not None:
                    value = shared[int(value)] if int(value) < len(shared) else ""
                elif shape == "inlineStr":
                    value = "".join(t.text or "" for t in cell.iter(f"{{{_MAIN}}}t"))
                elif shape == "b" and value is not None:
                    value = "TRUE" if value == "1" else "FALSE"
                if value is None and formula is None:
                    continue
                cells[address] = (value or "", formula)
            out[sheet.get("name") or ""] = cells
        return out


def _cell_key(address: str) -> tuple[int, int]:
    match = re.match(r"([A-Z]+)(\d+)", address)
    if not match:
        return (0, 0)
    column = 0
    for letter in match.group(1):
        column = column * 26 + ord(letter) - 64
    return (int(match.group(2)), column)


def _sheet(old: bytes | None, new: bytes) -> dict:
    after = sheet_cells(new)
    before = sheet_cells(old) if old is not None else {}
    sheets: list[dict] = []
    total = 0
    for name in list(after) + [n for n in before if n not in after]:
        if name not in before:
            sheets.append({"name": name, "status": "added", "cells": []})
            continue
        if name not in after:
            sheets.append({"name": name, "status": "removed", "cells": []})
            continue
        was, now = before[name], after[name]
        cells = []
        for address in sorted(was.keys() | now.keys(), key=_cell_key):
            old_value, old_formula = was.get(address, ("", None))
            new_value, new_formula = now.get(address, ("", None))
            if (old_value, old_formula) == (new_value, new_formula):
                continue
            cells.append(
                {
                    "address": address,
                    "before": old_value,
                    "after": new_value,
                    "before_formula": old_formula,
                    "after_formula": new_formula,
                    "formula": old_formula != new_formula,
                }
            )
        total += len(cells)
        sheets.append(
            {
                "name": name,
                "status": "changed" if cells else "same",
                "cells": cells[:MAX_CELLS],
                "truncated": len(cells) > MAX_CELLS,
            }
        )
    return {
        "kind": "sheet",
        "new_file": old is None,
        "identical": total == 0 and all(s["status"] == "same" for s in sheets),
        "changed": total,
        "sheets": sheets,
    }


# ---------------------------------------------------------------- slides


def slide_texts(raw: bytes) -> list[tuple[str, str]]:
    """(title, all text) of every slide, in the order the deck shows them."""
    with zipfile.ZipFile(BytesIO(raw)) as z:
        deck = ET.fromstring(z.read("ppt/presentation.xml"))
        targets = _rels(z, "ppt/presentation.xml")
        out: list[tuple[str, str]] = []
        for slide in deck.iter(f"{{{_PML}}}sldId"):
            part = targets.get(slide.get(f"{{{_REL}}}id") or "")
            if not part:
                continue
            root = ET.fromstring(z.read(part))
            title = ""
            lines: list[str] = []
            for shape in root.iter(f"{{{_PML}}}sp"):
                kind: Any = shape.find(f".//{{{_PML}}}ph")
                text = "\n".join(
                    "".join(t.text or "" for t in p.iter(f"{{{_DML}}}t"))
                    for p in shape.iter(f"{{{_DML}}}p")
                ).strip()
                if not text:
                    continue
                if (
                    not title
                    and kind is not None
                    and kind.get("type") in ("title", "ctrTitle")
                ):
                    title = text
                lines.append(text)
            out.append(
                (title or (lines[0].splitlines()[0] if lines else ""), "\n".join(lines))
            )
        return out


def _slides(old: bytes | None, new: bytes) -> dict:
    after = slide_texts(new)
    before = slide_texts(old) if old is not None else []
    a = [text for _, text in before]
    b = [text for _, text in after]
    rows: list[dict] = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            rows += [
                {
                    "op": "same",
                    "before": i1 + k + 1,
                    "after": j1 + k + 1,
                    "title": after[j1 + k][0],
                }
                for k in range(i2 - i1)
            ]
            continue
        paired = min(i2 - i1, j2 - j1) if op == "replace" else 0
        rows += [
            {
                "op": "changed",
                "before": i1 + k + 1,
                "after": j1 + k + 1,
                "title": after[j1 + k][0],
                "pieces": pieces(a[i1 + k], b[j1 + k]),
            }
            for k in range(paired)
        ]
        rows += [
            {
                "op": "removed",
                "before": i + 1,
                "after": None,
                "title": before[i][0],
                "text": a[i],
            }
            for i in range(i1 + paired, i2)
        ]
        rows += [
            {
                "op": "added",
                "before": None,
                "after": j + 1,
                "title": after[j][0],
                "text": b[j],
            }
            for j in range(j1 + paired, j2)
        ]
    changed = sum(1 for r in rows if r["op"] != "same")
    return {
        "kind": "slide",
        "new_file": old is None,
        "identical": changed == 0,
        "changed": changed,
        "slides": rows[:MAX_ROWS],
    }
