"""清单什么时候被写，和清单里写了什么一样是给别人看的东西。

进度层（#187）存的是这份清单，但这些用例盯的是时间：会话还在干活、它这一段工作写的那一
版清单停在原地时，平台要叫它一声。2026-10-11 报上来的症状是清单「全空」到「全打勾」
没有中间——写的人只在自己开工和收尾两头写了它，中间那 40 分钟里，看清单的人一直看
着开工那一版。这里钉住的是平台这一侧的兜底：`QuietSessionReminders.checklist` 会叫在干活的
会话再写一次，走的是和人的中途消息、和沉默提醒同一条路（`notify_running_session`）。

三个条件缺一不可，所以三个都有反例：会话得还在干活、这一版清单得是**它手上这段工作
写的**（之前留下的那版，平台叫不动它）、而且它得真的停够久。
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.deps import get_chat_service, get_work_runner
from app.core.config import settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.turn.steps.reminders import QuietSessionReminders
from tests.integration.conftest import (
    in_thread,
    open_task,
    post_message,
    post_project,
    room_socket,
    session_auth_headers,
)

PLAN = [
    {"content": "核实 issue 论断", "status": "completed"},
    {"content": "写实现", "status": "in_progress"},
    {"content": "补测试", "status": "pending"},
]
THRESHOLD = 600


@pytest.fixture
def chat(client):
    return client.app.dependency_overrides[get_chat_service]()


@pytest.fixture
def notices(monkeypatch, chat) -> list[str]:
    """What the platform said into the running turn, instead of really saying it."""
    said: list[str] = []

    async def notice(topic_id, text, *args, **kwargs):
        said.append(text)
        return True

    monkeypatch.setattr(chat, "notify_running_session", notice)
    monkeypatch.setattr(settings, "chat_progress_reminder_after_s", THRESHOLD)
    return said


@pytest.fixture
def clock() -> list[datetime]:
    """The sweep's clock, in a one-cell list the test moves and hands in."""
    return [datetime.now(UTC)]


@pytest.fixture
def quiet(chat) -> QuietSessionReminders:
    """The platform's reminders, over the sessions this process holds."""
    return QuietSessionReminders(chat, chat.compute)


@pytest.fixture
def mid_flight(stub_hooks, monkeypatch):
    """A session that takes its input, says something, and stays running: the
    frame every "this turn is still going" test in this file needs.

    What must not happen is ``stops`` — that is what ends the turn.
    """

    def begin(topic_id, prompt, reply, agent=None):
        stub_hooks.starts(topic_id)
        stub_hooks.acknowledges(topic_id, prompt)
        stub_hooks.says(topic_id, "Internal output")

    monkeypatch.setattr(stub_hooks, "emit_turn", begin)
    return begin


def _room(client) -> tuple[str, dict]:
    """A 支线 in a channel — where 芝士 answers — and the credentials its
    session writes with."""
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题"},
        headers=session_auth_headers("user-1"),
    ).json()["data"]
    thread = in_thread(client, t["id"], "user-1")
    token = mint_scoped_token(project_id=p["id"], topic_id=thread)
    return thread, {"Authorization": f"Bearer {token}"}


def _write(client, topic_id: str, headers: dict, todos: list[dict], **extra):
    return client.put(
        f"/topics/{topic_id}/progress",
        json={"todos": todos, **extra},
        headers=headers,
    )


def _start(client, ws, topic_id: str, *, handle: str = "user-1") -> uuid.UUID:
    """Start a turn on ``topic_id`` and leave it running; return its work_id.

    Returns once the room has heard the session start its work (``turn_started``
    on ``ws``): that is when the platform knows which work the session is on,
    and so what ``PUT /progress`` stamps the checklist with. The session's own
    records are archived before its runner's report is applied, so seeing what
    it said is not that.
    """
    post_message(client, topic_id, handle, {"content": "@芝士 做一件事"})
    while ws.receive_json()["type"] != "turn_started":
        pass
    live = client.portal.call(
        chat_of(client).live.active_turn_ids.get, uuid.UUID(topic_id)
    )
    assert live, "这一轮应该还在跑"
    return next(iter(live))


