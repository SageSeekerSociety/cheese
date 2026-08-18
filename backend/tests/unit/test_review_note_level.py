"""验收卡 note 的分级：谁来判断、判断得对不对。

分级过去在浏览器里，是一份写死的 emoji 列表——而后端每加一条新前缀，那份列表都
不会跟着变。`🌿 本地分支与 PR 分支已分叉` 就是这么漏掉的：卡在那个状态上是停住
了、等人动手，却和「还在等检查」渲染成同一个颜色。
"""

from app.domain.review import notes


def test_a_stuck_card_is_error_including_the_one_the_browser_missed():
    for prefix in (
        notes.REPUSH_FAILED_PREFIX,
        notes.REPUSH_DIVERGED_PREFIX,
        notes.POLL_PAUSED_PREFIX,
        notes.ACCEPT_PR_OPEN_FAILED_PREFIX,
        notes.ACCEPT_PR_STALLED_PREFIX,
        notes.GATE_ABANDONED_PREFIX,
    ):
        assert notes.note_level(f"{prefix}：细节") is notes.NoteLevel.error, prefix


def test_a_card_still_moving_is_info():
    for prefix in (
        notes.WAITING_CHECKS_PREFIX,
        notes.DEPLOY_STALLED_PREFIX,
        notes.FORCE_MERGED_PREFIX,
        notes.VOIDED_PREFIX,
    ):
        assert notes.note_level(f"{prefix}：细节") is notes.NoteLevel.info, prefix


def test_an_empty_note_has_no_level():
    assert notes.note_level("") is None
    assert notes.note_level("   ") is None


def test_a_note_with_no_known_prefix_is_info():
    assert notes.note_level("PR #12 检查全绿，已自动合并") is notes.NoteLevel.info


def test_every_prefix_in_the_module_is_classified():
    """新增一条前缀却忘了分级，它会被当成「还在走」—— 也就是 `🌿` 那个坑。

    这条测试是那张表的守卫：模块里每一个 *_PREFIX 都必须能被认出来。
    """
    prefixes = [
        v
        for k, v in vars(notes).items()
        if k.endswith("_PREFIX") and isinstance(v, str)
    ]
    assert prefixes, "没找到任何前缀常量，这条测试自己坏了"
    for prefix in prefixes:
        assert notes.note_level(prefix) is not None, prefix
