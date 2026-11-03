"""A person's 芝士 at work on its own (`routine.personal`), over the real HTTP
stack, database and Valkey, answered by the pinned pi on a session host.

The rules pinned here: a routine 芝士 makes when the person asks runs on its
own when its time comes, in a conversation of its own, and the person is told;
in a run, the gate reads only what the person said when they asked for the
routine, and what it would ask about is left as a card without waiting; a run's
cards are good until the routine runs again; what happens to the person fires
the routines watching for it, once each; out of credits or away for long,
routines pause.
"""

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

from redis.asyncio import from_url
from sqlalchemy import select, update

from app.api.deps import get_consumptions, get_session_host
from app.core.config import settings
from app.core.sandbox_auth import mint_delegated_credential
from app.domain import happenings
from app.domain.addressing import Event, Hand, address
from app.domain.agent.session_host.answer import Answer
from app.domain.assistant import gate
from app.domain.assistant.models import (
    AssistantConversation,
    AssistantMessage,
    AssistantProposal,
    AssistantRoutine,
    AssistantRun,
)
from app.domain.delivery.ledger import deliver
from app.domain.delivery_events import DeliveryEvent
from app.domain.gateway_chat import Completion, Usage
from app.domain.notification.models import Notification, NotificationType
from app.domain.project.models import Project
from app.domain.routine import personal
from app.domain.topic.models import Topic
from app.domain.user.models import UserSession
from app.domain.user.services import usernames_by_ids
from app.main import app
from tests.conftest import seed_user
from tests.delivery import delivery_task_id
from tests.integration.conftest import add_external_member, new_project
from tests.integration.test_assistant import _auth, _ledger_user
from tests.integration.test_assistant import gateway as gateway  # the fixture
from tests.integration.test_card_filed_notice import _decide as decide
from tests.integration.test_card_filed_notice import _file_card as file_card
from tests.integration.test_card_filed_notice import _room as filed_room
from tests.integration.test_card_filed_notice import _set_reporter as set_reporter
from tests.integration.test_message_edit import _room


def _ask(client, conversation: str, question: str, headers: dict):
    return client.post(
        f"/assistant/conversations/{conversation}/ask",
        json={"question": question},
        headers=headers,
    )


