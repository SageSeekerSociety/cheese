"""Living doc ⇄ block tree (spec §5 / design B1, Phase 1).

The living document is stored as an ordered tree of `kind=doc` blocks
(`struct_parent` + `struct_order`) instead of one markdown blob, so individual
nodes get stable ids that comments / cross-view highlights / live refs can anchor
to later (B1 Phase 2/3). This module is the pure, deterministic bridge between a
markdown string and a flat list of document nodes — NO natural-language semantics
are inferred, only structure (allowed by CLAUDE.md).

Phase 1 granularity: a node is a top-level markdown block separated by blank
lines (a paragraph, a heading, a whole list, a fenced code block, a blockquote).
Per-list-item / per-todo splitting is a later phase (A2).

A block that encloses others is one node however many blank lines it holds: a
`:::` container (timeline, stat cards, columns), a `<details>` fold, a `$$`
formula. The editor shows each of them as one block, and comments anchor by
counting the editor's blocks against these nodes.
"""

import re
from dataclasses import dataclass

# Document node types (stored in Block.node_type). Coarse, structure-only.
HEADING = "heading"
CODE = "code"
QUOTE = "quote"
LIST = "list"
PARAGRAPH = "paragraph"

_HEADING_RE = re.compile(r"#{1,6}\s")
_LIST_RE = re.compile(r"\s*([-*+]\s|\d+[.)]\s)")


@dataclass
class DocNode:
    """One top-level document block: its structural type + raw markdown."""

    node_type: str
    content: str


_CONTAINER_OPEN_RE = re.compile(r"(:{3,})[a-z]+")
_CONTAINER_CLOSE_RE = re.compile(r"(:{3,})\s*$")
_DETAILS_RE = re.compile(r"<(/?)details\b[^>]*>", re.IGNORECASE)


def _is_fence(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("```") or s.startswith("~~~")


def _classify(block: str) -> str:
    first = block.lstrip().splitlines()[0] if block.strip() else ""
    if first.lstrip().startswith(("```", "~~~")):
        return CODE
    if _HEADING_RE.match(first):
        return HEADING
    if first.lstrip().startswith(">"):
        return QUOTE
    if _LIST_RE.match(first):
        return LIST
    return PARAGRAPH


def markdown_to_nodes(md: str) -> list[DocNode]:
    """Split markdown into top-level blocks (blank-line separated, fence-aware)."""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[str] = []
    cur: list[str] = []
    in_fence = False

    def flush() -> None:
        nonlocal cur
        text = "\n".join(cur).strip("\n")
        if text.strip():
            blocks.append(text)
        cur = []

    # The block that encloses others, while one is open: what closes it, and how
    # many of the same kind are open inside it.
    enclosure: list[int] | None = None  # container colon counts, innermost last
    details = 0
    in_math = False

    for line in lines:
        stripped = line.strip()
        if enclosure is not None:
            cur.append(line)
            opened = _CONTAINER_OPEN_RE.match(stripped)
            closed = _CONTAINER_CLOSE_RE.match(stripped)
            if opened:
                enclosure.append(len(opened.group(1)))
            elif closed and enclosure and enclosure[-1] == len(closed.group(1)):
                enclosure.pop()
            if not enclosure:
                enclosure = None
                flush()
            continue
        if details:
            cur.append(line)
            for tag in _DETAILS_RE.finditer(line):
                details += -1 if tag.group(1) else 1
            if details <= 0:
                details = 0
                flush()
            continue
        if in_math:
            cur.append(line)
            if stripped.endswith("$$"):
                in_math = False
                flush()
            continue
        if not in_fence and (opener := _CONTAINER_OPEN_RE.match(stripped)):
            flush()
            cur = [line]
            enclosure = [len(opener.group(1))]
            continue
        if not in_fence and stripped.lower().startswith("<details"):
            flush()
            cur = [line]
            for tag in _DETAILS_RE.finditer(line):
                details += -1 if tag.group(1) else 1
            if details <= 0:
                details = 0
                flush()
            continue
        one_line_math = len(stripped) > 2 and stripped.endswith("$$")
        if not in_fence and stripped.startswith("$$") and not one_line_math:
            flush()
            cur = [line]
            in_math = True
            continue
        if in_fence:
            cur.append(line)
            if _is_fence(line):
                in_fence = False
            continue
        if _is_fence(line):
            # A fence starts its own block.
            flush()
            cur = [line]
            in_fence = True
            continue
        if line.strip() == "":
            flush()
            continue
        if _HEADING_RE.match(line.lstrip()):
            # A heading is always its own node, even without a surrounding blank
            # line, so it anchors separately from the body beneath it.
            flush()
            blocks.append(line.strip())
            continue
        cur.append(line)
    flush()

    return [DocNode(_classify(b), b) for b in blocks]


def nodes_to_markdown(nodes: list[DocNode]) -> str:
    """Reassemble nodes into markdown (one blank line between blocks)."""
    return "\n\n".join(n.content for n in nodes)
