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

Three actions, three tests:

  1. 发消息      — the resumed turn re-narrating must not post a second copy
  2. 记周报      — must not stack a second 周报 row
  3. 开 PR       — must not open a second PR / file a second card

Action 3 needs no new mechanism: 开 PR was ALREADY protected, by two guards that
predate this work (one non-terminal accept card per topic, and GitHub-side
adoption of a PR already open on the same head branch). The test is here anyway,
because "we believe it is covered" and "it is covered" are different claims.
"""

import uuid

import pytest
from sqlalchemy import func, select

from app.api.deps import get_work_runner
from app.domain.agent.chat import ChatService
from app.domain.block.models import Block, BlockKind
from app.domain.idempotency.keys import action_key
from tests.conftest import StubChannel, settle_turn, stub_compute
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import join_project_team, post_project

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
    pid = post_project(client, json={"name": "P"}).json()["data"]["id"]
    # 2026-09-27: 递卡那道门现在先问「这个人在不在房间里」
    # (`_require_reviewer_in_room`)。这个文件里的卡（以及拆出来的子话题）都点名
    # alice，就让她像真实参与者一样在项目里 —— 要检验的是重发去重，不是名册。
    join_project_team(client, pid, "alice")
    return pid


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

    new_session_id = "s1"

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
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
        session_factory=client.test_request_factory,
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

    client.portal.call(lambda: _both())

    said = client.portal.call(
        lambda: _count(
            client.test_request_factory,
            select(func.count())
            .select_from(Block)
            .where(
                Block.conversation_id == uuid.UUID(tid),
                Block.kind == BlockKind.event,
                Block.meta["progress"].as_boolean().is_(True),
                Block.content == text,
            ),
        )
    )
    assert said == 1, "重发把同一句话又说了一遍"


def test_message_dedup_does_not_leak_across_continuations(client, tmp_path):
    """The complement, and the reason the key is not just a content hash: the
    SAME text in a LATER, unrelated unit of work is a second message, not a
    duplicate. A dedup that swallowed it would silence 芝士 for saying "好的"
    twice in one topic."""
    pid = _project(client)
    tid = _topic(client, pid)
    text = "好的"
    chat = ChatService(
        session_factory=client.test_request_factory,
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

    client.portal.call(lambda: _both())

    said = client.portal.call(
        lambda: _count(
            client.test_request_factory,
            select(func.count())
            .select_from(Block)
            .where(
                Block.conversation_id == uuid.UUID(tid),
                Block.kind == BlockKind.event,
                Block.meta["progress"].as_boolean().is_(True),
                Block.content == text,
            ),
        )
    )
    assert said == 2


# --- 2. 记周报 ---------------------------------------------------------------


def test_weekly_is_recorded_once(client, in_a_turn):
    pid = _project(client)
    tid = _topic(client, pid)
    body = {"body": "这周把分页接口做完了"}

    assert client.post(f"/topics/{tid}/weekly", json=body).status_code == 200
    assert client.post(f"/topics/{tid}/weekly", json=body).status_code == 200

    rows = client.portal.call(
        lambda: _count(
            client.test_request_factory,
            select(func.count())
            .select_from(Block)
            .where(
                Block.conversation_id == uuid.UUID(tid),
                Block.kind == BlockKind.weekly,
            ),
        )
    )
    assert rows == 1, "重发把同一份周报记了两遍"


# --- 3. 开 PR ----------------------------------------------------------------


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
        f"/topics/{delivery_task_id(client, tid)}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
        headers=delivery_headers(client, tid),
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/topics/{delivery_task_id(client, tid)}/accept-card",
        json={
            "change_subject": "chore(test): file an accept card",
            "reviewer_handle": "alice",
            "routing_reason": "最懂",
        },
        headers=delivery_headers(client, tid),
    )
    assert second.status_code >= 400, "第二张验收卡没被拦住——它能开出第二个 PR"


# --- 反向对照 ----------------------------------------------------------------


def test_without_a_running_turn_nothing_is_deduped(client, monkeypatch):
    """Proves the weekly test above is not vacuous, and states the rule
    deliberately: outside an automatic turn there is no continuation and no
    dedup. A human pressing 记周报 twice means it twice — the risk this whole
    mechanism exists for is created by 自动重发, not by people."""
    runner = get_work_runner()
    monkeypatch.setattr(runner, "continuation_for", lambda _topic_id: None)
    pid = _project(client)
    tid = _topic(client, pid)

    for _ in range(2):
        assert (
            client.post(f"/topics/{tid}/weekly", json={"body": "同一份"}).status_code
            == 200
        )

    weeklies = client.portal.call(
        lambda: _count(
            client.test_request_factory,
            select(func.count())
            .select_from(Block)
            .where(
                Block.conversation_id == uuid.UUID(tid), Block.kind == BlockKind.weekly
            ),
        )
    )
    assert weeklies == 2


# --- the mechanism itself ----------------------------------------------------


def test_the_same_key_can_only_be_claimed_once(client):
    """The invariant every test above rests on, asserted directly and across
    two SEPARATE sessions — the durability that makes this survive a restart is
    the DB row, not anything held in memory."""
    from app.domain.idempotency import store as idem

    key = action_key(CONTINUATION, "weekly", "同一件事")

    async def _claims() -> tuple[bool, bool]:
        async with client.test_request_factory() as s1:
            first = await idem.claim(s1, key, action="weekly", scope_id="t")
            await s1.commit()
        async with client.test_request_factory() as s2:
            second = await idem.claim(s2, key, action="weekly", scope_id="t")
            await s2.commit()
        return first, second

    first, second = client.portal.call(lambda: _claims())
    assert first is True
    assert second is False
