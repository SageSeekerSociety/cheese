"""进程把「谁在做这条活」忘了，不等于那个分身死了。

后端一重启、房间换一个会话、分身交回一次话 —— 跑轮次进程手里那份认领（内存里的
`_live_workers`）就没了，而这三件事都说明不了那个分身死没死。看板原来只看那份认领，
答不上话就退回「这条活 10 分钟没落 block」，于是把一条正在埋头跑长命令的活报成失联。

持久的那一半是这条活**开工那一轮**（`Task.execution_turn_id`）：区间还开着，就还没
有人说过这件事收尾了；关掉它的正是知道区别的那一个 —— 孤儿扫描，在容器死掉 / 轮次
闷死的时候把区间关掉。

下面两条活只差这一件事：分身在一条一小时没说话、一条 block 都没落过的活上（时间戳
那条规则判它们失联），一条开工轮次开着、一条关着。开着的那条是这次修的，关着的那条
证明没有修过头。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.agent.models import AgentTurn
from app.domain.agent.service import AgentSubagentStart, AgentSubagentStop
from app.domain.project.models import Project
from app.domain.room_task.models import Task
from app.domain.room_task.presentation import LOST_SIGNAL_AFTER, Building
from app.domain.topic.models import Topic, TopicKind
from tests.integration.conftest import a_team

AGENT = "agent-quiet"
#: 一条活安静了这么久：远在宽限期之外，所以时间戳那条规则只会给出一个答案。
SILENT_FOR = LOST_SIGNAL_AFTER + timedelta(hours=1)


def _seed(client, stub_hooks) -> dict[str, str]:
    """两条活，只差开工那一轮开着还是关了。屏幕另外点亮：分身住在房间的会话里。"""
    ids: dict[str, str] = {}
    quiet_turn, over_turn = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(UTC)
    started = now - SILENT_FOR

    async def _seed_it() -> None:
        async with client.test_factory() as s:
            project = Project(team_id=await a_team(s), name="P", owner_handle="alice")
            s.add(project)
            await s.flush()
            room = Topic(project_id=project.id, title="房间", kind=TopicKind.topic)
            s.add(room)
            await s.flush()
            quiet = Task(
                project_id=project.id,
                room_id=room.id,
                title="安安静静在跑的活",
                subagent_id=AGENT,
                execution_turn_id=quiet_turn,
                last_turn_at=started,
            )
            over = Task(
                project_id=project.id,
                room_id=room.id,
                title="那一轮早就收尾了的活",
                subagent_id=AGENT,
                execution_turn_id=over_turn,
                last_turn_at=started,
            )
            s.add_all([quiet, over])
            s.add_all(
                [
                    AgentTurn(
                        id=turn_id,
                        topic_id=room.id,
                        continuation_id=uuid.uuid4(),
                        author="alice",
                        content="做这件事",
                        is_resume=False,
                        resendable=True,
                        started_at=started,
                        delivered_at=started,
                        stopped_at=None if stopped_at is None else now,
                    )
                    for turn_id, stopped_at in (
                        (quiet_turn, None),
                        (over_turn, started),
                    )
                ]
            )
            await s.flush()
            ids.update(
                project=str(project.id),
                room=str(room.id),
                quiet=str(quiet.id),
                over=str(over.id),
            )
            await s.commit()

    asyncio.run(_seed_it())
    stub_hooks.runtime.live[(uuid.UUID(ids["room"]), "cheese")] = "screen"  # type: ignore[assignment]
    return ids


def _cells(client, ids: dict[str, str]) -> dict[str, str]:
    """一次读整个项目 —— 两条活在同一批里，各自答各自的。"""
    rows = client.get(f"/projects/{ids['project']}/tasks").json()["data"]["data"]
    return {row["id"]: row["presentation"]["display_status"] for row in rows}


def _chat(client):
    from app.api.deps import get_chat_service
    from app.main import app

    return app.dependency_overrides[get_chat_service]()


def _expected(ids: dict[str, str]) -> dict[str, str]:
    return {ids["quiet"]: Building.running, ids["over"]: Building.lost}


def test_a_silent_worker_on_an_open_turn_is_running(client, stub_hooks):
    ids = _seed(client, stub_hooks)
    chat = _chat(client)
    # 这次修的就是这一句：进程答不上话（内存里根本没有它），时间戳也说它一小时没动静。
    assert chat.worker_live(uuid.UUID(ids["room"]), AGENT) is None
    # 而它开工那一轮还开着 —— 所以看板说它在做，不是失联。
    assert _cells(client, ids) == _expected(ids)


def test_the_three_ways_the_process_forgets_do_not_call_it_lost(client, stub_hooks):
    ids = _seed(client, stub_hooks)
    chat = _chat(client)
    room = uuid.UUID(ids["room"])

    def claimed() -> None:
        """把认领放回内存，好让每条路各自把它弄丢一次。"""
        chat._note_worker_agent(room, AgentSubagentStart(agent_id=AGENT))

    # ① 后端重启：整份认领随进程没了。dev 每合一次 main 就部署一次。
    claimed()
    assert chat.worker_live(room, AGENT) is True
    chat._live_workers.clear()
    assert chat.worker_live(room, AGENT) is None
    assert _cells(client, ids) == _expected(ids)

    # ② 房间的会话换成另一个：`_note_room_session` 把这间房攒下的认领一次全弹掉。
    claimed()
    chat._note_room_session(room, "另一个屏幕")
    chat._note_room_session(room, "换回来的屏幕")
    assert chat.worker_live(room, AGENT) is None
    assert _cells(client, ids) == _expected(ids)

    # ③ 分身交回一次话：`SubagentStop` 只收回声明，不宣布它死了 —— 它可能只是把
    # 一条长命令停到了后台，等会儿接着干。
    claimed()
    chat._note_worker_agent(room, AgentSubagentStop(agent_id=AGENT))
    assert chat.worker_live(room, AGENT) is None
    assert _cells(client, ids) == _expected(ids)