def _start(client, headers: dict) -> str:
    r = client.post("/assistant/conversations", headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["data"]["id"]


def _db(client, work):
    async def run():
        async with client.test_factory() as db:
            out = await work(db)
            await db.commit()
            return out

    return asyncio.run(run())


def _routines(client, user_id: int) -> list[AssistantRoutine]:
    return _db(
        client,
        lambda db: personal.mine(db, user_id),
    )


def _runs(client, user_id: int) -> list[AssistantRun]:
    async def read(db):
        return list(
            await db.scalars(
                select(AssistantRun)
                .where(AssistantRun.user_id == user_id)
                .order_by(AssistantRun.created_at)
            )
        )

    return _db(client, read)


def _notices(client, user_id: int) -> list[dict]:
    async def read(db):
        rows = await db.scalars(
            select(Notification)
            .where(
                Notification.receiver_id == user_id,
                Notification.type == NotificationType.ASSISTANT_NOTICE.value,
            )
            .order_by(Notification.id)
        )
        return [dict(r.metadata_payload or {}) for r in rows]

    return _db(client, read)


def _due_now(client, routine_id: uuid.UUID) -> None:
    async def move(db):
        await db.execute(
            update(AssistantRoutine)
            .where(AssistantRoutine.id == routine_id)
            .values(next_run_at=datetime.now(UTC) - timedelta(minutes=1))
        )

    _db(client, move)


def _tick(client, gw) -> dict:
    """One pass of the routines' clock, on the app's own loop."""
    people = app.dependency_overrides[get_session_host]()
    questions = app.dependency_overrides[get_consumptions]()
    return client.portal.call(
        personal.tick,
        gw.sessions,
        people,
        questions,
        lambda: from_url(settings.redis_url),
    )


def _ended(client, user_id: int, count: int = 1) -> list[AssistantRun]:
    """The person's runs once ``count`` of them have ended."""
    deadline = time.monotonic() + 60
    while True:
        runs = _runs(client, user_id)
        if sum(r.status in ("done", "failed") for r in runs) >= count:
            return runs
        assert time.monotonic() < deadline, [(r.status, r.error) for r in runs]
        time.sleep(0.2)


def _make_routine(client, gw, me: dict, said: str, **args) -> AssistantRoutine:
    """The person asks for a routine; 芝士 makes it with ``args``."""
    conversation = _start(client, me)
    gw.script = [
        ("tool", "cheese_routine", {"action": "create", **args}),
        ("text", "已建好。"),
    ]
    r = _ask(client, conversation, said, me)
    assert r.status_code == 200, r.text
    assert "已建好" in r.text
    user_id = _ledger_user(client, "asker")[0]
    [routine] = _routines(client, user_id)
    return routine


def test_the_rule_cheese_makes_stays_in_its_answer_as_a_card(client, gateway):
    """The rule shows where it was asked for, and still does on reading the
    conversation again: what it waits for, what it will do, which rule."""
    me = _auth(client, "asker")
    routine = _make_routine(
        client,
        gateway,
        me,
        "每天早上八点把我的待办发给我",
        title="每日待办",
        note="列出待办",
        trigger="schedule",
        freq="daily",
        time="08:00",
    )

    r = client.get(f"/assistant/conversations/{routine.conversation_id}", headers=me)
    [answer] = [m for m in r.json()["data"]["messages"] if m["role"] == "assistant"]
    [card] = answer["parts"]["rules"]
    assert card["id"] == str(routine.id)
    assert card["when"].startswith("每天 08:00")
    assert card["note"] == "列出待办"


class _Keeper:
    """The gate's model: stops everything, and keeps what it was shown."""

    def __init__(self) -> None:
        self.shown: list[str] = []

    async def complete(self, *, system, prompt, timeout, max_tokens=None, extra=None):
        self.shown.append(prompt)
        said = "拦" if max_tokens and max_tokens < 50 else "你没有说要回复。\n结论：拦"
        return Completion(said, Usage(10, 1), 0.0)


def test_a_routine_runs_on_its_own_when_its_time_comes_and_tells_the_person(
    client, gateway
):
    me = _auth(client, "asker")
    user_id = _ledger_user(client, "asker")[0]
    routine = _make_routine(
        client,
        gateway,
        me,
        "每天早上八点把我的待办发给我",
        title="每日待办",
        note="列出用户的待办",
        trigger="schedule",
        freq="daily",
        time="08:00",
    )
    # What the person said is what the routine runs on; its name is 芝士's.
    assert routine.words == "每天早上八点把我的待办发给我"
    assert routine.next_run_at is not None and routine.next_run_at > datetime.now(UTC)
    listed = client.get("/assistant/routines", headers=me).json()["data"]
    assert [(r["title"], r["state"]) for r in listed["routines"]] == [
        ("每日待办", "active")
    ]

    _due_now(client, routine.id)
    gateway.script = [
        ("tool", "cheese_inbox", {"action": "list"}),
        ("text", "今天没有待办。"),
    ]
    _tick(client, gateway)
    [run] = _ended(client, user_id)

    assert run.status == "done", run.error
    assert run.cost_usd > 0
    # The run is a conversation of its own, started by the platform.
    conversations = client.get("/assistant/conversations", headers=me).json()["data"]
    mine = next(c for c in conversations["conversations"] if c["routine"])
    assert mine["id"] == str(run.conversation_id) and mine["title"] == "每日待办"
    said = client.get(f"/assistant/conversations/{mine['id']}", headers=me).json()[
        "data"
    ]["messages"]
    assert [m["role"] for m in said] == ["platform", "assistant"]
    assert "自动化「每日待办」现在运行" in said[0]["text"]
    assert said[1]["text"] == "今天没有待办。"
    # The person is told, and the notice opens the run's conversation.
    [notice] = _notices(client, user_id)
    assert notice["content"] == "自动化「每日待办」已完成"
    assert notice["assistantConversation"] == str(run.conversation_id)
    assert notice["quote"] == "今天没有待办。"
    # The next time is tomorrow's.
    [again] = _routines(client, user_id)
    assert again.next_run_at is not None and again.next_run_at > datetime.now(UTC)


def test_in_a_run_what_was_not_asked_for_is_left_as_a_card_without_waiting(
    client, gateway, monkeypatch
):
    """Nobody is there to confirm: the card is left and the run goes on. The
    gate reads what the person said when they made the routine, never what
    fired it. The card is good until the routine runs again."""
    keeper = _Keeper()

    async def looks(session):
        return keeper

    monkeypatch.setattr(gate, "gatekeeper", looks)
    me = _auth(client, "asker")
    user_id = _ledger_user(client, "asker")[0]
    room = _channel_of(client, "asker")
    routine = _make_routine(
        client,
        gateway,
        me,
        "每天早上看一下我的项目频道有什么新消息",
        title="看频道",
        note="看项目频道的新消息",
        trigger="schedule",
        freq="daily",
        time="08:00",
    )

    _due_now(client, routine.id)
    gateway.script = [
        ("tool", "chat_send", {"where": room, "content": "收到，我会处理"}),
        ("text", "我起草了一条回复，等你确认。"),
    ]
    started = time.monotonic()
    _tick(client, gateway)
    [run] = _ended(client, user_id)

    # It did not wait out the ten minutes a card waits for a person present.
    assert time.monotonic() - started < 60
    assert (run.status, run.asked) == ("done", 1)
    shown = keeper.shown[0]
    assert "[用户（建这条自动化时说）] 每天早上看一下我的项目频道有什么新消息" in shown
    assert "自动化「看频道」现在运行" not in shown
    [notice] = _notices(client, user_id)
    assert notice["content"] == "自动化「看频道」有 1 项操作待你确认"
    [card] = _cards(client, user_id)
    assert card.state == "pending" and card.work == str(run.id)

    # It runs again: the card the last run left is no longer good.
    _due_now(client, routine.id)
    gateway.script = [("text", "没有新消息。")]
    _tick(client, gateway)
    _ended(client, user_id, 2)
    [card] = _cards(client, user_id)
    assert card.state == "expired"
    refused = client.post(
        f"/assistant/proposals/{card.id}", json={"state": "sent"}, headers=me
    )
    assert refused.status_code == 400


def _cards(client, user_id: int) -> list[AssistantProposal]:
    async def read(db):
        return list(
            await db.scalars(
                select(AssistantProposal)
                .where(AssistantProposal.user_id == user_id)
                .order_by(AssistantProposal.id)
            )
        )

    return _db(client, read)


def _channel_of(client, handle: str) -> str:
    """A channel of a project ``handle`` is in, by its ref."""
    room, _ = _room(client)

    async def read(db):
        topic = await db.get(Topic, uuid.UUID(room))
        project = await db.get(Project, topic.project_id)
        return str(project.id), project.slug, topic.number

    project_id, slug, number = _db(client, read)
    add_external_member(client, project_id, handle, by="alice")
    return f"{slug}/channels/{number}"


# --- what fires a routine, without a session host --------------------------


class _Closed:
    """A session host that is not there: what is due is queued, not run."""

    def available(self) -> bool:
        return False


def _sweep(client) -> dict:
    return asyncio.run(
        personal.tick(client.test_factory, _Closed(), None, lambda: None)  # type: ignore[arg-type]
    )


def _routine(client, handle: str, **fields) -> AssistantRoutine:
    seed_user(client, handle)
    user_id = _ledger_user(client, handle)[0]

    async def make(db):
        conversation = AssistantConversation(
            user_id=user_id, place_kind="personal", place_id=str(user_id), title="x"
        )
        db.add(conversation)
        await db.flush()
        row = AssistantRoutine(
            user_id=user_id,
            title=fields.pop("title", "有人找我"),
            note="",
            words=fields.pop("words", "有人 @ 我就告诉我"),
            trigger=fields.pop("trigger", "mention"),
            spec=fields.pop("spec", {}),
            timezone="Asia/Shanghai",
            conversation_id=conversation.id,
            **fields,
        )
        db.add(row)
        await db.flush()
        return row

    return _db(client, make)


def _notified(
    client,
    user_id: int,
    type_: NotificationType,
    *,
    project: uuid.UUID | None = None,
    payload: dict | None = None,
) -> None:
    """The person is sent a notification, the way every one is sent."""

    async def send(db):
        handle = (await usernames_by_ids(db, [user_id]))[user_id]
        await deliver(
            db,
            DeliveryEvent(
                id=uuid.uuid4(),
                type=type_,
                payload={
                    **(payload or {"content": "周五前能交吗？", "author": "bob"}),
                    **({"projectId": str(project)} if project else {}),
                },
                occurred_at=datetime.now(UTC),
            ),
            address(Event(audience=(handle,)), Hand.participant),
        )

    _db(client, send)


def test_each_thing_that_happens_fires_the_routines_watching_for_it_once(client):
    routine = _routine(client, "dora")
    user_id = routine.user_id
    _notified(client, user_id, NotificationType.MENTION)
    _notified(client, user_id, NotificationType.REACTION)  # not waited for

    _sweep(client)
    _sweep(client)

    [run] = _runs(client, user_id)
    assert (run.routine_id, run.status) == (routine.id, "queued")
    assert "周五前能交吗？" in run.detail


def test_a_routine_limited_to_a_project_hears_only_what_happens_there(client):
    here = uuid.UUID(new_project(client, name="组会", owner="bob")["id"])
    there = uuid.UUID(new_project(client, name="课程", owner="bob")["id"])
    routine = _routine(
        client, "dora", spec={"project": str(here), "projectName": "组会"}
    )
    _notified(client, routine.user_id, NotificationType.MENTION, project=there)
    _notified(client, routine.user_id, NotificationType.MENTION, project=here)

    _sweep(client)

    assert len(_runs(client, routine.user_id)) == 1


def test_a_routine_fired_too_often_in_an_hour_skips_the_rest(client):
    routine = _routine(client, "dora")
    for _ in range(personal.EVENT_RUNS_PER_HOUR + 2):
        _notified(client, routine.user_id, NotificationType.MENTION)

    _sweep(client)

    runs = _runs(client, routine.user_id)
    assert [r.status for r in runs].count("queued") == personal.EVENT_RUNS_PER_HOUR
    assert [r.status for r in runs].count("skipped") == 2


def test_a_scheduled_routine_still_running_skips_its_next_time(client):
    routine = _routine(
        client,
        "dora",
        trigger="schedule",
        spec={"freq": "hourly", "minute": 0},
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    _sweep(client)
    _due_now(client, routine.id)
    _sweep(client)

    assert [r.status for r in _runs(client, routine.user_id)] == ["queued", "skipped"]


def test_a_happening_that_cannot_be_read_does_not_hold_up_the_ones_behind(client):
    routine = _routine(client, "dora", spec={"from": ["bob"], "fromNames": ["鲍勃"]})

    async def garbled(db):
        await happenings.happened(
            db,
            happenings.NOTIFIED,
            user_id=routine.user_id,
            payload={"type": NotificationType.MENTION.value, "payload": "garbled"},
        )

    _db(client, garbled)
    _notified(client, routine.user_id, NotificationType.MENTION)

    _sweep(client)
    _sweep(client)

    [run] = _runs(client, routine.user_id)
    assert "周五前能交吗？" in run.detail


def test_a_mention_rule_limited_to_some_people_hears_only_them(client):
    routine = _routine(client, "dora", spec={"from": ["bob"], "fromNames": ["鲍勃"]})
    _notified(client, routine.user_id, NotificationType.MENTION)
    _notified(
        client,
        routine.user_id,
        NotificationType.MENTION,
        payload={"content": "看一下", "author": "carol"},
    )

    _sweep(client)

    [run] = _runs(client, routine.user_id)
    assert "周五前能交吗？" in run.detail


def test_a_paused_rule_misses_what_happened_meanwhile(client):
    routine = _routine(client, "dora", state="paused", paused="person")
    _notified(client, routine.user_id, NotificationType.MENTION)
    _sweep(client)
    me = _auth(client, "dora")
    client.post(f"/assistant/routines/{routine.id}/resume", headers=me)

    _sweep(client)

    assert _runs(client, routine.user_id) == []


# --- what happens in a task ------------------------------------------------


def _filing_room(client, reporter: str) -> str:
    """A room whose task ``reporter`` asked for, reviewed by alice."""
    seed_user(client, "alice")
    room = filed_room(client)
    set_reporter(client, room, reporter)
    return room


def _waiting(client, handle: str, trigger: str, **spec) -> AssistantRoutine:
    return _routine(client, handle, title="等审阅", trigger=trigger, spec=spec)


def test_a_rule_waiting_once_for_a_review_runs_in_its_own_conversation(client):
    """「等这个任务提交审阅了帮我看看」: the first filing fires it, in the
    conversation it was asked for in, and nothing after does."""
    room = _filing_room(client, "dora")
    task = delivery_task_id(client, room)
    routine = _waiting(
        client, "dora", "filed", task=str(task), taskTitle="接入搜索", once=True
    )

    card = file_card(client, room).json()["data"]["id"]
    _sweep(client)
    decide(client, card, "reject", note="再改改")
    file_card(client, room)
    _sweep(client)

    [run] = _runs(client, routine.user_id)
    assert run.conversation_id == routine.conversation_id
    assert "提交审阅" in run.detail
    [row] = _routines(client, routine.user_id)
    assert row.state == "done"


def test_a_rule_on_the_persons_tasks_fires_for_theirs_alone(client):
    mine = _filing_room(client, "dora")
    theirs = _filing_room(client, "erin")
    routine = _waiting(client, "dora", "filed", mine=True)

    file_card(client, theirs)
    file_card(client, mine)
    _sweep(client)

    [run] = _runs(client, routine.user_id)
    assert run.conversation_id != routine.conversation_id


def test_a_rule_waiting_for_a_return_ignores_the_filing_and_the_acceptance(client):
    room = _filing_room(client, "dora")
    routine = _waiting(client, "dora", "decided", mine=True, outcome="rejected")

    card = file_card(client, room).json()["data"]["id"]
    _sweep(client)
    assert _runs(client, routine.user_id) == []
    assert decide(client, card, "reject", note="口径不对").status_code == 200
    _sweep(client)

    [run] = _runs(client, routine.user_id)
    assert "退回" in run.detail


def test_a_review_rule_fires_when_a_task_goes_to_review_with_the_person(client):
    room = _filing_room(client, "dora")
    routine = _waiting(client, "alice", "review")

    file_card(client, room, reviewer="alice")
    _sweep(client)

    [run] = _runs(client, routine.user_id)
    assert "提交审阅" in run.detail


def test_a_rule_waiting_once_that_nothing_fires_is_given_up_and_said(client):
    routine = _waiting(client, "dora", "filed", mine=True, once=True)

    async def long_ago(db):
        await db.execute(
            update(AssistantRoutine)
            .where(AssistantRoutine.id == routine.id)
            .values(updated_at=datetime.now(UTC) - personal.WAITS_FOR)
        )

    _db(client, long_ago)
    _sweep(client)

    [row] = _routines(client, routine.user_id)
    assert row.state == "done"
    [notice] = _notices(client, routine.user_id)
    assert notice["content"] == "已停止等待「等审阅」：14 天内没有发生"
    assert notice["assistantConversation"] == str(routine.conversation_id)


def test_out_of_credits_a_persons_routines_pause_and_they_are_told_once(client):
    routine = _routine(
        client,
        "dora",
        trigger="schedule",
        spec={"freq": "hourly", "minute": 0},
        next_run_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    other = _routine(client, "dora", title="第二条")

    async def out(db):
        run = AssistantRun(
            user_id=routine.user_id,
            routine_id=routine.id,
            occurrence="x",
            status="queued",
        )
        db.add(run)
        await db.flush()
        await personal._out_of_credits(db, run, datetime.now(UTC))
        return run

    run = _db(client, out)

    assert run.status == "skipped"
    states = {r.title: (r.state, r.paused) for r in _routines(client, routine.user_id)}
    assert states == {
        "有人找我": ("paused", "credits"),
        "第二条": ("paused", "credits"),
    }
    [notice] = _notices(client, routine.user_id)
    assert notice["content"] == "额度已用完，自动化已暂停"
    assert other.id


def test_routines_of_someone_away_for_long_pause_until_they_resume_them(client):
    routine = _routine(client, "dora")
    me = _auth(client, "dora")

    async def away(db):
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == routine.user_id)
            .values(last_used_at=datetime.now(UTC) - timedelta(days=15))
        )
        await db.execute(
            update(AssistantRoutine)
            .where(AssistantRoutine.id == routine.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=30))
        )

    _db(client, away)
    _sweep(client)

    [row] = _routines(client, routine.user_id)
    assert (row.state, row.paused) == ("paused", "away")
    [notice] = _notices(client, routine.user_id)
    assert notice["content"] == "你已 14 天未登录，自动化已暂停"
    resumed = client.post(f"/assistant/routines/{routine.id}/resume", headers=me)
    assert resumed.json()["data"]["routines"][0]["state"] == "active"


