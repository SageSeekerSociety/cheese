"""CSV cells a spreadsheet reads as data, never as a formula.

``csv.writer`` gets RFC 4180 quoting right, and quoting is not a defence here:
Excel, WPS and LibreOffice strip the quotes and then evaluate what is left. So
a participant who submits ``=HYPERLINK("http://evil.example","click")`` as
their phone number, or ``-1+1`` as their email, ends up with a live formula in
the teacher's export — it runs when the file is opened.

The fix is to stop the cell from *looking* like a formula at all: text that
begins with one of `_FORMULA_LEADS` gets a leading apostrophe, which is the
character both Excel and WPS read as "this cell is text". That is OWASP's
recommendation, and it is the one option that survives a spreadsheet stripping
quotes or whitespace first. The alternatives were rejected on purpose: quoting
alone is exactly what gets stripped before parsing, and rewriting or dropping
the character would silently change a real phone number, real name or address.

Two things are deliberately left alone, because neither is an attack:

* A value that is already a number — an ``int``/``float``/``bool`` the caller
  passed, not text anyone typed. Numbers are values; prefixing them would turn
  a count into text and break the sheet's arithmetic.
* A *string* that is merely a plain decimal (``-5``, ``-5.25``, ``-1e3``). A
  negative number is a legitimate value, and a plain decimal cannot execute
  anything — there is no function call in it. ``-1+1`` still is not a plain
  decimal, so it is still neutralised.

This module is the single implementation behind every CSV export in the
backend; nothing should hand-roll its own escaping next to it.
"""

from __future__ import annotations

import csv
import io
import re

#: Characters that open a formula in Excel/WPS/LibreOffice. Tab, CR and LF are
#: on the list because a reader strips leading whitespace before parsing, which
#: would smuggle a `=` past a check that only looked for `=`.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r", "\n")

#: A plain decimal, and nothing else - the one leading-`-` shape a spreadsheet
#: evaluates to the same number it displays. Anything richer (`-1+1`,
#: `-cmd|...`) fails to match and is neutralised.
_PLAIN_DECIMAL = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")


def csv_cell(value: object) -> str:
    """Render one cell's text, inert for a spreadsheet.

    ``None`` becomes an empty cell; numbers (and bools) are rendered as they
    were; text that would read as a formula is prefixed with an apostrophe.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        # A typed value the exporter computed: int, float, bool. Never text a
        # user typed, so there is no formula to neutralise.
        return str(value)
    if value.startswith(_FORMULA_LEADS) and not _PLAIN_DECIMAL.match(value):
        return "'" + value
    return value


def csv_row(*values: object) -> str:
    """One CSV row: RFC 4180 quoting plus the formula guard, no terminator."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="")
    writer.writerow([csv_cell(v) for v in values])
    return buf.getvalue()
