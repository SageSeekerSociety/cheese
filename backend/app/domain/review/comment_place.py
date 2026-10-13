"""Where in a file a comment points, as the file itself counts.

Lines are how a text file is pointed at. A Word document has pages, a deck has
slides and a workbook has cells, and none of them has a line a reader could
name. A comment stores its place in the file's own terms, and this module is
the one reading of it: what a place may look like for a given file, and how it
is said to 芝士.

- `L12` / `L12-L14` — lines of a text file;
- `p3` — a page of a Word document;
- `s2` — a slide of a deck;
- `汇总!C5` — a cell, with its sheet.
"""

import re
from pathlib import Path

from app.core.errors import UnprocessableEntityError
from app.core.sentences import say

_PAGE = re.compile(r"p([1-9]\d{0,4})")
_SLIDE = re.compile(r"s([1-9]\d{0,4})")
_CELL = re.compile(r"(.{1,100})!([A-Z]{1,3})([1-9]\d{0,6})")

_DOCUMENT = {".docx": "page", ".pptx": "slide", ".xlsx": "cell", ".xlsm": "cell"}


def kind_of(path: str) -> str:
    """`line`, or the unit a document of this type is pointed at in."""
    return _DOCUMENT.get(Path(path).suffix.lower(), "line")


def settle(
    path: str, line_start: int, line_end: int, given: str
) -> tuple[str, int, int]:
    """The place to store, and the numbers that go with it.

    A text file's place is its lines. A document's comes from the client, and
    its number (the page, the slide, the cell's row) is read off the place, so
    the two cannot disagree."""
    kind = kind_of(path)
    if kind == "line":
        if line_start < 1 or line_end < line_start:
            raise UnprocessableEntityError(say("reviewCommentBadLines"))
        span = f"-L{line_end}" if line_end != line_start else ""
        return f"L{line_start}{span}", line_start, line_end
    pattern = {"page": _PAGE, "slide": _SLIDE, "cell": _CELL}[kind]
    match = pattern.fullmatch(given or "")
    if match is None:
        raise UnprocessableEntityError(say("reviewCommentBadPlace"))
    number = int(match.groups()[-1])
    return given, number, number


def spoken(path: str, place: str) -> str:
    """The place as 芝士 is told it."""
    kind = kind_of(path)
    if kind == "line":
        return f"{path}:{place.replace('L', '')}"
    if kind == "cell":
        return f"{path} 的单元格 {place}"
    number = place[1:]
    return (
        f"{path} 第 {number} 页" if kind == "page" else f"{path} 第 {number} 张幻灯片"
    )
