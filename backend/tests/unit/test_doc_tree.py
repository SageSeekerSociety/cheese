"""Unit tests for the pure markdown ⇄ doc-node bridge and node reconciliation.

No DB: ``doc_tree`` is deterministic, structure-only. The reconcile planner takes
``(block_id, content)`` pairs and returns which ids survive an edit — the mechanism
that keeps comment/highlight anchors alive across document edits.
"""

from app.domain.block.doc_tree import (
    CODE,
    HEADING,
    LIST,
    PARAGRAPH,
    QUOTE,
    DocNode,
    markdown_to_nodes,
    nodes_to_markdown,
    reconcile_nodes,
)


def _contents(nodes: list[DocNode]) -> list[str]:
    return [n.content for n in nodes]


def test_splits_top_level_blocks_by_blank_lines():
    md = "para one\n\npara two"
    nodes = markdown_to_nodes(md)
    assert _contents(nodes) == ["para one", "para two"]
    assert all(n.node_type == PARAGRAPH for n in nodes)


def test_heading_is_its_own_node_without_blank_line():
    md = "# Title\nbody text"
    nodes = markdown_to_nodes(md)
    assert [(n.node_type, n.content) for n in nodes] == [
        (HEADING, "# Title"),
        (PARAGRAPH, "body text"),
    ]


def test_fenced_code_block_stays_whole():
    md = "before\n\n```py\na = 1\n\nb = 2\n```\n\nafter"
    nodes = markdown_to_nodes(md)
    assert _contents(nodes) == ["before", "```py\na = 1\n\nb = 2\n```", "after"]
    assert nodes[1].node_type == CODE


def test_list_and_quote_classification():
    assert markdown_to_nodes("- a\n- b")[0].node_type == LIST
    assert markdown_to_nodes("1. a\n2. b")[0].node_type == LIST
    assert markdown_to_nodes("> quoted")[0].node_type == QUOTE


def test_roundtrip_markdown():
    md = "# Title\n\npara\n\n- x\n- y"
    assert nodes_to_markdown(markdown_to_nodes(md)) == md


def test_reconcile_identical_is_noop():
    existing = [(1, "a"), (2, "b")]
    plan = reconcile_nodes(existing, [DocNode(PARAGRAPH, "a"), DocNode(PARAGRAPH, "b")])
    assert plan.delete_ids == []
    assert plan.insert == []
    assert [(r.block_id, r.idx) for r in plan.reuse] == [(1, 0), (2, 1)]


def test_reconcile_edit_middle_keeps_stable_ids():
    # Editing "b" -> "B" must NOT disturb the ids of "a" and "c" (anchor survival).
    existing = [(1, "a"), (2, "b"), (3, "c")]
    new = [DocNode(PARAGRAPH, "a"), DocNode(PARAGRAPH, "B"), DocNode(PARAGRAPH, "c")]
    plan = reconcile_nodes(existing, new)
    kept = {r.block_id for r in plan.reuse}
    assert kept == {1, 3}
    assert plan.delete_ids == [2]
    assert [idx for idx, _n in plan.insert] == [1]


def test_reconcile_insert_between_preserves_neighbors():
    existing = [(1, "a"), (2, "c")]
    new = [DocNode(PARAGRAPH, "a"), DocNode(PARAGRAPH, "b"), DocNode(PARAGRAPH, "c")]
    plan = reconcile_nodes(existing, new)
    assert {r.block_id for r in plan.reuse} == {1, 2}
    assert plan.delete_ids == []
    assert [idx for idx, _n in plan.insert] == [1]


def test_reconcile_reorder_reassigns_idx():
    existing = [(1, "a"), (2, "b")]
    new = [DocNode(PARAGRAPH, "b"), DocNode(PARAGRAPH, "a")]
    plan = reconcile_nodes(existing, new)
    # Both survive; difflib keeps one equal run, the other is delete+insert, but no
    # content is lost and the surviving id maps to its new position.
    kept = {r.block_id for r in plan.reuse}
    assert kept  # at least one id reused
    all_idx = sorted([r.idx for r in plan.reuse] + [i for i, _ in plan.insert])
    assert all_idx == [0, 1]
