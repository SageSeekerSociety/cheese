"""母子传话的合并 (`app.domain.topic.relay.WakeCoalescer`).

Three relays in one parent turn must cost the child ONE extra turn, not three —
and, just as important, must not cost it ZERO: a relay landing mid-turn can
arrive after that turn built its prompt, so "a turn is already running, skip it"
would drop the message outright.
"""

import uuid

import pytest

from app.domain.room_task.place import Place
from app.domain.topic.models import TopicStatus
from app.domain.topic.relay import (
    RelayDelivery,
    RelayDirection,
    WakeCoalescer,
    deliver_or_wake,
    inline_line,
    relay_prompt,
    wake_target,
)


class _FakeTopic:
    def __init__(self, status=TopicStatus.active) -> None:
        self.id = uuid.uuid4()
        self.status = status
        self.title = "子活"
        self.project_id = uuid.uuid4()
        self.parent_id = None


def _fake_place(status=TopicStatus.active) -> Place:
    """A relay is delivered to a PLACE, not to a topic row. Coalescing is the
    room's own main line either way — what it owns is one place's wake window —
    so these hand over a room with no thread on it."""
    return Place(room=_FakeTopic(status))


class _RecordingSubmit:
    """Stands in for `AgentWorkRunner.submit`: records the call and hands back the
    `on_done` hook so the test can decide when that turn "ends"."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, _chat, topic_id, **kwargs):
        self.calls.append({"topic_id": topic_id, **kwargs})
        return uuid.uuid4()

    def finish_last(self) -> None:
        self.calls[-1]["on_done"]()

    @property
    def prompts(self) -> list[str]:
        return [c["content"] for c in self.calls]


def _wake(topic, message, submit, coalescer) -> bool:
    return wake_target(
        runner=None,
        chat=None,
        target=topic,
        direction=RelayDirection.TO_CHILD,
        sender_title="母话题",
        sender_id=uuid.uuid4(),
        message=message,
        coalescer=coalescer,
        submit=submit,
    )


def test_first_relay_wakes_immediately():
    submit, queue, topic = _RecordingSubmit(), WakeCoalescer(), _fake_place()

    assert _wake(topic, "第一条", submit, queue) is True
    assert len(submit.calls) == 1
    assert submit.calls[0]["topic_id"] == topic.id
    assert submit.calls[0]["summon"] is True
    assert "第一条" in submit.prompts[0]


def test_a_burst_costs_one_extra_turn_not_three():
    submit, queue, topic = _RecordingSubmit(), WakeCoalescer(), _fake_place()

    assert _wake(topic, "一", submit, queue) is True
    # Everything that arrives while that turn runs rides its follow-up.
    assert _wake(topic, "二", submit, queue) is False
    assert _wake(topic, "三", submit, queue) is False
    assert len(submit.calls) == 1
    assert queue.buffered(topic.id) == 2

    submit.finish_last()  # the woken turn ends
    assert len(submit.calls) == 2
    # 二 and 三 in ONE follow-up, and the receiver is told there were two.
    follow_up = submit.prompts[1]
    assert "二" in follow_up
    assert "三" in follow_up
    assert "2 条" in follow_up


def test_nothing_is_dropped_and_the_queue_drains():
    """The end of the burst releases ownership: the next relay wakes again."""
    submit, queue, topic = _RecordingSubmit(), WakeCoalescer(), _fake_place()

    _wake(topic, "一", submit, queue)
    _wake(topic, "二", submit, queue)
    submit.finish_last()  # → follow-up carrying 二
    assert len(submit.calls) == 2

    submit.finish_last()  # follow-up ends with nothing buffered
    assert len(submit.calls) == 2
    assert queue.buffered(topic.id) == 0

    assert _wake(topic, "三", submit, queue) is True
    assert len(submit.calls) == 3
    assert "三" in submit.prompts[2]


def test_two_topics_do_not_share_a_window():
    submit, queue = _RecordingSubmit(), WakeCoalescer()
    a, b = _fake_place(), _fake_place()

    assert _wake(a, "给A", submit, queue) is True
    assert _wake(b, "给B", submit, queue) is True
    assert len(submit.calls) == 2


def test_archived_target_is_never_woken():
    submit, queue = _RecordingSubmit(), WakeCoalescer()
    archived = _fake_place(status=TopicStatus.archived)

    assert _wake(archived, "还有一件事", submit, queue) is False
    assert submit.calls == []
    # …and it does not silently claim the topic, which would wedge it forever.
    assert queue.offer(archived.id, "x") == ["x"]


# --- 迅捷: 对方正在跑一轮就直接插进去 ----------------------------------------


class _FakeChat:
    """Stands in for ChatService's mid-turn injection (`merge_into_running_turn`)."""

    def __init__(self, *, live: bool) -> None:
        self.live = live
        self.injected: list[tuple[uuid.UUID, str, str]] = []

    async def merge_into_running_turn(self, topic_id, block_ids, content, author):
        del block_ids
        if not self.live:
            return False
        self.injected.append((topic_id, content, author))
        return True


