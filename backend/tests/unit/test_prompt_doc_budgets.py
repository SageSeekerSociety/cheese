"""文档块的预算帽与丢弃顺序（#1535 第 1 条）。

无上限的文档注入是实测到 79K 字符失控的那个洞。这里钉的是丢弃顺序本身：临时/待办
先丢、进展次之、目标/约束/决策/约定最后才动——顺序反了等于先删最重要的。以及两
条安静失败的防线：没超帽时一个字节不动（byte-identical），被压缩时必须留痕（原文
多长、全文去哪读）——静默截断读起来就是「全文就这么长」。
"""

from app.domain.agent.harness.prompt import (
    build_system_prompt,
    fit_doc_to_budget,
)

HINT = "用 `cheese_doc_get` 读全文"


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


def test_the_two_machine_written_sections_survive_while_temporary_drops():
    doc = (
        "## 大家都该知道的\n"
        + "众" * 300
        + "\n\n## 项目记忆（由记忆整理迁入）\n"
        + "忆" * 300
        + "\n\n## 临时草稿\n"
        + "草" * 300
    )

    kept = _fit(doc, 750)

    assert "大家都该知道的" in kept
    assert "项目记忆（由记忆整理迁入）" in kept
    assert "## 临时草稿" not in kept


def test_an_unsectioned_doc_is_truncated_from_the_tail_with_a_note():
    doc = "句。" * 2000

    kept = _fit(doc, 500)

    assert kept.startswith("句。句。")
    assert "⚠️" in kept


def test_the_topic_doc_note_names_the_full_read_command():
    prompt = build_system_prompt("底稿", "", "## 临时\n" + "长" * 9000, [])

    assert "cheese_doc_get" in prompt


def test_compressed_docs_leave_the_memory_block_untouched():
    prompt = build_system_prompt(
        "底稿",
        "",
        "## 临时\n" + "长" * 9000,
        ["记忆甲", "记忆乙"],
        overview_doc="## 临时\n" + "短" * 9000,
    )

    assert "记忆甲" in prompt and "记忆乙" in prompt
    assert "### 核心记忆（每轮都在场" in prompt
