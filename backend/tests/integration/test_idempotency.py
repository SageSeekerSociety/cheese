"""同一把钥匙执行两次，只产生一次效果 (④ 重发, 下半).

The upper half (``test_turn_exit_paths.py``) narrows the window in which a side
effect can be committed while the record of it is not. It cannot close it:
``asyncio.shield`` survives cancellation, not SIGKILL, and nothing survives the
power going out. So every side effect a re-sent turn could repeat carries a
durable key, and this file asserts the property one action at a time.

The key is scoped to the **continuation** — the logical unit of work that a turn
and its re-send share (``AgentWorkRunner.continuation_for``). That is what makes
"the re-sent 芝士 re-doing it" and "someone legitimately doing the same thing
next week" distinguishable at all; see ``domain.idempotency.keys``.

Five actions, five tests:

  1. 发消息      — the resumed turn re-narrating must not post a second copy
  2. 拆子话题    — must not spawn a second 分身 on the same brief
  3. 记决策      — must not stack a second 决策记录 row
  4. 钉里程碑    — must not pin the same milestone twice
  5. 开 PR       — must not open a second PR / file a second card

Action 5 needs no new mechanism: 开 PR was ALREADY protected, by two guards that
predate this work (one non-terminal accept card per topic, and GitHub-side
adoption of a PR already open on the same head branch). The test is here anyway,
because "we believe it is covered" and "it is covered" are different claims.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from app.api.deps import get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.block.models import Block, BlockKind
from app.domain.idempotency.keys import action_key
from app.domain.milestone.models import Milestone
from app.domain.room_task.models import Task
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.integration.conftest import session_auth_headers

# One fixed continuation for every test here: it stands for "the interrupted
# turn and the turn that resumed it", which is the whole point — two separate
# turns, one unit of work.
CONTINUATION = uuid.UUID("11111111-2222-3333-4444-555555555555")


@pytest.fixture
def in_a_turn(monkeypatch):
    """Make the endpoints believe a turn of ours is running on any topic.

    Production gets this from the runner's live lifecycle record; a test that
    started a real background turn just to read one uuid back out would be
    testing the turn machinery, not the dedup."""
    runner = get_work_runner()
    monkeypatch.setattr(runner, "continuation_for", lambda _topic_id: CONTINUATION)
    return runner


def _project(client) -> str:
    return client.post("/projects", json={"name": "P"}).json()["data"]["id"]


def _topic(client, project_id: str, title: str = "母话题") -> str:
    return client.post(
        "/topics", json={"project_id": project_id, "title": title}
    ).json()["data"]["id"]


async def _count(factory, stmt) -> int:
    async with factory() as s:
        return (await s.execute(stmt)).scalar_one()


# --- 1. 发消息 ---------------------------------------------------------------


class _SameMessageTwice(StubChannel):
    """A 芝士 that says the exact same thing on both attempts — which is what a
    resumed session with no memory of the first attempt does."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.starts(topic_id, session_id="s1")
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, self._text)
        self.stops(topic_id, self._text, session_id="s1")


