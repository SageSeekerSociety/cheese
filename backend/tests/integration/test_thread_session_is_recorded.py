"""一条跑过的支线，必须说得出自己跑过。

「现场」这一格由 `work-summary` 的 `has_run` 决定，而 `has_run` 问的是「这个地点
有没有哪个 agent 留下过会话行」。读侧从一开始就是按**地点**算的
（`AgentSessionRepository` 自己把一个 id 解析成 (房间, 支线) 两半）；坏的是
**写侧** —— 一轮结束时保存会话指针的那段先按 id 去 `topics` 表捞一行，而支线不在
那张表里，于是拿到 None，整段 remember 被安静地跳过。所以线上一条跑了几小时、
几百个 block 的支线，`has_run` 仍然是 `False`。

每条用例都**跑一轮真的活**：stub 只负责一块屏幕本来就该提供的东西（那几个 hook），
组装、归属、收尾全是生产那条路。刻意不直接往 `agent_sessions` 里塞行 —— 塞行测的
是读侧，而读侧本来就是好的。

同一行代码还带着第二个用途：`resume_token`。`--resume` 只在**冷启动**时用得上
（`build_session_launch` 那道守卫：tmux 会话本身就是一个普通话题的连续性），所以
写侧被跳过的后果不是「每轮都失忆」，而是**屏幕一旦被回收，这条支线就接不回自己那
段对话** —— 尽管 transcript 就躺在它自己的 session 目录里。这里用「第二轮冷启动
时拿到的 resume 指针」来钉它，因为那正是唯一用得上它的时刻。

两种支线在这里是分开的，因为它们证的事不一样：一条是**跑过一轮**的活，复现的是
线上那个症状本身；另一条是**谁都没跑过**的活，用来挡住「无脑返回 True」。

派活本身不再跑任何东西（`/split` 只建行和简报文档），所以「跑过」这件事在这里是
显式跑一轮跑出来的 —— 这也更贴题：这个文件问的是写侧记没记下会话，不是谁把那一轮
踢起来的。
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


def test_a_dispatched_thread_says_it_has_run(client, tmp_path, bearer):
    """跑过一轮的活要说自己跑过 —— 「现场」全靠这一个布尔值决定出不出。"""
    pid, room = _room(client)
    thread = _worked(client, tmp_path, room)

    assert _has_run(client, pid, thread, bearer) is True


def test_a_freshly_dispatched_thread_has_not_run(client, bearer):
    """刚派出去、还没人做的活说自己没跑过 —— 派活本身不算跑过一轮。"""
    pid, room = _room(client)
    thread = _dispatched(client, room)

    assert _has_run(client, pid, thread, bearer) is False


def test_an_untouched_sibling_still_says_it_has_not(client, tmp_path, bearer):
    """同房间另一条谁都没跑过的活，不能被兄弟带成「跑过」。

    没有这一条，一个无脑返回 True 的修法也能过上面那条。
    """
    pid, room = _room(client)
    _worked(client, tmp_path, room, "跑过的")
    idle = _dormant(client, pid, room)

    assert _has_run(client, pid, idle, bearer) is False


def test_a_threads_turn_does_not_make_its_room_look_run(client, tmp_path, bearer):
    """支线跑过，房间自己没跑过 —— 两个地点，两条会话。"""
    pid, room = _room(client)
    _worked(client, tmp_path, room)

    assert _has_run(client, pid, room, bearer) is False


def test_a_room_still_records_its_own_turn(client, tmp_path, bearer):
    """房间那条路本来就是好的，修支线不能把它弄坏。"""
    pid, room = _room(client)

    _turn(client, _service(client, tmp_path, _Screen()), room)

    assert _has_run(client, pid, room, bearer) is True


# --- resume：屏幕被回收之后，这段对话还接不接得回去 ---------------------------


def test_a_threads_next_cold_start_resumes_the_conversation_it_had(client, tmp_path):
    """第二次冷启动必须被告知「接着上一段」。

    `--resume` 只在冷启动时用得上，所以这就是支线丢没丢过对话的判据：写侧被跳过
    时，第二轮拿到的是 None —— 一段全新的对话，尽管 transcript 就在那儿。
    """
    pid, room = _room(client)
    thread = _dormant(client, pid, room, "两轮的活")
    screen = _Screen("s-thread-1")
    svc = _service(client, tmp_path, screen)

    _turn(client, svc, thread, "第一轮")
    _turn(client, svc, thread, "第二轮")

    assert screen.resume_asked[0] is None, "第一轮无可接续，这是对的"
    assert screen.resume_asked[1:] == ["s-thread-1"], screen.resume_asked
