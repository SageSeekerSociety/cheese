"""卡上的「重试」：它说重试有用，就得真的有用。

2026-10-08 的一个任务页上，卡片写着「会话没有启动 · 这个频道的环境没有连接 ·
可以重试」，点下去却回答「没有需要重试的消息」。房间里确实没有一条没人读过的
消息 —— 那一次要交出去的是**平台自己欠这个任务的输入**：任务开始的那句指令。
它躺在投递账本上等退避时间，而要有人再送它一次，得先在同一个项目里碰巧发生
另一件走 `dispatch_pending` 的事（另一个人说话、另一条任务指令）。一个安静下来
的任务，等不到那一天。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.api.deps import get_chat_service, get_work_runner
from app.domain.agent.session_host.driver import startup_refused
from app.domain.delivery.agent import dispatch_pending
from app.domain.delivery.models import Delivery
from tests.conftest import StubChannel, wait_work_idle
from tests.integration.conftest import (
    post_project,
    session_auth_headers,
    teammate_task,
)

# What dev's session host recorded for a session relaunched onto a machine the
# work lease answered 未连接 for (2026-09-25), paths shortened.
LOG = """cheese-runner 0f0f ended: Claude Code exited with status 1 before it started:
Traceback (most recent call last):
  File "/h/.cheese/remote-execution/client.py", line 544, in _take_leased_machine
    client.acquire(deadline=time.monotonic())
  File "/h/.cheese/remote-execution/executor_transport.py", line 515, in acquire
    raise PlatformHTTPError(response.status, body)
executor_transport.PlatformHTTPError: Platform HTTP 409: 工作电脑未连接"""


class MachineNotConnected(StubChannel):
    """这台机器一开始没连上；连上之后，一切照常。"""

    def __init__(self, **policy: float) -> None:
        super().__init__(**policy)
        self.online = False

    async def open(self, session, agent: str, launch) -> None:
        if not self.online:
            raise startup_refused(LOG, harness="Claude Code")
        await super().open(session, agent, launch)


@pytest.fixture
def stub_hooks() -> MachineNotConnected:
    # Overrides conftest's stub_hooks for this module; `client` picks it up.
    return MachineNotConnected()


def _started_task(client, title: str = "接口分页") -> str:
    """一个已经开始、而它的会话没能起来的任务，返回它的 id。

    建出来即已开始（#718），那句交代写在开场指令里 —— 机器没连上时，它躺在
    投递账本上，房间里留下的是那张「可以重试」的卡。"""
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "频道"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    task = teammate_task(client, room, title)
    wait_work_idle()
    return task["id"]


def _retry_offered(client, task_id: str) -> bool:
    """房间此刻有没有在说「可以重试」—— 按钮画不画，看的就是这一句。"""
    blocks = client.get(f"/topics/{task_id}/blocks").json()["data"]["data"]
    return any((b.get("meta") or {}).get("retryable") is True for b in blocks)


def _summon(client, task_id: str) -> dict:
    r = client.post(
        f"/topics/{task_id}/summon", json={}, headers=session_auth_headers("alice")
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_the_retry_a_card_offers_sends_the_instruction_the_platform_is_holding(
    client, stub_hooks
):
    task_id = _started_task(client)
    assert _retry_offered(client, task_id)

    # 机器连上了 —— 那是这一轮唯一缺的东西。
    stub_hooks.online = True

    assert _summon(client, task_id) == {"started": True}
    wait_work_idle()
    # 交出去的是那句指令本身，不是「房间里有人有东西没读到」。
    assert "接口分页" in (stub_hooks.told or "")


def test_a_task_with_nothing_owed_still_says_there_is_nothing(client, stub_hooks):
    """没有欠着的东西时，回答照旧 —— 一个按钮不等于总有一轮要开。"""
    task_id = _started_task(client)
    stub_hooks.online = True

    assert _summon(client, task_id) == {"started": True}
    wait_work_idle()
    assert _summon(client, task_id) == {"started": False, "reason": "nothing_pending"}


def _failure_lines(client, task_id: str) -> list[dict]:
    """The lines in the task saying its teammate could not do the work."""
    blocks = client.get(f"/topics/{task_id}/blocks").json()["data"]["data"]
    return [b for b in blocks if (b.get("meta") or {}).get("retryable") is True]


def _platform_tries_again(client, task_id: str) -> int:
    """Time passes until the instruction's backoff runs out, and the platform's
    own sweep of the ledger tries it again. Returns how many it tried."""
    factory = client.test_request_factory
    chat = client.app.dependency_overrides[get_chat_service]()

    async def sweep() -> int:
        async with factory() as session:
            await session.execute(
                update(Delivery)
                .where(
                    Delivery.conversation_id == uuid.UUID(task_id),
                    Delivery.sent_at.is_(None),
                )
                .values(retry_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()
        return await dispatch_pending(factory, chat=chat, runner=get_work_runner())

    return client.portal.call(sweep)


def test_the_platform_trying_an_instruction_again_does_not_say_it_failed_again(
    client, stub_hooks
):
    """2026-11-02, on a task whose machine was not connected: every time the
    task was opened the line 「这次没做完」 read ×2, then ×4, then ×5. Nobody had
    done anything; the platform was trying the task's opening instruction again
    on its own backoff, and each try that failed the same way added a line.

    One instruction that could not start is one line, however many times the
    platform tries it again behind the scenes."""
    task_id = _started_task(client)
    assert len(_failure_lines(client, task_id)) == 1
    factory = client.test_request_factory

    for _ in range(3):
        assert _platform_tries_again(client, task_id) == 1
        wait_work_idle()

    async def attempts() -> list[int]:
        async with factory() as session:
            return list(
                await session.scalars(
                    select(Delivery.attempts).where(
                        Delivery.conversation_id == uuid.UUID(task_id)
                    )
                )
            )

    # It was tried again, and still could not start each time ...
    assert client.portal.call(attempts) == [4]
    # ... and the task says so once.
    assert len(_failure_lines(client, task_id)) == 1


def test_a_retry_a_person_asked_for_says_how_it_went(client, stub_hooks):
    """Someone pressed 「重试」 and it could not start again: the task says so,
    one line more, so the press visibly did something. The platform's own
    tries after that still add nothing."""
    task_id = _started_task(client)
    assert len(_failure_lines(client, task_id)) == 1

    assert _summon(client, task_id) == {"started": True}
    wait_work_idle()
    assert len(_failure_lines(client, task_id)) == 2

    assert _platform_tries_again(client, task_id) == 1
    wait_work_idle()
    assert len(_failure_lines(client, task_id)) == 2
