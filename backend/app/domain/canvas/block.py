"""How a canvas is written in its document's Markdown.

    :::canvas 6f1c2e0a-3b7d-4e58-9a51-0c2d4b8e7f13 项目地图
    各件事和它们之间的关系。
    :::

The opening line names the canvas by its id and gives its title; the line
under it says in a sentence what it shows. The id is written out, not hidden:
a document rewritten as Markdown keeps it, and a reader who opens the text
anywhere else still sees a titled, explained block. The board itself is kept
in the canvas tables. ``new`` in place of the id is a block whose canvas was
never made.

The editor's half of the same syntax is ``frontend/src/lib/docSchema/canvas.ts``;
the two read and write the same lines.
"""

import re
import uuid
from dataclasses import dataclass

#: What the id is written as before a canvas exists.
UNMADE = "new"

_UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_OPENER = re.compile(
    rf"^ {{0,3}}:::canvas[ \t]+(?P<ref>{_UUID}|{UNMADE})"
    r"(?:[ \t]+(?P<title>[^\n]*?))?[ \t]*$",
    re.MULTILINE,
)
_CLOSER = re.compile(r"^ {0,3}:::[ \t]*$", re.MULTILINE)
_FENCE = re.compile(r"^ {0,3}(```|~~~)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class CanvasBlock:
    #: None for a block whose canvas was never made (``new``).
    canvas_id: uuid.UUID | None
    title: str
    about: str
    #: Where the opening line starts and the closing line ends in the text.
    start: int
    end: int


def _fenced(text: str) -> list[tuple[int, int]]:
    """The spans of fenced code: a canvas line written inside one is an
    example, not a block."""
    spans: list[tuple[int, int]] = []
    opened: tuple[int, str] | None = None
    for match in _FENCE.finditer(text):
        if opened is None:
            opened = (match.start(), match.group(1))
        elif match.group(1) == opened[1]:
            spans.append((opened[0], match.end()))
            opened = None
    if opened is not None:
        spans.append((opened[0], len(text)))
    return spans


def blocks(text: str) -> list[CanvasBlock]:
    """Every canvas block of a document's Markdown, in order."""
    found: list[CanvasBlock] = []
    fenced = _fenced(text)
    for match in _OPENER.finditer(text):
        if any(start <= match.start() < end for start, end in fenced):
            continue
        closer = _CLOSER.search(text, match.end())
        if closer is None:
            continue
        body = text[match.end() : closer.start()].strip()
        ref = match.group("ref")
        found.append(
            CanvasBlock(
                canvas_id=None if ref == UNMADE else uuid.UUID(ref),
                title=(match.group("title") or "").strip(),
                about=" ".join(body.split()),
                start=match.start(),
                end=closer.end(),
            )
        )
    return found


def canvas_ids(text: str) -> list[uuid.UUID]:
    """The canvases the text names, each once, in order."""
    seen: dict[uuid.UUID, None] = {}
    for block in blocks(text):
        if block.canvas_id is not None:
            seen.setdefault(block.canvas_id, None)
    return list(seen)


def renamed(text: str, refs: list[uuid.UUID | None]) -> str:
    """The text with its canvas blocks, in order, naming ``refs`` instead: one
    entry per block ``blocks`` finds, None for ``new``."""
    found = blocks(text)
    if len(found) != len(refs):
        raise ValueError("one ref per block")  # i18n-exempt: a caller bug
    out: list[str] = []
    last = 0
    for block, ref in zip(found, refs, strict=True):
        opener = _OPENER.match(text, block.start)
        assert opener is not None
        out.append(text[last : opener.start("ref")])
        out.append(str(ref) if ref is not None else UNMADE)
        last = opener.end("ref")
    out.append(text[last:])
    return "".join(out)
