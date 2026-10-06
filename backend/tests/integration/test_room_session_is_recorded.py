"""一个房间跑过一轮，必须说得出自己跑过 —— 「现场」全靠这一个布尔值决定出不出。

`has_run` 问的是「这个房间有没有哪个 agent 留下过会话行」。房间里的任务各有自己的
会话，它们跑的轮次不算这个房间跑过。

同一行代码还带着第二个用途：`resume_token`。`--resume` 只在**冷启动**时用得上
（runner 那道守卫：只有 transcript 在的时候才 `--resume`，活着的会话本身就是
连续性），所以
写侧被跳过的后果不是「每轮都失忆」，而是**屏幕一旦被回收，这个房间就接不回自己那
段对话** —— 尽管 transcript 就躺在它自己的 session 目录里。这里用「第二轮冷启动
时拿到的 resume 指针」来钉它，因为那正是唯一用得上它的时刻。
"""

import uuid

from app.domain.agent.chat import ChatService
from tests.conftest import StubChannel, settle_turn, stub_compute, wait_work_idle
from tests.integration.conftest import open_task, post_project, session_auth_headers


class _Screen(StubChannel):
    """一块每轮都报同一个 session id 的屏幕，并记下每次启动被要求 resume 什么。"""

    def __init__(self, session_id: str = "s-1") -> None:
        super().__init__()
        self.new_session_id = session_id
        self._sid = session_id
        #: 每次启动拿到的 resume 指针，按顺序。冷启动是唯一用得上它的
        #: 时刻，所以这就是「这条会话接不接得回去」的全部证据。
        self.resume_asked: list[str | None] = []

    async def open(self, session, agent, launch):
        self.resume_asked.append(launch.resume_session_id)
        return await super().open(session, agent, launch)

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        self.starts(topic_id, session_id=self._sid)
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, reply)
        self.stops(topic_id, reply, session_id=self._sid)


def _room(client) -> tuple[str, str]:
    pid = post_project(client, json={"name": "P"}, owner="alice").json()["data"]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return pid, rid


def _started(client, room_id: str, title: str = "一件活") -> str:
    """房间里开始了的一条活。它在自己的会话里跑，等它那一轮跑完再看房间。"""
    task = open_task(client, room_id, title)
    wait_work_idle()
    return task["id"]


def _service(client, tmp_path, screen: _Screen) -> ChatService:
    return ChatService(
        session_factory=client.test_request_factory,
        compute=stub_compute(screen),
        base_system_prompt="你是芝士。",
        workspace_root=str(tmp_path / "ws"),
    )


def _turn(client, svc: ChatService, place_id: str, content: str = "干活") -> None:
    """跑完一轮，等到它真的收尾 —— `converse` 只等到 prompt 进了会话为止。"""
    pid = uuid.UUID(place_id)

    async def _go() -> None:
        async for _ in svc.converse(
            topic_id=pid, author="alice", content=content, summon=True
        ):
            pass
        await settle_turn(svc, pid)

    client.portal.call(_go)


def _has_run(client, project_id: str, place_id: str, bearer) -> bool:
    r = client.get(
        f"/projects/{project_id}/topics/{place_id}/work-summary",
        headers=bearer("alice"),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["has_run"]


# --- has_run：这一格该不该摆出来 ---------------------------------------------


def test_a_freshly_opened_room_has_not_run(client, bearer):
    """一步都还没走的房间说自己没跑过 —— 里面的任务开始了，跑的是任务自己的会话，
    不算房间跑过一轮。"""
    pid, room = _room(client)
    _started(client, room)

    assert _has_run(client, pid, room, bearer) is False


def test_a_task_is_asked_about_its_own_session(client, bearer):
    """任务是一段自己的对话：问它跑没跑过，答的是它自己的会话，不是房间的。"""
    pid, room = _room(client)
    card = _started(client, room)

    # Answers (200, a boolean) about the task's own session; it is no longer a
    # question put to the wrong object.
    assert isinstance(_has_run(client, pid, card, bearer), bool)
    assert _has_run(client, pid, room, bearer) is False


def test_a_room_records_its_own_turn(client, tmp_path, bearer):
    """跑完一轮，写侧要真的记下这个房间的会话。"""
    pid, room = _room(client)

    _turn(client, _service(client, tmp_path, _Screen()), room)

    assert _has_run(client, pid, room, bearer) is True


# --- resume：屏幕被回收之后，这段对话还接不接得回去 ---------------------------


def test_the_next_cold_start_resumes_the_conversation_it_had(client, tmp_path):
    """第二次冷启动必须被告知「接着上一段」。

    `--resume` 只在冷启动时用得上，所以这就是丢没丢过对话的判据：写侧被跳过时，
    第二轮拿到的是 None —— 一段全新的对话，尽管 transcript 就在那儿。
    """
    _pid, room = _room(client)
    screen = _Screen("s-room-1")
    svc = _service(client, tmp_path, screen)

    _turn(client, svc, room, "第一轮")
    _turn(client, svc, room, "第二轮")

    assert screen.resume_asked[0] is None, "第一轮无可接续，这是对的"
    assert screen.resume_asked[1:] == ["s-room-1"], screen.resume_asked
