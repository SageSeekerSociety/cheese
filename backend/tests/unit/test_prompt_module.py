"""提示词/上下文渲染：从 ``prompt`` 直接测，不经过 ``ChatService``、不碰数据库。

进度层、开场事实、话题披露、平台提示进 prompt 的那一段由
``test_progress_prompt.py``、``test_session_opening_prompt.py``、
``test_prompt_topic_disclosure.py`` 与 ``test_doc_change_notice.py`` /
``test_message_edit.py`` 覆盖。这里测的是另外几件——「哪些块是这一轮还没读进去的
输入」「重放几次才值得说一句」「整理上下文那两行怎么变」。
"""

from types import SimpleNamespace

from app.domain.agent import prompt
from app.domain.agent.service import AgentCompacting
from app.domain.agent.turn.state import inputs
from app.domain.block.models import (
    AGENT_NOTICE_META_KEY,
    CONSUMED_TURN_META_KEY,
    BlockKind,
)
from app.domain.event_author import AuthorType
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.notice_meta import (
    EVENT_CONTEXT_COMPACT,
    SEVERITY_INFO,
    SEVERITY_WARN,
    WHO_PLATFORM,
)


def _block(
    *,
    author: str = "someone",
    author_type: AuthorType = AuthorType.participant,
    kind: BlockKind = BlockKind.message,
    content: str = "话",
    meta: dict | None = None,
) -> SimpleNamespace:
    """只带那几个被读的字段的鸭子。

    ``prompt.py`` 里这几件只读 ``author`` / ``author_type`` / ``kind`` /
    ``content`` / ``meta``，所以这里不必造一个真 ``Block``——真的那个要挂在一个会话
    上才像话，而「不碰数据库」正是这个文件想证明的事。
    """
    return SimpleNamespace(
        author=author,
        author_type=author_type,
        kind=kind,
        content=content,
        meta=meta if meta is not None else {},
    )


# ---- 待读窗口：由真实准备与收尾共用 state owner，保留全部行为断言 ----


def test_a_message_after_the_last_agent_line_is_pending():
    history = [
        _block(content="早在芝士说话之前"),
        _block(author=CHEESE_HANDLE, content="答"),
        _block(content="芝士说完之后"),
    ]
    assert inputs._pending_input_blocks(history) == [history[2]]


def test_a_block_a_turn_already_stamped_is_not_pending():
    history = [
        _block(author=CHEESE_HANDLE, content="答"),
        _block(content="读过了", meta={CONSUMED_TURN_META_KEY: "turn-1"}),
        _block(content="还没读"),
    ]
    assert inputs._pending_input_blocks(history) == [history[2]]


def test_a_tracked_input_is_pending_even_before_the_watermark():
    """位置水位盖不住「明确标记过还没读」的输入。

    轮次运行中到达的消息，``created_at`` 排在那一轮的回复之前，位置水位会把它切掉，
    而它恰恰是这一轮要读的。带 ``consumed_turn: null`` 标记的新输入不受水位管，这也
    正是那个 null 要「在场」的原因。
    """
    history = [
        _block(content="轮次跑着的时候到的", meta={CONSUMED_TURN_META_KEY: None}),
        _block(author=CHEESE_HANDLE, content="那一轮答的"),
    ]
    assert inputs._pending_input_blocks(history) == [history[0]]


def test_a_platform_event_is_not_an_input_to_read():
    history = [
        _block(author=CHEESE_HANDLE, content="答"),
        _block(
            author="platform",
            author_type=AuthorType.platform,
            kind=BlockKind.event,
            content="闸门没过",
        ),
    ]
    assert inputs._pending_input_blocks(history) == []


def test_only_what_a_participant_said_counts_as_pending_input():
    assert inputs._is_pending_input(_block(kind=BlockKind.message))
    assert inputs._is_pending_input(_block(kind=BlockKind.attachment))
    assert not inputs._is_pending_input(_block(kind=BlockKind.weekly))
    assert not inputs._is_pending_input(
        _block(author_type=AuthorType.platform, kind=BlockKind.message)
    )