def test_message_is_not_posted_twice_under_one_continuation(client, tmp_path):
    pid = _project(client)
    tid = _topic(client, pid)
    text = "我先把这五条落到代码上逐一自查，不动手改。"
    chat = ChatService(
        session_factory=client.test_factory,
        compute=stub_compute(_SameMessageTwice(text)),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    async def _turn() -> None:
        async for _ in chat.converse(
            topic_id=uuid.UUID(tid),
            author="u",
            content="做事",
            summon=True,
            continuation_id=CONTINUATION,
        ):
            pass
        await settle_turn(chat, uuid.UUID(tid))

    async def _both() -> None:
        await _turn()  # the attempt that got interrupted
        await _turn()  # the auto-resume, saying the same thing again

    asyncio.run(_both())

    said = asyncio.run(
        _count(
            client.test_factory,
            select(func.count())
            .select_from(Block)
            .where(
                Block.topic_id == uuid.UUID(tid),
                Block.kind == BlockKind.message,
                Block.content == text,
            ),
        )
    )
    assert said == 1, "重发把同一句话又说了一遍"


def test_kickoff_message_is_not_posted_twice_under_one_turn(client, tmp_path):
    """A kickoff turn (分身自动开工 / parent digesting a conclusion) is the one
    turn shape that ran with NO continuation — so an interrupted kickoff's
    auto-resume re-said everything, unprotected. It must claim under its own
    turn id like every converse turn does."""
    pid = _project(client)
    tid = _topic(client, pid)
    text = "领到任务了，我先把仓库结构过一遍。"
    chat = ChatService(
        session_factory=client.test_factory,
        compute=stub_compute(_SameMessageTwice(text)),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    kickoff_turn = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    async def _turn() -> None:
        async for _ in chat.kickoff(topic_id=uuid.UUID(tid), turn_id=kickoff_turn):
            pass

    async def _both() -> None:
        await _turn()  # the attempt that got interrupted
        await _turn()  # the auto-resume, saying the same thing again

    asyncio.run(_both())

    said = asyncio.run(
        _count(
            client.test_factory,
            select(func.count())
            .select_from(Block)
            .where(
                Block.topic_id == uuid.UUID(tid),
                Block.kind == BlockKind.message,
                Block.content == text,
            ),
        )
    )
    assert said == 1, "kickoff 的重发把同一句话又说了一遍"


def test_message_dedup_does_not_leak_across_continuations(client, tmp_path):
    """The complement, and the reason the key is not just a content hash: the
    SAME text in a LATER, unrelated unit of work is a second message, not a
    duplicate. A dedup that swallowed it would silence 芝士 for saying "好的"
    twice in one topic."""
    pid = _project(client)
    tid = _topic(client, pid)
    text = "好的"
    chat = ChatService(
        session_factory=client.test_factory,
        compute=stub_compute(_SameMessageTwice(text)),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    async def _turn(cont: uuid.UUID) -> None:
        async for _ in chat.converse(
            topic_id=uuid.UUID(tid),
            author="u",
            content="做事",
            summon=True,
            continuation_id=cont,
        ):
            pass
        await settle_turn(chat, uuid.UUID(tid))

    async def _both() -> None:
        await _turn(uuid.uuid4())
        await _turn(uuid.uuid4())

    asyncio.run(_both())

    said = asyncio.run(
        _count(
            client.test_factory,
            select(func.count())
            .select_from(Block)
            .where(
                Block.topic_id == uuid.UUID(tid),
                Block.kind == BlockKind.message,
                Block.content == text,
            ),
        )
    )
    assert said == 2


# --- 2. 拆子话题 -------------------------------------------------------------


def test_split_does_not_spawn_a_second_subtopic(client, in_a_turn, monkeypatch):
    """A re-sent turn re-splitting leaves the room holding two threads on one
    brief, and nobody can tell which of them the work is happening in.

    Dispatch starts no worker of its own any more — the caller does that, and
    would do it once per row it was handed. So the row is the whole of what has
    to not double.
    """
    kickoffs: list[uuid.UUID] = []
    monkeypatch.setattr(
        in_a_turn,
        "submit_kickoff",
        lambda _chat, topic_id, **_kw: kickoffs.append(topic_id) or uuid.uuid4(),
    )
    pid = _project(client)
    tid = _topic(client, pid)
    body = {"title": "数据清洗", "brief": "把脏数据洗掉", "created_by": "cheese"}

    first = client.post(f"/topics/{tid}/split", json=body)
    second = client.post(f"/topics/{tid}/split", json=body)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text

    children = asyncio.run(
        _count(
            client.test_factory,
            select(func.count())
            .select_from(Task)
            .where(Task.room_id == uuid.UUID(tid)),
        )
    )
    assert children == 1, "重发派出了第二条支线"
    assert kickoffs == [], "派活不该起任何会话——分身是调用方在自己会话里起的"
    # The replay gets the FIRST child back, not an error: a resumed 芝士 asking
    # again should learn what already exists.
    assert second.json()["data"]["id"] == first.json()["data"]["id"]


# --- 3. 记决策 ---------------------------------------------------------------


def test_decision_is_recorded_once(client, in_a_turn):
    pid = _project(client)
    tid = _topic(client, pid)
    body = {"decision": "用 item-based CF"}

    assert client.post(f"/topics/{tid}/decision", json=body).status_code == 200
    assert client.post(f"/topics/{tid}/decision", json=body).status_code == 200

    rows = asyncio.run(
        _count(
            client.test_factory,
            select(func.count())
            .select_from(Block)
            .where(
                Block.topic_id == uuid.UUID(tid),
                Block.kind == BlockKind.decision,
            ),
        )
    )
    assert rows == 1, "重发把同一条决策记了两遍"


# --- 4. 钉里程碑 -------------------------------------------------------------


def test_milestone_is_pinned_once(client, in_a_turn):
    pid = _project(client)
    tid = _topic(client, pid)
    body = {
        "title": "中期汇报",
        "due_date": "2026-06-20",
        "source_topic_id": tid,
    }

    assert client.post(f"/projects/{pid}/milestones", json=body).status_code == 200
    assert client.post(f"/projects/{pid}/milestones", json=body).status_code == 200

    rows = asyncio.run(
        _count(
            client.test_factory,
            select(func.count())
            .select_from(Milestone)
            .where(Milestone.project_id == uuid.UUID(pid)),
        )
    )
    assert rows == 1, "重发把同一个里程碑钉了两次"


# --- 5. 开 PR ----------------------------------------------------------------


def test_second_accept_card_is_refused_so_no_second_pr(client):
    """开 PR 的幂等不是这次加的，是本来就有的：一个话题同时只能有一张非终态
    验收卡，而 PR 只能由卡开出。重发的芝士再递一张卡会被拒，所以开不出第二个 PR。

    (The second guard sits on the GitHub side: the head branch is derived from
    the topic id, so a re-open resolves to the PR already on that branch and is
    adopted rather than duplicated — `github_pr.open_pull_request`'s contract,
    `already_existed=True`.)"""
    pid = _project(client)
    tid = _topic(client, pid)

    first = client.post(
        f"/topics/{tid}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
        headers=session_auth_headers("cheese"),
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/topics/{tid}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
        headers=session_auth_headers("cheese"),
    )
    assert second.status_code >= 400, "第二张验收卡没被拦住——它能开出第二个 PR"


# --- 反向对照 ----------------------------------------------------------------


def test_without_a_running_turn_nothing_is_deduped(client, monkeypatch):
    """Proves the three endpoint tests above are not vacuous, and states the
    rule deliberately: outside an automatic turn there is no continuation and
    no dedup. A human pressing 记决策 twice means it twice — the risk this whole
    mechanism exists for is created by 自动重发, not by people."""
    runner = get_work_runner()
    monkeypatch.setattr(runner, "continuation_for", lambda _topic_id: None)
    kickoffs: list[uuid.UUID] = []
    monkeypatch.setattr(
        runner,
        "submit_kickoff",
        lambda _chat, topic_id, **_kw: kickoffs.append(topic_id) or uuid.uuid4(),
    )
    pid = _project(client)
    tid = _topic(client, pid)

    for _ in range(2):
        assert (
            client.post(
                f"/topics/{tid}/decision", json={"decision": "同一条"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/projects/{pid}/milestones",
                json={
                    "title": "同一个",
                    "due_date": "2026-06-20",
                    "source_topic_id": tid,
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/topics/{tid}/split",
                json={"title": "同一个子话题", "created_by": "cheese"},
            ).status_code
            == 200
        )

    decisions = asyncio.run(
        _count(
            client.test_factory,
            select(func.count())
            .select_from(Block)
            .where(Block.topic_id == uuid.UUID(tid), Block.kind == BlockKind.decision),
        )
    )
    milestones = asyncio.run(
        _count(
            client.test_factory,
            select(func.count())
            .select_from(Milestone)
            .where(Milestone.project_id == uuid.UUID(pid)),
        )
    )
    children = asyncio.run(
        _count(
            client.test_factory,
            select(func.count())
            .select_from(Task)
            .where(Task.room_id == uuid.UUID(tid)),
        )
    )
    assert (decisions, milestones, children) == (2, 2, 2)


# --- the mechanism itself ----------------------------------------------------


def test_the_same_key_can_only_be_claimed_once(client):
    """The invariant every test above rests on, asserted directly and across
    two SEPARATE sessions — the durability that makes this survive a restart is
    the DB row, not anything held in memory."""
    from app.domain.idempotency import store as idem

    key = action_key(CONTINUATION, "decision", "同一件事")

    async def _claims() -> tuple[bool, bool]:
        async with client.test_factory() as s1:
            first = await idem.claim(s1, key, action="decision", scope_id="t")
            await s1.commit()
        async with client.test_factory() as s2:
            second = await idem.claim(s2, key, action="decision", scope_id="t")
            await s2.commit()
        return first, second

    first, second = asyncio.run(_claims())
    assert first is True
    assert second is False
