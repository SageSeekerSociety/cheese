"""Attach 收账 (#316 root-cause side): a backend restart kills the waiter, not
the working claude. The orphan sweep no longer re-prompts a turn claude already
received — so the spool settle has to finish the turn instead: a Stop parked
with no listener lands the final message, saves the finished session pointer,
and an undelivered prompt (the ONE re-send case) goes out as the original text.
"""

import asyncio
import time
import uuid

import pytest

from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import event_spool
from app.domain.agent.chat import ChatService
from app.domain.agent.runtime import AgentWorkRunner, InProcessBroker
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.project.services import ProjectService
from app.domain.topic.services import TopicService
from app.domain.workspace import service as ws
from tests.conftest import StubChannel, stub_compute
from tests.integration.conftest import chat_ws_url
from tests.turn_log import open_turn, open_turn_ids


def _spool_event(spool, eid: str, payload: dict) -> None:
    """Simulate the cheese-hook forwarder's atomic write of one hook."""
    event_spool.append(spool, eid, payload)


class _MustNotRun(StubChannel):
    """A screen whose being written to IS the failure: attach mode means no
    prompt is sent at all."""

    async def send_prompt(
        self, screen: uuid.UUID, prompt: str, images: list[dict] | None = None
    ) -> bool:
        del screen, prompt, images
        raise AssertionError("attach mode must never start a turn")


class _RecordingScreen(StubChannel):
    """Records every prompt it is asked to run (the re-send path's witness)."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[str] = []

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        del reply
        self.prompts.append(prompt)
        self.starts(topic_id, session_id="s-new")
        self.acknowledges(topic_id, prompt)
        self.stops(topic_id, "收到", session_id="s-new")


async def _seed_topic(factory) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="u")
        topic = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        pid, tid = project.id, topic.id
        await session.commit()
    return pid, tid


@pytest.mark.anyio
async def test_settle_lands_parked_stop_and_finishes_the_turn(
    client, tmp_path, monkeypatch
):
    """MessageDisplay + Stop parked while nobody listened: the settle lands the
    final reply once and retains its execution log, saves the
    finished session pointer, and empties the spool."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    pid, tid = await _seed_topic(factory)
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(_MustNotRun()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )

    _spool_event(
        ws.spool_dir(pid, tid),
        "m1",
        {"hook_event_name": "MessageDisplay", "delta": "收尾汇报：都做完了"},
    )
    _spool_event(
        ws.spool_dir(pid, tid),
        "s1",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "收尾汇报：都做完了",
            "session_id": "s-done",
        },
    )

    landed = await svc.settle_spool(tid)
    assert landed == 1  # retained output; Stop cannot publish a second copy

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
        resumes_by = await AgentSessionService(session).resume_token(tid, CHEESE_HANDLE)
    finals = [
        b
        for b in rows
        if b.kind == BlockKind.message and b.content == "收尾汇报：都做完了"
    ]
    assert finals == []
    progress = [b for b in rows if (b.meta or {}).get("progress")]
    assert len(progress) == 1
    assert progress[0].meta["in_room"] is False
    assert progress[0].meta.get("backfilled") is True
    assert resumes_by == "s-done"  # the next summon resumes the FINISHED session
    # The settle read to the end. Reading no longer deletes — the files live out
    # their retention — so what "drained" means is an empty tail past the cursor.
    spool = ws.spool_dir(pid, tid)
    assert event_spool.spool_entries(spool, after=event_spool.read_cursor(spool)) == []


@pytest.mark.anyio
async def test_settle_lands_a_stop_only_final_message(client, tmp_path, monkeypatch):
    """A Stop whose MessageDisplay never made it anywhere still lands its
    last_assistant_message — the turn's ending must not be lost with it."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    pid, tid = await _seed_topic(factory)
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(_MustNotRun()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    _spool_event(
        ws.spool_dir(pid, tid),
        "s-only",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "只有Stop带回来的结论",
            "session_id": "s-final",
        },
    )

    assert await svc.settle_spool(tid) == 1
    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
        resumes_by = await AgentSessionService(session).resume_token(tid, CHEESE_HANDLE)
    finals = [b for b in rows if b.content == "只有Stop带回来的结论"]
    assert len(finals) == 1
    assert finals[0].kind == BlockKind.event
    assert finals[0].meta["in_room"] is False
    assert finals[0].meta.get("eid") == "s-only"
    assert resumes_by == "s-final"
    # Idempotent: a second settle finds nothing to do.
    assert await svc.settle_spool(tid) == 0


@pytest.mark.anyio
async def test_orphan_with_parked_stop_is_settled_not_reprompted(
    client, tmp_path, monkeypatch
):
    """The full chain of the incident fix: a turn the transport had accepted,
    whose screen is gone by the time the sweep runs (the container went with the
    deploy). No prompt reaches the agent — asking again for work 芝士 already
    heard is what stacked five zombie turns on one topic — and the settle the
    sweep schedules finishes the turn out of the Stop the dead screen parked,
    saying nothing, because from the room's side nothing broke."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    pid, tid = await _seed_topic(factory)
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(_MustNotRun()),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    # No test should wait out the production debounce.
    real_schedule = ChatService.schedule_spool_settle
    monkeypatch.setattr(
        ChatService,
        "schedule_spool_settle",
        lambda self, topic_id, delay_s=2.0: real_schedule(self, topic_id, delay_s=0),
    )

    await open_turn(factory, tid, content="把测试跑绿", age_s=300, delivered=True)
    _spool_event(
        ws.spool_dir(pid, tid),
        "s1",
        {
            "hook_event_name": "Stop",
            "last_assistant_message": "跑绿了，收工",
            "session_id": "s-done",
        },
    )

    runner = AgentWorkRunner(InProcessBroker())
    assert await runner.resume_orphans(svc) == 0  # attach — nothing re-prompted

    async def _final_landed() -> bool:
        async with factory() as session:
            rows = await BlockRepository(session).list_for_topic(tid)
        return any(b.content == "跑绿了，收工" for b in rows)

    for _ in range(200):
        if await _final_landed():
            break
        await asyncio.sleep(0.01)
    assert await _final_landed()

    async with factory() as session:
        rows = await BlockRepository(session).list_for_topic(tid)
    # Nothing is announced. #316 added a verdict here because a restart left the
    # room looking dead — the backend half died and the session's output only
    # resurfaced later out of the spool. Retiring the turn (#508) removed that
    # break: the subscription lives with the screen and reattaches, so 芝士's
    # output keeps landing. The platform attached, the settle landed the Stop,
    # and there is no anomaly left for a person to act on.
    verdicts = [
        b
        for b in rows
        if b.author_type == AuthorType.system and "部署中断" in (b.content or "")
    ]
    assert verdicts == []
    assert await open_turn_ids(factory) == set()