# ---- 平台通知：说了但没人读过 ----


def test_a_notice_the_platform_left_is_pending_until_a_turn_stamps_it():
    unread = _block(meta={AGENT_NOTICE_META_KEY: "文档改了"})
    read = _block(
        meta={AGENT_NOTICE_META_KEY: "文档改了", CONSUMED_TURN_META_KEY: "turn-1"}
    )
    plain = _block(content="一句人话")
    assert prompt._pending_platform_notices([unread, read, plain]) == [unread]


# ---- 收件人：这一轮读谁的输入 ----


def test_input_for_another_agent_is_not_this_turns():
    for_other = _block(meta={"agent_recipient": {"handle": "cheese-abc"}})
    assert inputs._addressed_to(for_other, "cheese-abc")
    assert not inputs._addressed_to(for_other, CHEESE_HANDLE)


def test_input_with_no_recipient_belongs_to_the_rooms_agent():
    assert inputs._addressed_to(_block(), CHEESE_HANDLE)


# ---- 重放：几次才值得在房间里说一句 ----


def test_one_retry_is_ordinary_and_a_third_one_is_a_pattern():
    pending = [_block(content="在吗")]
    assert prompt._replay_notice(prompt._REPLAY_NOTICE_AT - 1, pending) is None
    assert prompt._replay_notice(prompt._REPLAY_NOTICE_AT, pending) is not None


def test_the_replay_line_throttles_once_the_state_is_known():
    pending = [_block(content="在吗")]
    assert prompt._replay_notice(prompt._REPLAY_NOTICE_AT + 1, pending) is None
    assert prompt._replay_notice(prompt._REPLAY_NOTICE_EVERY, pending) is not None
    assert prompt._replay_notice(prompt._REPLAY_NOTICE_EVERY + 1, pending) is None


def test_the_replay_line_names_the_count_and_the_oldest_one_only():
    """一行，且任何批量下都只有一行：其余那些消息就在时间线上方，不必再抄一遍。"""
    pending = [_block(content="最早的一条"), _block(content="后到的")]
    line = prompt._replay_notice(3, pending)
    assert line is not None
    assert "\n" not in line
    assert "2 条" in line and "第 3 次" in line
    assert "最早的一条" in line and "后到的" not in line


def test_a_long_oldest_message_is_clipped_in_the_replay_line():
    line = prompt._replay_notice(3, [_block(content="字" * 100)])
    assert line is not None
    assert "字" * 24 + "…" in line


def test_an_image_is_named_as_an_image_in_the_replay_line():
    line = prompt._replay_notice(3, [_block(kind=BlockKind.attachment, content="")])
    assert line is not None and "图片" in line


def test_a_replay_with_nothing_pending_still_says_the_count():
    line = prompt._replay_notice(3, [])
    assert line is not None and "0 条" in line


# ---- 整理上下文：一行，原地改口 ----


def test_a_running_compaction_says_why_the_room_is_silent():
    content, meta = prompt._compaction_notice(AgentCompacting())
    assert "正在整理上下文" in content
    assert meta["state"] == "running"
    assert meta["event_type"] == EVENT_CONTEXT_COMPACT
    assert meta["severity"] == SEVERITY_INFO
    assert meta["who"] == WHO_PLATFORM
    assert meta["detail"] is None


def test_a_compaction_that_failed_says_so_with_the_reason():
    content, meta = prompt._compaction_notice(
        AgentCompacting(done=True, error="context window exhausted")
    )
    assert content == "上下文整理没有完成"
    assert meta["state"] == "over"
    assert meta["severity"] == SEVERITY_WARN
    assert meta["detail"] == "context window exhausted"
    assert meta["detail_label"] == "原因"


def test_a_compaction_that_came_back_says_it_carries_on():
    content, meta = prompt._compaction_notice(AgentCompacting(done=True))
    assert content == "上下文已整理，接着处理"
    assert meta["state"] == "over"
    assert meta["severity"] == SEVERITY_INFO
    assert meta["detail"] is None and meta["detail_label"] is None
