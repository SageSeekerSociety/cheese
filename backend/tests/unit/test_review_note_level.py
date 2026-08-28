"""验收卡 note 的分级：谁来判断、判断得对不对。

分级过去在浏览器里，是一份写死的 emoji 列表——后端每加一条新状态，那份列表都不会
跟着变。`🌿 本地分支与 PR 分支已分叉` 和 `🚪 PR 被关闭且没合并` 就是这么漏掉的：
卡在这两个状态上是停住了、等人动手，却和「还在等检查」渲染成同一个颜色。

现在判断的依据是卡自己的状态码，不是那句话怎么开的头。
"""

from app.domain.review import notes
from app.domain.review.notes import NoteCode, NoteLevel


def test_a_stuck_card_is_error():
    for code in (
        NoteCode.repush_failed,
        NoteCode.repush_diverged,
        NoteCode.poll_paused,
        NoteCode.checks_failed,
        NoteCode.merge_refused,
        NoteCode.merge_withheld,
        NoteCode.pr_closed_unmerged,
        NoteCode.pr_open_failed,
        NoteCode.accept_pr_open_failed,
        NoteCode.accept_pr_stalled,
        NoteCode.merge_conflict,
        NoteCode.gate_abandoned,
        NoteCode.pr_skipped,
    ):
        assert notes.note_level(code, "细节") is NoteLevel.error, code


def test_a_card_still_moving_is_info():
    for code in (
        NoteCode.waiting_checks,
        NoteCode.force_merged,
        NoteCode.voided,
        NoteCode.archived,
    ):
        assert notes.note_level(code, "细节") is NoteLevel.info, code


def test_an_empty_note_has_no_level_whatever_the_code():
    assert notes.note_level(None, "") is None
    assert notes.note_level(None, "   ") is None
    assert notes.note_level(NoteCode.repush_failed, "") is None


def test_a_note_with_no_code_is_info():
    assert notes.note_level(None, "PR #12 检查全绿，已自动合并") is NoteLevel.info


def test_the_wording_no_longer_decides_the_level():
    """判断和文案分家的那条断言：同一个码，怎么写都还是那个级别。

    反过来也一样——一句以 `⚠️` 开头、但没有码的交代不会因为那个字符变红。旧实现
    两条都做不到：它读的就是开头那个字符。
    """
    assert notes.note_level(NoteCode.repush_failed, "改成一句完全不同的话") is (
        NoteLevel.error
    )
    assert notes.note_level(None, "⚠️ 一句带感叹号的普通交代") is NoteLevel.info


def test_every_code_is_classified():
    """新增一个码却忘了在 `_STUCK` 里表态，它会被默默算成「还在走」。

    这条测试是那张表的守卫：漏掉一个码不再只是渲染得不对，而是这里会红。
    """
    for code in NoteCode:
        assert notes.note_level(code, "细节") in (NoteLevel.error, NoteLevel.info), code
