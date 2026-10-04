"""文档块的预算帽与丢弃顺序（#1535 第 1 条）。

无上限的文档注入是实测到 79K 字符失控的那个洞。这里钉的是丢弃顺序本身：临时/待办
先丢、进展次之、目标/约束/决策/约定最后才动——顺序反了等于先删最重要的。以及两
条安静失败的防线：没超帽时一个字节不动（byte-identical），被压缩时必须留痕（原文
多长、全文去哪读）——静默截断读起来就是「全文就这么长」。
"""

from app.domain.agent.harness.prompt import (
    build_session_opening,
    fit_doc_to_budget,
)
from app.domain.memory.files_store import IndexSection, MemoryIndex

HINT = "用 `cheese_doc_get` 读全文"


def _index(*lines: str) -> MemoryIndex:
    """A turn's L1 index, built by hand — the prompt only ever sees this shape."""
    return MemoryIndex(
        sections=[IndexSection(label="项目", prefix="team", text="\n".join(lines))],
        warnings=[],
    )


def _fit(text: str, budget: int) -> str:
    return fit_doc_to_budget(text, budget, full_read_hint=HINT)


def test_a_doc_within_the_budget_passes_through_byte_for_byte():
    doc = "## 目标\n\n做一件事。\n"

    assert _fit(doc, 6000) == doc
    assert "⚠️" not in _fit(doc, 6000)


def test_temporary_and_progress_drop_while_goals_and_decisions_survive():
    doc = "\n\n".join(
        [
            "## 目标\n" + "目" * 300,
            "## 临时\n" + "临" * 300,
            "## 进展\n" + "进" * 300,
            "## 决策\n" + "决" * 300,
        ]
    )

    kept = _fit(doc, 700)

    assert "## 目标" in kept
    assert "## 决策" in kept
    assert "## 临时" not in kept
    # 700 的预算在临时丢完后仍然不够——进展第二个被丢。
    assert "## 进展" not in kept
    # 被压缩必须留痕：原文多长、全文去哪读。
    assert str(len(doc)) in kept
    assert HINT in kept


def test_an_unsectioned_doc_is_truncated_from_the_tail_with_a_note():
    doc = "句。" * 2000

    kept = _fit(doc, 500)

    assert kept.startswith("句。句。")
    assert "⚠️" in kept


def test_the_topic_doc_note_names_the_full_read_command():
    opening = build_session_opening(doc="## 临时\n" + "长" * 9000).text

    assert "cheese_doc_get" in opening


def test_compressed_docs_leave_the_memory_block_untouched():
    prompt = build_session_opening(
        doc="## 临时\n" + "长" * 9000,
        memory=_index("- [甲](a.md) — 记忆甲", "- [乙](b.md) — 记忆乙"),
        overview_doc="## 临时\n" + "短" * 9000,
        # 记忆那两段要有得看，得先说清这一轮跑的骨架会把文件对账回去
        # （`keeps_memory`，见 `test_the_memory_section_follows_the_harness.py`）。
        keeps_memory=True,
    ).text

    assert "记忆甲" in prompt and "记忆乙" in prompt
    assert "## 你的记忆（索引" in prompt
