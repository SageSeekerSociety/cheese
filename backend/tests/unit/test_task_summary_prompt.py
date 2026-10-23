"""收尾那一轮的指令里，留痕记录是单独一条（ANYUE，2026-10-22）。

它原先只写在团队记忆里，由收尾那一轮的 AI 队友自己想起来。两次收尾，一次
贴了一次没贴——记忆是提醒，不是保证。所以它挪进平台自己发的那段指令里。

这里钉的是「说了什么」：单独一条、第一行就是那对括号、字段齐、正文原样贴。
提示词是纯字符串，没法在这层演一次真的收尾，所以判据是它必须说出的那几件事
（和 `assert-copy-by-parts-not-whole-line` 同一个理由）。
"""

from app.domain.review.task_landing import task_summary_prompt


def _text(**kw) -> str:
    return task_summary_prompt(title="改动页默认折叠", **kw)


def test_the_record_is_a_message_of_its_own():
    text = _text(pr_number=4693)

    assert "再单独发一条【留痕记录】" in text
    # 单独一条是这条规矩的全部意义：并进总结就没法整段圈选抄进表。
    assert "不要并进总结" in text
    assert "不要分几条发" in text


def test_the_record_says_which_fields_to_write():
    text = _text(pr_number=4693)

    for part in ("任务名", "PR 号加链接", "标题", "合并时间", "正文："):
        assert part in text, part
    assert "原样贴" in text and "不概括" in text


def test_the_agent_reads_the_pr_itself_from_the_number_it_was_given():
    # 平台上这一段话是纯字符串，取不到远端：号码由平台给，标题和正文由它自己读。
    assert "gh pr view 4693" in _text(pr_number=4693)
    assert "gh pr view" not in _text(pr_number=None)


def test_a_delivery_without_a_pr_writes_the_deliverable_instead():
    text = _text(pr_number=None)

    assert "没有 PR" in text
    assert "交付物名字" in text and "交付说明原文" in text


def test_closing_the_task_is_still_the_last_thing_it_does():
    text = _text(pr_number=1)

    assert text.index("【留痕记录】") < text.index("cheese_close_task")
