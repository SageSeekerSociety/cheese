"""One term, one way of saying it ran out, and one gate on the way back.

A lease is where four facts about a borrowed machine used to sit apart: how long
we have it, what state it is in, what has to be back in our hands before we let
go, and what the agent is told when it ends. These hold the four together.

结论 24、39、55。不变量 I19、I25。
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.agent import executor_transport, place, platform_failures

ROOM = "9f1d0f9f-0a9a-4f6e-9b6e-2f6a3f0d9c11"


def test_a_row_written_before_the_lease_had_states_reads_as_in_use_forever():
    """存量行读成「在用、无期限」。

    这一列在本次之前记的是「手在哪」，不记「到什么时候」。把它读成别的什么都会立刻
    造出一批假事实：读成已归还，每一条跑着的会话都会被当成可回收的；读成休眠，下一
    轮会去唤醒一台从没停过的机器。
    """
    before = {"kind": "device", "device_id": "box-1", "resource_id": ROOM}
    lease = place.Lease.from_record(before)

    assert lease is not None
    assert lease.state is place.LeaseState.in_use
    assert lease.expires_at is None
    assert lease.expired(datetime(2099, 1, 1, tzinfo=UTC)) is False


def test_no_row_at_all_is_no_lease_rather_than_an_expired_one():
    """手就在会话机上的那些会话（私聊这一类）根本没有租约，不是有一份过期的。"""
    assert place.Lease.from_record(None) is None
    assert place.Lease.from_record({}) is None


def test_the_lease_writes_its_own_state_without_touching_what_is_already_there():
    """三态与期限写回那一行，而那一行上执行器自己的 ``state`` 不能被顶掉。

    两个 ``state`` 挤进一个 dict，后写的会悄悄覆盖先写的——而执行器那一位是重开一
    轮时用来判断该不该重装的，被顶掉之后每一轮都会走一遍安装。
    """
    written = place.Lease(
        machine="box-1",
        resource_id=ROOM,
        state=place.LeaseState.asleep,
        expires_at=datetime(2026, 9, 20, 12, tzinfo=UTC),
        asleep_until=datetime(2026, 9, 20, 12, 30, tzinfo=UTC),
    ).into({"kind": "device", "device_id": "box-1", "resource_id": ROOM, "state": "up"})

    assert written["state"] == "up"
    read = place.Lease.from_record(written)
    assert read is not None
    assert read.state is place.LeaseState.asleep
    assert read.asleep_until == datetime(2026, 9, 20, 12, 30, tzinfo=UTC)
    assert read.wakeable(datetime(2026, 9, 20, 12, 15, tzinfo=UTC)) is True
    assert read.wakeable(datetime(2026, 9, 20, 12, 45, tzinfo=UTC)) is False


def test_the_gate_takes_three_receipts_to_return_and_none_to_sleep():
    """同一个函数回答两种转移，答案不同——这就是「休眠不是归还」的全部内容。"""
    nothing = place.Receipts()
    all_three = place.Receipts(
        transcript_stored=True, memory_tidied=True, work_published=True
    )

    assert place.receipts_ready(place.LeaseState.asleep, nothing) == (True, "")
    assert place.receipts_ready(place.LeaseState.returned, all_three) == (True, "")

    ready, why = place.receipts_ready(place.LeaseState.returned, nothing)
    assert ready is False
    assert "transcript" in why and "记忆整理" in why and "推上去" in why


def test_the_shipped_transport_carries_the_sentence_the_platform_chose():
    """机器上那份传输层带的是同一句话，不是一个状态码。

    它在那台机器上运行，我们的东西一样都 import 不到，所以它带一份拷贝。拷贝漂了
    不会有任何东西失败：agent 照样读到一句话，只不过那句话不再告诉它这一轮还剩哪些
    通路，而是告诉它有个数字。
    """
    assert executor_transport.MACHINE_OUT_OF_REACH == (
        platform_failures.MACHINE_OUT_OF_REACH
    )


def test_what_the_agent_reads_when_its_hands_are_out_of_reach_has_no_status_code(
    monkeypatch, tmp_path
):
    """够不着是一条能力话，不是一次 HTTP 报错（结论 23、不变量 I25）。

    断言的是 agent 真的读到的那个字符串：一个裸状态码会让它回头去自己的工具调用里
    找 bug，而它需要知道的是「文件和命令这一轮没有，对话和记忆还有」。后端早就写好
    的中文 body 和 ``X-Device-Id`` 头在这条路上本来就全丢了，剩下的只有那个数字。
    """
    token = tmp_path / "execution.token"
    token.write_text("t")

    class Response:
        status = 503

        @staticmethod
        def read():
            return b"whatever the executor said"

    class Connection:
        sock = None

        def request(self, method, path, *, body, headers):
            return None

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            pass

    client = executor_transport.RemoteClient(
        {"kind": "device", "url": "http://executor.test", "token_file": str(token)}
    )
    monkeypatch.setattr(client, "connection", lambda: (Connection(), "/execution"))
    client.transport.headers = {}

    with pytest.raises(RuntimeError) as raised:
        client.call("context_fs")

    said = str(raised.value)
    assert not re.search(r"\b[1-5][0-9][0-9]\b", said)
    assert "HTTP" not in said
    assert said == platform_failures.MACHINE_OUT_OF_REACH


def test_nothing_swaps_a_machine_because_a_lease_ran_out():
    """到期是感知，不是决策（结论 55）。

    平台没有「等到期就替它换一台」的路径，而这一条只有在没有人写过它的时候才成
    立——所以守在这里：租约到期的判定和「换一台机器」的两个动作（改绑定、放掉绑
    定）不许出现在同一个函数里。
    """
    import ast

    root = Path(place.__file__).resolve().parents[3]
    moving = {"bind_topic_device", "release_topic_device"}
    offenders = []
    for path in (root / "app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            names = {
                sub.func.attr
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
            }
            if ("expired" in names or "wakeable" in names) and names & moving:
                offenders.append(f"{path.relative_to(root)}::{node.name}")
    assert offenders == []