# --- 芝士's tools, as the routes it calls --------------------------------------


def _acting(client, handle: str, conversation: str | None, work: str | None = None):
    seed_user(client, handle)
    return {
        "Authorization": f"Bearer {
            mint_delegated_credential(
                user_id=_ledger_user(client, handle)[0],
                handle=handle,
                work=work or str(uuid.uuid4()),
                ttl_s=300,
                read_only=False,
                conversation=conversation,
            )
        }"
    }


def _conversation(client, handle: str, said: str) -> str:
    seed_user(client, handle)
    user_id = _ledger_user(client, handle)[0]

    async def make(db):
        row = AssistantConversation(
            user_id=user_id, place_kind="personal", place_id=str(user_id), title=said
        )
        db.add(row)
        await db.flush()
        db.add(AssistantMessage(conversation_id=row.id, seq=1, role="user", text=said))
        return str(row.id)

    return _db(client, make)


def test_cheese_makes_lists_and_pauses_the_persons_routines(client):
    conversation = _conversation(client, "dora", "每周一早上九点提醒我交周报")
    acting = _acting(client, "dora", conversation)

    made = client.post(
        "/assistant/agent/routine",
        json={
            "action": "create",
            "title": "交周报",
            "note": "提醒用户交周报",
            "trigger": "schedule",
            "freq": "weekly",
            "weekdays": [0],
            "time": "09:00",
        },
        headers=acting,
    )
    assert made.status_code == 200, made.text
    assert "「交周报」" in made.json()["data"]["text"]
    listed = client.post(
        "/assistant/agent/routine", json={"action": "list"}, headers=acting
    ).json()["data"]["text"]
    routine_id = listed.split("编号 ")[1].split("）")[0]
    paused = client.post(
        "/assistant/agent/routine",
        json={"action": "pause", "routine": routine_id},
        headers=acting,
    )
    assert "（已暂停）" in paused.json()["data"]["text"]
    me = _auth(client, "dora")
    [row] = client.get("/assistant/routines", headers=me).json()["data"]["routines"]
    assert (row["title"], row["state"], row["paused"]) == ("交周报", "paused", "person")
    # Someone else's id answers as one that does not exist.
    other = _acting(client, "erin", _conversation(client, "erin", "你好"))
    assert (
        client.post(
            "/assistant/agent/routine",
            json={"action": "delete", "routine": routine_id},
            headers=other,
        ).status_code
        == 404
    )


