"""实况文档写入时的检查（#1889 第 3 条）。

写入照样成功，警告要求当场改。这里钉的是「什么算不像状态」这件事本身：一份写
成状态的文档一个字都不该被念；写了流水账、追加更正、粘贴聊天或命令输出的，每
样都要被点名。警告只是提醒，所以越界的判定必须**准**——一份干净的文档收到警告，
下次它照写不误，检查就白做了。
"""

from app.domain.topic.doc_checks import (
    LIVING_DOC_CHAR_LIMIT,
    living_doc_warning_text,
    living_doc_warnings,
)

CLEAN = """# 目标

让列表接口支持翻页。

## 当前结论

分页方案已定：用 cursor，不用 offset。

## 进行中与下一步

- @张衡 本周过一遍接口设计。

## 待决

- 每页默认多少条？
"""


def test_a_document_written_as_state_is_left_alone():
    assert living_doc_warnings(CLEAN) == []


def test_the_character_budget_is_a_ceiling_not_a_target():
    assert living_doc_warnings("状" * LIVING_DOC_CHAR_LIMIT) == []

    warnings = living_doc_warnings("状" * (LIVING_DOC_CHAR_LIMIT + 1))

    assert any(str(LIVING_DOC_CHAR_LIMIT) in w for w in warnings)


def test_a_progress_log_section_is_named_by_its_title():
    for title in ("进展日志", "时间线"):
        warnings = living_doc_warnings(f"## {title}\n\n- 修好了。\n")

        assert any(title in w for w in warnings), title


def test_a_run_of_date_headed_lines_is_a_log_but_two_are_not():
    lines = "\n".join(f"2026-09-{day:02d} 做了点事" for day in range(1, 4))
    assert living_doc_warnings(lines) != []

    two = "\n".join(f"2026-09-{day:02d} 做了点事" for day in range(1, 3))
    assert living_doc_warnings(two) == []


def test_appending_a_correction_instead_of_rewriting_is_flagged():
    warnings = living_doc_warnings("## 当前结论\n\n- 用 offset。\n\n更正：改用 cursor。\n")

    assert any("更正" in w for w in warnings)


def test_pasted_terminal_output_is_flagged_but_a_command_or_two_is_not():
    pasted = "## 验证\n\n" + "\n".join(f"$ pytest -k case_{n}" for n in range(4))
    assert any("命令" in w for w in living_doc_warnings(pasted))

    two = "## 验证\n\n$ pytest -k a\n$ pytest -k b\n"
    assert living_doc_warnings(two) == []


def test_pasted_chat_transcript_is_flagged():
    pasted = "## 讨论\n\n" + "\n".join(f"[张衡]: 第 {n} 点呢？" for n in range(4))

    assert any("聊天" in w for w in living_doc_warnings(pasted))


def test_a_long_code_block_is_flagged_but_a_short_snippet_is_not():
    long_block = "## 方案\n\n```sql\n" + "\n".join("select 1;" for _ in range(60)) + "\n```\n"
    assert any("代码块" in w for w in living_doc_warnings(long_block))

    short = "## 方案\n\n```sql\nselect 1;\nselect 2;\n```\n"
    assert living_doc_warnings(short) == []


def test_every_warning_carries_the_fix_and_the_write_still_landed():
    doc = "## 进展日志\n\n" + "\n".join(f"$ run {n}" for n in range(4))

    text = living_doc_warning_text(doc)

    assert text is not None
    assert "文档已写入" in text
    # 每条警告都说清怎么改，不只是「有问题」。
    for warning in living_doc_warnings(doc):
        assert any(word in warning for word in ("改掉", "删掉", "只留", "留在", "不要")), warning


def test_a_clean_document_has_nothing_to_say():
    assert living_doc_warning_text(CLEAN) is None
