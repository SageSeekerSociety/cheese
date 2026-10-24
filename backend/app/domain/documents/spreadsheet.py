"""A spreadsheet's formulas, recomputed, and every cell that would not compute.

A room can write a `.xlsx` and can edit one in place, and neither produces the
numbers. `openpyxl` writes the formula and no result under it; editing a cell
leaves the results of every formula that depended on it as they were. The file
opens, the formula bar is right, and the number the reader sees is the one from
before — a delivery that is wrong with nothing anywhere reporting it.

Excel and LibreOffice compute on load, so the machine that can answer this is
the one already running LibreOffice for previews (``deploy/office-render``).
This module is the thin half: hand it the workbook, and read back both the
recomputed file and the cells it could not compute.

Those cells are the other half of the obligation. `#DIV/0!` in a delivered
budget is worse than a blank, because it looks like a formatting problem rather
than an answer nobody has. They come back named the way a person points at
them — ``Sheet1!B7`` — which is the same coordinate the preview panel uses.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from xml.etree import ElementTree as ET

import httpx

from app.core.sentences import listing, say
from app.domain.documents.convert import suffix_of

#: Only `.xlsx`. Recomputing something with no formulas in it is a request
#: that cannot be meant, and `.xlsm` is left out for the opposite reason: the
#: recalculated workbook comes back through the plain xlsx filter, so a macro
#: workbook would return under its own name with the macros gone and nothing
#: anywhere reporting it.
RECALCULABLE_SUFFIXES = (".xlsx",)

#: The seven values Excel stores in place of a number it could not produce.
#: They are ordinary cell text with `t="e"`, so nothing downstream treats them
#: as errors unless it knows this list.
FORMULA_ERRORS = (
    "#DIV/0!",
    "#N/A",
    "#NAME?",
    "#NULL!",
    "#NUM!",
    "#REF!",
    "#VALUE!",
)

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


class SpreadsheetRecalcUnavailable(RuntimeError):
    """The deployment has no recalculation service, or it did not answer."""


class SpreadsheetRecalcFailed(RuntimeError):
    """The service answered, and could not recompute this workbook."""


@dataclass(frozen=True)
class BadCell:
    """One cell that did not compute, addressed the way a person reads it."""

    sheet: str
    cell: str
    value: str

    @property
    def address(self) -> str:
        return f"{self.sheet}!{self.cell}"

    def as_dict(self) -> dict:
        return {
            "sheet": self.sheet,
            "cell": self.cell,
            "value": self.value,
            "address": self.address,
        }


def _sheet_names(book: zipfile.ZipFile) -> dict[str, str]:
    """Worksheet part name → the sheet name on its tab.

    A part is `xl/worksheets/sheet1.xml`, and the tab says 「预算」. Reporting
    the part would hand someone a coordinate they cannot find in Excel, so the
    workbook's own list is read to translate.
    """
    try:
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    except KeyError:
        return {}
    targets = {
        rel.get("Id"): rel.get("Target")
        for rel in rels.iter(f"{{{_PKG_REL}}}Relationship")
    }
    names: dict[str, str] = {}
    for sheet in workbook.iter(f"{{{_MAIN}}}sheet"):
        target = targets.get(sheet.get(f"{{{_REL}}}id"))
        if not target:
            continue
        part = target.lstrip("/")
        if not part.startswith("xl/"):
            part = "xl/" + part
        names[part] = sheet.get("name", "")
    return names


def cells_that_did_not_compute(raw: bytes) -> list[BadCell]:
    """Every error value in the workbook, in sheet then cell order."""
    found: list[BadCell] = []
    with zipfile.ZipFile(BytesIO(raw)) as book:
        names = _sheet_names(book)
        for part in book.namelist():
            if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", part):
                continue
            sheet = names.get(part, part.rsplit("/", 1)[-1])
            body = ET.fromstring(book.read(part))
            for cell in body.iter(f"{{{_MAIN}}}c"):
                if cell.get("t") != "e" or not cell.get("r"):
                    continue
                text = cell.findtext(f"{{{_MAIN}}}v", "").strip()
                if text in FORMULA_ERRORS:
                    found.append(
                        BadCell(sheet=sheet, cell=cell.attrib["r"], value=text)
                    )
    return found


async def recalculate(
    raw: bytes, path: str, endpoint: str | None, timeout: float = 120.0
) -> tuple[bytes, list[BadCell]]:
    """The workbook with every formula recomputed, and what would not compute."""
    suffix = suffix_of(path)
    if suffix not in RECALCULABLE_SUFFIXES:
        raise SpreadsheetRecalcFailed(
            say(
                "recalcFormatUnsupported",
                formats=listing(RECALCULABLE_SUFFIXES),
                path=path,
            )
        )
    if not endpoint:
        raise SpreadsheetRecalcUnavailable(say("recalcDisabled"))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint.rstrip("/") + "/recalc",
                params={"suffix": suffix},
                content=raw,
                headers={"Content-Type": "application/octet-stream"},
            )
    except Exception as exc:  # every transport failure reads alike
        raise SpreadsheetRecalcUnavailable(say("recalcUnreachable")) from exc

    if response.status_code != 200:
        detail = ""
        try:
            detail = str(response.json().get("error") or "")
        except Exception:  # a non-JSON body is just no detail
            detail = ""
        if response.status_code >= 500 or response.status_code in (404, 405):
            # 404 and 405 mean the running renderer predates this endpoint — a
            # deployment whose two images came from different tags. Reporting it
            # as a problem with the workbook would send the caller looking at a
            # file that is fine.
            raise SpreadsheetRecalcUnavailable(detail or say("recalcServiceError"))
        raise SpreadsheetRecalcFailed(
            detail or say("recalcFailedStatus", status=response.status_code)
        )

    book = response.content
    if not book.startswith(b"PK"):
        raise SpreadsheetRecalcFailed(say("recalcNotXlsx"))
    try:
        bad = cells_that_did_not_compute(book)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        raise SpreadsheetRecalcFailed(say("recalcUnreadable")) from exc
    return book, bad
