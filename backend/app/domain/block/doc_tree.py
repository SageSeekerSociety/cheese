"""Living doc ⇄ block tree (万物皆块 §2.1; product design §5).

A document's body is stored as an ordered tree of blocks — a ``DOC_ROOT`` block
holding the whole markdown, plus one ``DOC_NODE`` child per top-level markdown
block (``struct_parent_id`` + ``struct_order``) — instead of a single opaque blob.
Each node gets a stable id that comments / cross-view highlights / live refs can
anchor to, and those anchors survive edits (see ``reconcile_nodes``).

This module is the pure, deterministic bridge between a markdown string and a flat
list of document nodes: it infers **structure only**, never natural-language
semantics (CLAUDE.md — no regex/heuristics over natural language for meaning).

Granularity: a node is a top-level markdown block separated by blank lines (a
paragraph, a heading, a whole list, a fenced code block, a blockquote). Splitting
lists into per-item nodes is a later phase.
"""

import difflib
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


@dataclass
class ReuseOp:
    """Keep existing node ``block_id``; set it to node ``idx`` (order/type may change)."""

    block_id: int
    idx: int
    node: DocNode


@dataclass
class ReconcilePlan:
    """The block-level edit plan for saving a document body.

    ``reuse`` — existing nodes whose content is unchanged, re-pointed to their new
    position (so their ids, and any anchors on them, survive the edit).
    ``delete_ids`` — existing node blocks to drop.
    ``insert`` — new nodes to create, as ``(idx, DocNode)`` in document order.
    """

    reuse: list[ReuseOp]
    delete_ids: list[int]
    insert: list[tuple[int, DocNode]]


def reconcile_nodes(existing: list[tuple[int, str]], new_nodes: list[DocNode]) -> ReconcilePlan:
    """Diff a document's current node blocks against freshly parsed nodes.

    ``existing`` is ``(block_id, content)`` in document order; ``new_nodes`` is the
    parsed target. Unchanged runs (``difflib`` equal opcodes) reuse their block ids
    so comment/highlight/ref anchors survive; everything else is delete + insert.
    Re-saving identical markdown yields an empty (no-op) plan.
    """
    matcher = difflib.SequenceMatcher(
        a=[c for _id, c in existing],
        b=[n.content for n in new_nodes],
        autojunk=False,
    )
    reuse_by_idx: dict[int, int] = {}  # new idx -> reused block_id
    for tag, i1, i2, j1, _j2 in matcher.get_opcodes():
        if tag == "equal":
            for off in range(i2 - i1):
                reuse_by_idx[j1 + off] = existing[i1 + off][0]

    kept_ids = set(reuse_by_idx.values())
    delete_ids = [bid for bid, _c in existing if bid not in kept_ids]

    reuse: list[ReuseOp] = []
    insert: list[tuple[int, DocNode]] = []
    for idx, node in enumerate(new_nodes):
        bid = reuse_by_idx.get(idx)
        if bid is not None:
            reuse.append(ReuseOp(block_id=bid, idx=idx, node=node))
        else:
            insert.append((idx, node))
    return ReconcilePlan(reuse=reuse, delete_ids=delete_ids, insert=insert)