def test_a_person_keeps_at_most_so_many_routines(client):
    conversation = _conversation(client, "dora", "有人 @ 我就告诉我")
    acting = _acting(client, "dora", conversation)
    for i in range(personal.MAX_ROUTINES + 1):
        r = client.post(
            "/assistant/agent/routine",
            json={"action": "create", "title": f"第 {i} 条", "trigger": "mention"},
            headers=acting,
        )
    assert r.status_code == 422 or r.status_code == 400, r.text
    assert str(personal.MAX_ROUTINES) in r.text


def test_cheese_sends_the_person_a_notification_that_opens_the_conversation(client):
    conversation = _conversation(client, "dora", "半小时后提醒我交报告")
    acting = _acting(client, "dora", conversation)

    sent = client.post(
        "/assistant/agent/notify-me", json={"text": "该交报告了"}, headers=acting
    )

    assert sent.status_code == 200, sent.text
    user_id = _ledger_user(client, "dora")[0]
    [notice] = _notices(client, user_id)
    assert notice["content"] == "该交报告了"
    assert notice["assistantConversation"] == conversation


def test_the_persons_entries_are_read_by_the_gate_one_a_line(client, monkeypatch):
    """Entries the person adds are kept one a line, said once, and the gate
    reads them as a list."""
    keeper = _Keeper()

    async def looks(session):
        return keeper

    monkeypatch.setattr(gate, "gatekeeper", looks)
    room = _channel_of(client, "dora")
    me = _auth(client, "dora")
    saved = client.put(
        "/assistant/rules",
        json={
            "ask": ["remove_member"],
            "askItems": ["发给老师的消息", "发给老师的消息", " "],
            "allowItems": ["在我负责的频道里回答问题"],
        },
        headers=me,
    ).json()["data"]
    assert saved["askItems"] == ["发给老师的消息"]
    acting = _acting(client, "dora", _conversation(client, "dora", "帮我看看频道"))

    client.post(
        "/assistant/agent/review",
        json={"kind": "message", "where": room, "text": "收到"},
        headers=acting,
    )

    assert "需要确认：\n- 发给老师的消息" in keeper.shown[0]
    assert "无需确认：\n- 在我负责的频道里回答问题" in keeper.shown[0]