def _stopped_working(chat, topic) -> None:
    """Until the session's runner has said it is idle: the room hears the
    result (``done``) a moment before the seat is marked idle."""
    deadline = time.monotonic() + 10
    while chat.compute.working_seat(uuid.UUID(str(topic))) is not None:
        assert time.monotonic() < deadline, "the session never went idle"
        time.sleep(0.01)


def chat_of(client):
    return client.app.dependency_overrides[get_chat_service]()


def _restamp(client, topic_id: str, turn_id: uuid.UUID) -> None:
    """Put a different turn's id on the stored row — a checklist this turn did
    not write, which is what an earlier turn leaves behind.

    Written through `PUT /progress` (so the row is the one the route writes)
    and then re-stamped directly: nothing in the product can produce this state
    with a live turn, because the writer always stamps the live one.
    """

    async def restamp() -> None:
        from sqlalchemy import update

        from app.domain.topic.models import TopicProgress

        service = chat_of(client)
        async with service.session_factory() as session:
            await session.execute(
                update(TopicProgress)
                .where(TopicProgress.conversation_id == uuid.UUID(topic_id))
                .values(turn_id=turn_id)
            )
            await session.commit()

    client.portal.call(restamp)


def _its_own_checklist(
    client, topic_id: str, headers: dict, clock: list[datetime]
) -> uuid.UUID:
    """Write the checklist the way the turn itself does, and check that the
    write was stamped with that turn.

    ``PUT /progress`` stamps the row with the turn the runner says is live in
    this conversation — the same lookup the agent's own tool call goes through.
    A write that lands unstamped is the previous shape of this problem, and the
    sweep correctly skips it, so the tests that expect a reminder must not
    silently start from one.

    The sweeps' clock is pinned here, after the write: the row's ``updated_at``
    comes from the real clock at that moment, so a "now" taken a few
    milliseconds later makes the sweep measure exactly the seconds the test
    then adds. Pinned any earlier and those milliseconds count against the
    threshold, which is the difference between a boundary and a coin toss.
    """
    assert _write(client, topic_id, headers, PLAN).status_code == 200
    clock[0] = datetime.now(UTC)
    work = client.portal.call(
        get_work_runner().live_work_for_topic, uuid.UUID(topic_id)
    )
    assert work is not None, "写作端得认得出正在跑的那一轮"
    return uuid.UUID(work["turn_id"])


def test_a_running_turns_stalled_checklist_is_asked_for_again(
    client, chat, notices, clock, quiet, mid_flight
):
    """一轮在跑、清单十分钟没动，就叫它再写一次。

    叫法本身和沉默提醒一样：一句话进那一轮。区别是这里数的是清单自己的时刻
    （`TopicProgress.updated_at`），不是这一轮多久没说话——说得很勤、清单却停着，
    正是报上来的那一种。
    """
    topic, headers = _room(client)
    with room_socket(client, topic, "user-1") as ws:
        _start(client, ws, topic)
        _its_own_checklist(client, topic, headers, clock)
        # 刚写完，一动不动，数出来是正的几毫秒——远在阈值左边。
        assert client.portal.call(quiet.checklist, clock[0]) == 0
        # 还没到窗口：不叫。
        clock[0] += timedelta(seconds=THRESHOLD - 60)
        assert client.portal.call(quiet.checklist, clock[0]) == 0
        assert notices == []
        # 到点，叫一次。
        clock[0] += timedelta(seconds=60)
        assert client.portal.call(quiet.checklist, clock[0]) == 1
        assert len(notices) == 1
        assert "todo_write" in notices[0]
        assert "10 分钟" in notices[0]
        # 叫过就等下一次窗口，别在同一次停歇里反复说。
        clock[0] += timedelta(seconds=THRESHOLD - 1)
        assert client.portal.call(quiet.checklist, clock[0]) == 0
        clock[0] += timedelta(seconds=1)
        assert client.portal.call(quiet.checklist, clock[0]) == 1
        assert len(notices) == 2
        # 那一轮听话写了下一步：清单又动了，时刻从写下的那一刻重新算。
        _write(
            client,
            topic,
            headers,
            [
                {**PLAN[0], "status": "completed"},
                {**PLAN[1], "status": "completed"},
                {**PLAN[2], "status": "in_progress"},
            ],
        )
        # 时刻往前推一个窗口（上面那次提醒的时刻也记着，两样一起看才不会提前
        # 开口）。
        clock[0] += timedelta(seconds=THRESHOLD - 1)
        assert client.portal.call(quiet.checklist, clock[0]) == 0
        assert len(notices) == 2
        # 再停够一个窗口，就再叫一次——不是「叫过这一轮就再也不叫」。
        clock[0] += timedelta(seconds=1)
        assert client.portal.call(quiet.checklist, clock[0]) == 1
        assert len(notices) == 3


