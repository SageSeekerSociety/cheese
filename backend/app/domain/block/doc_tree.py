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

    for line in lines:
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
