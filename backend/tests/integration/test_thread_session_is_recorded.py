"""一个房间跑过一轮，必须说得出自己跑过 —— 「现场」全靠这一个布尔值决定出不出。

`has_run` 问的是「这个地点有没有哪个 agent 留下过会话行」，而地点只有房间：它派
出去的每一件活都是这一个会话里的一个分身，不另开会话，也没有自己的 `--resume`。

同一行代码还带着第二个用途：`resume_token`。`--resume` 只在**冷启动**时用得上
（`build_session_launch` 那道守卫：tmux 会话本身就是一个普通话题的连续性），所以
写侧被跳过的后果不是「每轮都失忆」，而是**屏幕一旦被回收，这个房间就接不回自己那
段对话** —— 尽管 transcript 就躺在它自己的 session 目录里。这里用「第二轮冷启动
时拿到的 resume 指针」来钉它，因为那正是唯一用得上它的时刻。
"""

import asyncio
import uuid

from app.domain.agent.chat import ChatService
from app.domain.room_task.services import TaskService
from tests.conftest import StubChannel, settle_turn, stub_compute, wait_work_idle


class _Screen(StubChannel):
    """一块每轮都报同一个 session id 的屏幕，并记下每次启动被要求 resume 什么。"""

    def __init__(self, session_id: str = "s-1") -> None:
        super().__init__()
        self._sid = session_id
        #: 每次 `ensure_ready` 拿到的 resume 指针，按顺序。冷启动是唯一用得上它的
        #: 时刻，所以这就是「这条会话接不接得回去」的全部证据。
        self.resume_asked: list[str | None] = []

    async def ensure_ready(self, **kwargs: object) -> uuid.UUID:  # type: ignore[override]
        launch = kwargs["launch"]
        self.resume_asked.append(launch.resume_session_id)  # type: ignore[attr-defined]
        return await super().ensure_ready(**kwargs)  # type: ignore[arg-type]

    def emit_turn(self, topic_id: uuid.UUID, prompt: str, reply: str) -> None:
        self.starts(topic_id, session_id=self._sid)
        self.acknowledges(topic_id, prompt)
        self.says(topic_id, reply)
        self.stops(topic_id, reply, session_id=self._sid)


def _room(client) -> tuple[str, str]:
    pid = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()[
        "data"
    ]["id"]
    rid = client.post(
        "/topics",
        json={"project_id": pid, "title": "房间", "created_by": "alice"},
    ).json()["data"]["id"]
    return pid, rid


def _dispatched(client, room_id: str, title: str = "一件活") -> str:
    """派出去的一条活。派活不跑任何东西，所以它出来时是安静的。"""
    r = client.post(f"/topics/{room_id}/split", json={"title": title})
    assert r.status_code == 200, r.text
    wait_work_idle()
    return r.json()["data"]["id"]


def _worked(client, tmp_path, room_id: str, title: str = "一件活") -> str:
    """一条派出去、并且真的跑过一轮的活。"""
    thread = _dispatched(client, room_id, title)
    _turn(client, _service(client, tmp_path, _Screen()), thread)
    return thread


def _dormant(client, project_id: str, room_id: str, title: str = "没跑过的") -> str:
    """一条开出来但谁都没跑过的活。"""
    made: dict[str, str] = {}

    async def _run() -> None:
        async with client.test_factory() as s:
            task = await TaskService(s).open_thread(
                project_id=uuid.UUID(project_id),
                room_id=uuid.UUID(room_id),
                title=title,
                owner_handle="alice",
                created_by="alice",
            )
            made["id"] = str(task.id)
            await s.commit()

    asyncio.run(_run())
    return made["id"]


def _service(client, tmp_path, screen: _Screen) -> ChatService:
    return ChatService(
        session_factory=client.test_factory,
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
    """一步都还没走的房间说自己没跑过 —— 派活本身不算跑过一轮。"""
    pid, room = _room(client)
    _dispatched(client, room)

    assert _has_run(client, pid, room, bearer) is False


def test_a_card_is_not_asked_whether_it_has_run(client, bearer):
    """卡不是地点，这个问题对它不成立 —— 问了是 404，不是 False。

    False 会更糟：那读起来像「这条活闲着」，而事实是这个问题问错了对象。
    """
    pid, room = _room(client)
    card = _dispatched(client, room)

    r = client.get(
        f"/projects/{pid}/topics/{card}/work-summary", headers=bearer("alice")
    )
    assert r.status_code == 404


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