def test_a_one_off_routine_that_ran_is_done_frees_its_place_and_is_cleared_later(
    client,
):
    """A routine that runs once is done after its run: it cannot be paused,
    does not count toward the limit, and is gone with its runs once the cards
    it could have left have expired."""
    at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    routine = _routine(
        client,
        "dora",
        title="提醒交报告",
        trigger="schedule",
        spec={"freq": "once", "at": at},
        next_run_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    me = _auth(client, "dora")

    async def run_it(db):
        run = AssistantRun(
            user_id=routine.user_id,
            routine_id=routine.id,
            occurrence=f"{routine.id}:once",
            status="running",
        )
        db.add(run)
        await db.flush()
        return run.id

    run_id = _db(client, run_it)
    asyncio.run(
        personal.ended(client.test_factory, run_id, Answer("已提醒。"), False, False)
    )

    [listed] = client.get("/assistant/routines", headers=me).json()["data"]["routines"]
    assert (listed["state"], listed["nextRunAt"]) == ("done", None)
    assert listed["once"] is True
    paused = client.post(f"/assistant/routines/{routine.id}/pause", headers=me)
    assert paused.status_code in (400, 422), paused.text

    conversation = _conversation(client, "dora", "有人 @ 我就告诉我")
    acting = _acting(client, "dora", conversation)
    for i in range(personal.MAX_ROUTINES):
        r = client.post(
            "/assistant/agent/routine",
            json={"action": "create", "title": f"第 {i} 条", "trigger": "mention"},
            headers=acting,
        )
        assert r.status_code == 200, r.text

    async def long_ago(db):
        await db.execute(
            update(AssistantRoutine)
            .where(AssistantRoutine.id == routine.id)
            .values(
                updated_at=datetime.now(UTC) - personal.KEPT_FOR - timedelta(hours=1)
            )
        )

    _db(client, long_ago)
    _sweep(client)
    left = _routines(client, routine.user_id)
    assert routine.id not in {r.id for r in left}
    assert len(left) == personal.MAX_ROUTINES
    assert run_id not in {r.id for r in _runs(client, routine.user_id)}