@pytest.mark.anyio
async def test_zero_evidence_orphan_resends_the_original_text(
    client, tmp_path, monkeypatch
):
    """No block, no spool trace → the sweep re-sends, and the turn's prompt is
    the pending HUMAN message verbatim — not a continuation nudge."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    factory = client.test_factory
    _pid, tid = await _seed_topic(factory)
    agent = _RecordingScreen()
    svc = ChatService(
        session_factory=factory,
        compute=stub_compute(agent),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )
    # The interrupted turn had posted its human message before dying — that is
    # what the pending-message mechanism re-hands to the re-sent turn.
    async with factory() as session:
        await BlockRepository(session).add(
            project_id=_pid,
            topic_id=tid,
            author="u",
            author_type=AuthorType.human,
            content="修一下登录页",
            kind=BlockKind.message,
        )
        await session.commit()
    await open_turn(factory, tid, content="修一下登录页", age_s=300)
    # Collapse the 3s re-send delay, keep the real path.
    real_resend = AgentWorkRunner._schedule_resend
    monkeypatch.setattr(
        AgentWorkRunner,
        "_schedule_resend",
        lambda self, chat, topic_id, after_s, content, **kw: real_resend(
            self, chat, topic_id, 0.0, content, **kw
        ),
    )

    runner = AgentWorkRunner(InProcessBroker())
    assert await runner.resume_orphans(svc) == 1
    for _ in range(300):
        if agent.prompts:
            break
        await asyncio.sleep(0.01)
    assert len(agent.prompts) == 1
    assert "[u]: 修一下登录页" in agent.prompts[0]  # the original text, verbatim


def test_parked_hook_schedules_a_settle(client, tmp_path, monkeypatch):
    """The endpoint half of attach 收账: parking an event with no listener must
    also schedule the settle that drains it — otherwise a no-longer-re-prompted
    orphan's events sit invisible until a human happens to speak."""
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    scheduled: list[uuid.UUID] = []
    monkeypatch.setattr(
        ChatService,
        "schedule_spool_settle",
        lambda self, topic_id, delay_s=2.0: scheduled.append(topic_id),
    )
    pid, tid = uuid.uuid4(), uuid.uuid4()
    token = mint_scoped_token(project_id=str(pid), topic_id=str(tid), ttl_s=60)

    r = client.post(
        f"/sandbox/hooks/{tid}",
        json={"hook_event_name": "Stop", "last_assistant_message": "收工"},
        headers={"X-Cheese-Token": token, "X-Cheese-Event-Id": "e1"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["delivered"] is False
    assert scheduled == [tid]
    # The event really is parked for that settle to find.
    assert len(event_spool.spool_entries(ws.spool_dir(pid, tid))) == 1


def test_completed_live_turn_settles_before_another_prompt(
    client, stub_hooks, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    real_schedule = ChatService.schedule_spool_settle
    monkeypatch.setattr(
        ChatService,
        "schedule_spool_settle",
        lambda self, topic_id, delay_s=2.0: real_schedule(self, topic_id, delay_s=0),
    )
    project = client.post("/projects", json={"name": "P"}).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "T", "created_by": "u"},
    ).json()["data"]
    pid, tid = uuid.UUID(project["id"]), uuid.UUID(topic["id"])
    spool = ws.spool_dir(pid, tid)

    def emit_turn(topic_id, prompt, reply):
        del reply
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        for eid, payload in (
            ("live-message", {"hook_event_name": "MessageDisplay", "delta": "done"}),
            (
                "live-stop",
                {
                    "hook_event_name": "Stop",
                    "last_assistant_message": "done",
                    "session_id": "sess-test-1",
                },
            ),
        ):
            _spool_event(spool, eid, payload)
            stub_hooks.hook(topic_id, _eid=eid, **payload)

    monkeypatch.setattr(stub_hooks, "emit_turn", emit_turn)
    with client.websocket_connect(chat_ws_url(str(tid), "u")) as socket:
        socket.send_json({"type": "message", "content": "hello", "summon": True})
        while True:
            frame = socket.receive_json()
            assert frame["type"] != "error", frame
            if frame["type"] == "done":
                break
        for _ in range(100):
            cursor = event_spool.read_cursor(spool)
            if cursor is not None and not event_spool.spool_entries(spool, cursor):
                break
            client.get(f"/topics/{tid}")
            time.sleep(0.01)
        assert event_spool.read_cursor(spool) is not None
        assert event_spool.spool_entries(spool, event_spool.read_cursor(spool)) == []

    async def replies():
        async with client.test_factory() as session:
            return await BlockRepository(session).list_for_topic(tid)

    assert (
        len([block for block in asyncio.run(replies()) if block.content == "done"]) == 1
    )
