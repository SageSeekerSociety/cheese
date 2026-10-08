"""A routine's run is a message of its teammate's in the channel's main line,
and the run happens in that message's 支线.

The rules, as stated before the code was written:

- the run's own turn may keep what it produces, though a 支线 reads only; a
  question asked in that 支线 afterwards reads only, like in any 支线;
- the rule's owner takes part in that 支线, so a reply there reaches them;
- once settled, the message says how it went: what the teammate handed back
  when it succeeded, nothing in the teammate's name when it did not, and it
  sits at the bottom of the main line, at the moment the run was settled.
"""

import uuid
from contextlib import asynccontextmanager

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.thread import reads as thread_reads
from tests.conftest import StubChannel, settle_turn
from tests.integration.conftest import post_message
from tests.integration.test_routines import (
    OWNER,
    PERSON,
    _make_due,
    _project,
    _room,
    _sweep,
    _weekly,
)


class Screen(StubChannel):
    """A turn says one line; each session it opens says whether it may write."""

    def __init__(self, **policy: float) -> None:
        super().__init__(**policy)
        self.scratch: list[bool] = []

    @asynccontextmanager
    async def prepare_session(self, *, scratch=False, **kwargs):
        self.scratch.append(scratch)
        async with super().prepare_session(scratch=scratch, **kwargs) as prepared:
            yield prepared

    async def send_prompt(self, screen: uuid.UUID, prompt: str) -> bool:
        self.starts(screen, session_id="run")
        self.stops(screen, "Done.", session_id="run")
        return True


def _db(client, fn):
    async def go():
        async with client.test_request_factory() as session:
            out = await fn(session)
            await session.commit()
            return out

    return client.portal.call(go)


def _main_line(client, room):
    return client.get(f"/topics/{room}/blocks", headers=PERSON).json()["data"]["data"]


def _run_message(client, room, run_id):
    return next(
        b
        for b in _main_line(client, room)
        if (b.get("routine_run") or {}).get("run_id") == run_id
    )


def _fired(client):
    project = _project(client)
    room = _room(client, project)
    routine = _weekly(client, room, headers=PERSON).json()["data"]
    _make_due(client, routine["id"])
    _, runner = _sweep(client)
    conversation, submitted = runner.submitted[0]
    runs = client.get(f"/routines/{routine['id']}", headers=PERSON).json()["data"]
    return room, routine, runs["runs"][0], conversation, submitted


def test_the_run_keeps_its_results_and_a_question_after_it_does_not(client, tmp_path):
    room, _routine, _run, conversation, submitted = _fired(client)
    screen = Screen()
    svc = ChatService(
        session_factory=client.test_request_factory,
        compute=ComputePool([screen.runtime], screen.name),
        base_system_prompt="You are Cheese.",
        workspace_root=str(tmp_path / "ws"),
    )
    thread = uuid.UUID(conversation)

    async def run():
        async for _ in svc.converse(
            topic_id=thread,
            author=submitted["author"],
            content=submitted["content"],
            summon=submitted["addressed"],
            turn_id=submitted["turn_id"],
            delivery_id=submitted["delivery_id"],
            recipient_instance_id=submitted["recipient_instance_id"],
        ):
            pass
        await settle_turn(svc, thread)

    client.portal.call(run)
    assert screen.scratch and screen.scratch[-1] is False, "这次执行不能存结果"

    async def ask():
        async for _ in svc.converse(
            topic_id=thread, author=OWNER, content="卡住的那个写上预计时间", summon=True
        ):
            pass
        await settle_turn(svc, thread)

    client.portal.call(ask)
    assert screen.scratch[-1] is True, "追问那一轮的改动留得下"
    assert "卡住的那个写上预计时间" in screen.told, "换成只读之后，追问那一轮没有跑起来"


def test_the_rules_owner_takes_part_in_the_runs_thread(client):
    _room_id, routine, _run, conversation, _submitted = _fired(client)

    async def people(session):
        return await thread_reads.people_in(session, uuid.UUID(conversation))

    assert routine["owner_handle"] in _db(client, people)


def test_a_settled_run_says_how_it_went_at_the_bottom_of_the_main_line(
    client, monkeypatch
):
    room, _routine, run, _conversation, _submitted = _fired(client)
    from app.domain.agent.realtime.broker import get_broker

    broker = get_broker()
    original = broker.publish
    frames: list[tuple[str, dict]] = []

    async def publish(channel, frame, *args, **kwargs):
        frames.append((channel, frame))
        return await original(channel, frame, *args, **kwargs)

    monkeypatch.setattr(broker, "publish", publish)
    post_message(client, room, OWNER, {"content": "顺便说一句别的"})

    done = client.post(
        f"/routine-runs/{run['id']}/report",
        json={"status": "succeeded", "summary": "本周三个任务完成", "outputs": []},
    )
    assert done.status_code == 200, done.text
    _sweep(client)

    main = _main_line(client, room)
    message = _run_message(client, room, run["id"])
    assert message["content"] == "本周三个任务完成"
    assert message["routine_run"]["status"] == "succeeded"
    assert main[-1]["id"] == message["id"], "跑完的结果没有落到主线最下面"
    # Whoever has the channel open sees it change, whole, without reloading.
    told = [
        f["block"]
        for channel, f in frames
        if channel == room
        and f.get("type") == "block_updated"
        and f["block"]["id"] == message["id"]
    ]
    assert told, "开着频道的人没收到这条消息变了"
    assert told[-1]["content"] == "本周三个任务完成"
    assert told[-1]["routine_run"]["status"] == "succeeded"
    assert told[-1]["created_at"] == message["created_at"]


def test_a_run_that_failed_says_nothing_in_the_teammates_name(client):
    room, _routine, run, _conversation, _submitted = _fired(client)
    failed = client.post(
        f"/routine-runs/{run['id']}/report",
        json={"status": "failed", "summary": "资料库里没有上周的周报"},
    )
    assert failed.status_code == 200, failed.text
    _sweep(client)

    message = _run_message(client, room, run["id"])
    assert message["content"] == ""
    assert message["routine_run"]["status"] == "failed"
    assert "资料库里没有上周的周报" in message["routine_run"]["reason"]