def test_a_checklist_left_by_an_earlier_turn_is_not_this_turns_problem(
    client, chat, notices, clock, quiet, mid_flight
):
    """上一轮留下的清单：平台叫不动它，也不该叫这一轮。

    `TopicProgress.turn_id` 记着它是哪一轮写的。新一轮开工时还没写自己的那一版，
    存着的仍是上一轮的——这时候提醒一个正在干别的的轮次去改一份它没写过的清单，
    只会让它对着别人的计划打勾。
    """
    topic, headers = _room(client)
    with room_socket(client, topic, "user-1") as ws:
        _start(client, ws, topic)
        # 这一轮还没写自己那一版，存着的是上一轮的：`turn_id` 对不上，越久越该不管。
        assert _write(client, topic, headers, PLAN).status_code == 200
        _restamp(client, topic, uuid.uuid4())
        clock[0] += timedelta(seconds=3600)
        assert client.portal.call(quiet.checklist, clock[0]) == 0
        assert notices == []


def test_a_finished_turn_is_not_reminded(
    client, stub_hooks, chat, notices, clock, quiet, mid_flight
):
    """这一轮已经收工，就不用再叫了——清单留在那儿，下一轮接着写它自己的。"""
    topic, headers = _room(client)
    with room_socket(client, topic, "user-1") as ws:
        _start(client, ws, topic)
        _its_own_checklist(client, topic, headers, clock)
        stub_hooks.stops(uuid.UUID(topic), "Done")
        while ws.receive_json()["type"] != "done":
            pass
        _stopped_working(chat, topic)
        clock[0] += timedelta(seconds=3600)
        assert client.portal.call(quiet.checklist, clock[0]) == 0
        assert notices == []


def test_a_tasks_own_checklist_is_reminded_the_same_way(
    client, chat, notices, clock, quiet, mid_flight
):
    """任务里也一样：清单记在任务上、按任务的频道推，但停在原地时同样要被叫。

    任务的清单不发成房间里的消息（写作端按 `place.task_id` 分），所以这一条也钉住
    兜底不是在房间消息上做的。
    """
    room, _ = _room(client)
    task = open_task(client, room, owner="user-1")
    task_id = str(task["id"])
    token = mint_scoped_token(project_id=task["project_id"], topic_id=task_id)
    headers = {"Authorization": f"Bearer {token}"}
    with room_socket(client, task_id, "user-1") as ws:
        _start(client, ws, task_id)
        _its_own_checklist(client, task_id, headers, clock)
        clock[0] += timedelta(seconds=THRESHOLD)
        assert client.portal.call(quiet.checklist, clock[0]) == 1
        assert len(notices) == 1
        assert "todo_write" in notices[0]