async def _deliver(topic, message, chat, submit, queue) -> str:
    return await deliver_or_wake(
        chat=chat,
        runner=None,
        target=topic,
        direction=RelayDirection.TO_CHILD,
        sender_title="母话题",
        sender_id=uuid.uuid4(),
        block_id=uuid.uuid4(),
        message=message,
        coalescer=queue,
        submit=submit,
    )


@pytest.mark.anyio
async def test_a_running_target_gets_the_line_injected_not_a_new_turn():
    """The fast path: 对方正在跑 → 秒级插进那一轮，不排队、不新起一轮。"""
    chat, submit, queue, topic = (
        _FakeChat(live=True),
        _RecordingSubmit(),
        WakeCoalescer(),
        _fake_place(),
    )

    assert (
        await _deliver(topic, "口径改了", chat, submit, queue) == RelayDelivery.INJECTED
    )
    assert submit.calls == []  # no extra turn was spent
    _topic_id, line, author = chat.injected[0]
    assert "口径改了" in line
    # Where it came from is in the line itself; the author slot carries which
    # WAY it came, so the receiver can weigh it without knowing any titles.
    assert "母话题" in line
    assert author == "房间"
    # Injection must not claim ownership of the topic — the next relay is free to
    # take whichever path is fastest THEN.
    assert queue.buffered(topic.id) == 0
    assert (
        await _deliver(topic, "再一条", chat, submit, queue) == RelayDelivery.INJECTED
    )


@pytest.mark.anyio
async def test_no_live_screen_falls_back_to_a_turn_then_to_merging():
    """Every transport that cannot inject (SDK/per-turn providers) still delivers:
    first relay starts a turn, the ones behind it ride that turn's follow-up."""
    chat, submit, queue, topic = (
        _FakeChat(live=False),
        _RecordingSubmit(),
        WakeCoalescer(),
        _fake_place(),
    )

    assert await _deliver(topic, "一", chat, submit, queue) == RelayDelivery.WOKE
    assert await _deliver(topic, "二", chat, submit, queue) == RelayDelivery.MERGED
    assert len(submit.calls) == 1


@pytest.mark.anyio
async def test_archived_target_is_not_even_offered_to_the_live_screen():
    chat, submit, queue = _FakeChat(live=True), _RecordingSubmit(), WakeCoalescer()
    archived = _fake_place(status=TopicStatus.archived)

    assert await _deliver(archived, "x", chat, submit, queue) == RelayDelivery.ARCHIVED
    assert chat.injected == []
    assert submit.calls == []


def test_injected_line_carries_source_and_authority():
    """It lands mid-thought, so it must say where it came from and how much it
    outranks the brief — an unmarked aside gets finished around, not acted on."""
    to_child = inline_line(
        direction=RelayDirection.TO_CHILD, sender_title="母话题X", message="改口径"
    )
    assert "母话题X" in to_child
    assert "以这条为准" in to_child
    to_parent = inline_line(
        direction=RelayDirection.TO_PARENT, sender_title="子活Y", message="卡住了"
    )
    assert "子活Y" in to_parent
    assert "卡住了" in to_parent


@pytest.mark.parametrize(
    ("direction", "must_contain"),
    [
        (RelayDirection.TO_CHILD, "以这条为准"),
        (RelayDirection.TO_PARENT, "不是结论回流"),
    ],
)
def test_prompt_says_what_kind_of_message_this_is(direction, must_contain):
    prompt = relay_prompt(
        direction=direction,
        sender_title="来源话题",
        sender_id=uuid.uuid4(),
        messages=["原话在这"],
    )
    assert "原话在这" in prompt
    assert "来源话题" in prompt
    assert must_contain in prompt
    # A single message must NOT claim a count — that line only makes sense when
    # more than one was merged.
    assert "一共来了" not in prompt
