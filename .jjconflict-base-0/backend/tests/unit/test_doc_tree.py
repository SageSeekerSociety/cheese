"""B1 Phase 1: markdown ⇄ doc-node bridge (pure, no DB)."""

from app.domain.block.doc_tree import (
    CODE,
    HEADING,
    LIST,
    PARAGRAPH,
    QUOTE,
    DocNode,
    markdown_to_nodes,
    nodes_to_markdown,
)

SAMPLE = """# 目标

基于脱敏数据搭建推荐原型。

## 约束

- 数据仅用于本项目
- 评测指标 Recall@10

```python
def rec(u):
    return top_k(u)
```

> 中期汇报 2026-06-20。
"""


def _types(md: str) -> list[str]:
    return [n.node_type for n in markdown_to_nodes(md)]


def test_classifies_top_level_blocks():
    assert _types(SAMPLE) == [
        HEADING,
        PARAGRAPH,
        HEADING,
        LIST,
        CODE,
        QUOTE,
    ]


def test_heading_splits_even_without_blank_line():
    # AI/markdown often writes "## 标题\n正文" with no blank line; the heading
    # must still be its own node so it anchors separately from the body.
    nodes = markdown_to_nodes("## 目标\n基于数据搭原型。\n\n## 约束\n- a")
    assert [(n.node_type, n.content) for n in nodes] == [
        (HEADING, "## 目标"),
        (PARAGRAPH, "基于数据搭原型。"),
        (HEADING, "## 约束"),
        (LIST, "- a"),
    ]


def test_list_stays_one_node():
    nodes = markdown_to_nodes("- a\n- b\n- c")
    assert len(nodes) == 1
    assert nodes[0].node_type == LIST
    assert nodes[0].content == "- a\n- b\n- c"


def test_code_fence_with_blank_lines_is_one_node():
    md = "```\nline1\n\nline2\n```"
    nodes = markdown_to_nodes(md)
    assert len(nodes) == 1
    assert nodes[0].node_type == CODE
    # the blank line inside the fence is preserved, not used as a separator
    assert "line1\n\nline2" in nodes[0].content


def test_roundtrip_is_idempotent():
    # Parsing the reassembled markdown yields the same nodes (stable doc set).
    nodes1 = markdown_to_nodes(SAMPLE)
    md1 = nodes_to_markdown(nodes1)
    nodes2 = markdown_to_nodes(md1)
    assert [(n.node_type, n.content) for n in nodes1] == [
        (n.node_type, n.content) for n in nodes2
    ]
    # And reassembling again is byte-stable.
    assert nodes_to_markdown(nodes2) == md1


def test_collapses_extra_blank_lines():
    nodes = markdown_to_nodes("a\n\n\n\nb")
    assert [n.content for n in nodes] == ["a", "b"]


def test_empty_markdown_is_no_nodes():
    assert markdown_to_nodes("") == []
    assert markdown_to_nodes("\n\n  \n") == []
    assert nodes_to_markdown([]) == ""


def test_nodes_to_markdown_separates_with_blank_line():
    out = nodes_to_markdown([DocNode(PARAGRAPH, "a"), DocNode(PARAGRAPH, "b")])
    assert out == "a\n\nb"
