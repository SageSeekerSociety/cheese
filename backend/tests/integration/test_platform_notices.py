"""平台提示统一契约 —— 平台在房间里说的每一句都是「一行 + 可展开」.

## 这些测试在钉什么

平台自己在房间里说话原来有两条路。一条是正常的系统事件（`kind=event,
author_type=system`，前端一行灰字）。另一条更糟：`runner.submit(author="system")`
落下的其实是一条 **`author_type=human`** 的聊天消息 —— 前端把它当真人发言渲染，
完整气泡、名字显示成 "system"。CI 失败播报走的就是这条，最长能往房间里铺 4000
字符。

所以本文件的**核心断言不是「消息变短了」，而是「房间里不再多出一条人说的话」** ——
那是数据形状的改变，不是显示的改变，短不短是前端那张卡的事。

第二条硬约束是产品定的：**信息不能丢，只能收起来**。所以每条用例都会把原文
（CI 日志 / 检查输出 / 冲突文件清单）**一字不差**地从 `meta.detail` 里取回来。
只断言"变短了"是不够的 —— 那样把长文删掉也能过。

## 为什么闸门那两条不走 HTTP

`review/gate.py` 的 runner **已经退役**（`采纳即合并`，见该模块 docstring：
"THIS RUNNER IS NO LONGER DISPATCHED"），`routes/accept.py` 不再调 `dispatch`，
所以没有任何 HTTP 路径能把它跑起来。它留在库里是因为 `gate_sweep` 还要用它的
常量去清历史 `pending_gate` 行。要钉住它的话术，只能直接调 `gate._run` —— 用的
仍然是生产函数本体和真实的卡状态机，只是把 runner 换成一个记录器。
"""

import asyncio
import uuid

import pytest

from app.domain.review import gate
from app.domain.review.services import _NUDGE_TAIL_LIMIT
from app.domain.workspace import service as ws
from tests.conftest import wait_turns_idle

# 复用 PR 采纳那套 fake GitHub 装置 —— 本文件测的是同一条真实路径的另一端
# （房间里落下什么块），没有理由再造一套。
from tests.integration.test_accept_pr import (
    _accept_to_pr_open,
    _poll,
    _reset_client,
)


def _blocks(client, topic_id: str) -> list[dict]:
    return client.get(f"/api/topics/{topic_id}/blocks").json()["data"]["data"]


def _wait_for_event(client, topic_id: str, event_type: str, *, timeout: float = 10.0):
    """等那条系统事件落库。

    `runner.submit` 是 fire-and-forget，所以单靠 `wait_turns_idle()` 会有竞态：
    轮次还没注册进 runner 时它就返回了（test_upstream.py 里有一次 CI 首跑就挂的
    记录）。这里跟 test_accept_gate_orphan.py 一样，轮询到出现为止。
    """
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        wait_turns_idle()
        for block in _blocks(client, topic_id):
            if (block.get("meta") or {}).get("event_type") == event_type:
                return block
        time.sleep(0.05)
    seen = [
        (b["kind"], (b.get("meta") or {}).get("event_type"))
        for b in _blocks(client, topic_id)
    ]
    raise AssertionError(f"没等到 event_type={event_type} 的系统事件；现有的块：{seen}")


def _assert_is_a_platform_notice(
    block: dict, *, event_type: str, severity: str, who: str
) -> None:
    """契约本身：一行 content + 五个键齐全的 meta。"""
    assert block["kind"] == "event"
    assert block["author_type"] == "system"
    assert "\n" not in block["content"], f"content 不是一行：{block['content']!r}"
    assert len(block["content"]) <= 40, f"content 超过 40 字：{block['content']!r}"
    meta = block["meta"]
    assert meta["event_type"] == event_type
    assert meta["severity"] == severity
    assert meta["who"] == who
    # 五个键**总是**都在，前端不用先判断键存不存在。
    assert set(meta) >= {"event_type", "severity", "who", "detail", "detail_label"}


# --------------------------------------------------------------------------
# CI 没过 —— 本卡最主要的行为变化
# --------------------------------------------------------------------------


def test_ci_failure_lands_as_one_line_event_not_a_fake_human_message(
    client, monkeypatch
):
    """CI 播报以前是一条 `author_type=human`、作者叫 "system" 的聊天消息，正文
    最多 4000 字符。现在：房间里**没有**新的人类消息，只有一行系统事件，日志
    一字不差躺在 `meta.detail` 里。"""
    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        # 一段超过截断上限的日志尾巴，才能同时验"截断上限没被偷偷改小"和"没丢"。
        log = "".join(
            f"FAILED tests/test_thing.py::test_case_{i} - AssertionError\n"
            for i in range(200)
        )
        assert len(log) > _NUDGE_TAIL_LIMIT

        before = {b["id"] for b in _blocks(client, tid)}
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("failure", log)

        _poll(client)
        event = _wait_for_event(client, tid, "ci_failed")

        fresh = [b for b in _blocks(client, tid) if b["id"] not in before]
        # ① 核心：房间里没有多出任何一条"人"说的话。
        assert [b for b in fresh if b["author_type"] == "human"] == [], (
            "平台又伪装成人在房间里发言了"
        )

        # ② 一行人话。
        _assert_is_a_platform_notice(
            event, event_type="ci_failed", severity="error", who="cheese"
        )
        assert f"#{number}" in event["content"]

        # ③ 信息不能丢：日志一字不差取得回来，截断上限还是原来那个。
        assert event["meta"]["detail"] == log[:_NUDGE_TAIL_LIMIT]
        assert event["meta"]["detail_label"] == "CI 日志"
        # ④ 而它确实不在房间的正文里 —— 这才是"收起来"。
        assert "AssertionError" not in event["content"]
        assert all("AssertionError" not in (b.get("content") or "") for b in fresh)
    finally:
        _reset_client()


def test_ci_failure_still_hands_the_agent_the_whole_instruction(
    client, monkeypatch, stub_agent
):
    """改的是**房间里显示什么**，不是**芝士收到什么**：整段指令（日志 + 怎么读
    全文 + 该干什么）照旧作为 prompt 送到芝士手上。"""
    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = (
            "failure",
            "pytest: 3 failed",
        )
        _poll(client)
        _wait_for_event(client, tid, "ci_failed")
        wait_turns_idle()

        prompt = stub_agent.last_prompt or ""
        assert "pytest: 3 failed" in prompt
        # 芝士推不了 GitHub，指令必须说清楚是平台代推（2026-08-09 的回归）。
        assert "推送新 commit" not in prompt
        assert "平台会自动把新提交同步到这个 PR" in prompt
        # 要看全文得自己铸只读 token —— 这两句是芝士唯一能读到这条路的地方。
        assert "cheese gh-token" in prompt
        assert "repos/acme/widgets/actions/jobs/" in prompt
    finally:
        _reset_client()


# --------------------------------------------------------------------------
# PR 全绿但 GitHub 拒绝合并
# --------------------------------------------------------------------------


def test_merge_refused_lands_as_one_line_event(client, monkeypatch):
    fake, tid, number = _accept_to_pr_open(client, monkeypatch)
    try:
        reason = "HTTP 405：Merge commits are not allowed on this repository"
        fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全部通过")
        fake.merge_blocked_by_number[number] = reason

        before = {b["id"] for b in _blocks(client, tid)}
        _poll(client)
        event = _wait_for_event(client, tid, "merge_refused")

        fresh = [b for b in _blocks(client, tid) if b["id"] not in before]
        assert [b for b in fresh if b["author_type"] == "human"] == []
        _assert_is_a_platform_notice(
            event, event_type="merge_refused", severity="error", who="cheese"
        )
        # GitHub 的原话原样收在展开区里，不是摘要。
        assert event["meta"]["detail"] == reason
        assert "405" not in event["content"]
    finally:
        _reset_client()


# --------------------------------------------------------------------------
# 质量闸门 —— 红了 vs 没跑起来（两套话术不能合并）
# --------------------------------------------------------------------------


class _RecordingRunner:
    """只记不跑的 runner。闸门 runner 已退役，没有 HTTP 路径能驱动它。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def submit(self, chat_service, topic_id, **kw):
        self.calls.append({"topic_id": topic_id, **kw})
        return uuid.uuid4()


def _seed_pending_gate_card(client, topic_id: str) -> uuid.UUID:
    from app.domain.review.models import AcceptCard, AcceptStatus

    async def _do() -> uuid.UUID:
        async with client.test_factory() as session:
            card = AcceptCard(
                topic_id=uuid.UUID(topic_id),
                reviewer_handle="alice",
                routing_reason="最懂",
                status=AcceptStatus.pending_gate,
            )
            session.add(card)
            await session.flush()
            cid = card.id
            await session.commit()
            return cid

    return asyncio.run(_do())


def _run_retired_gate(client, monkeypatch, tmp_path, *, exit_code: int, tail: str):
    """把退役的闸门 runner 就地跑一次，返回它发出的那一次 submit。"""
    pid = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    tid = client.post(
        "/api/topics", json={"project_id": pid, "title": "做一个东西"}
    ).json()["data"]["id"]
    card_id = _seed_pending_gate_card(client, tid)

    monkeypatch.setattr(ws, "topic_worktree", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(
        ws,
        "run_check_command",
        lambda *_a, **_kw: {"exit_code": exit_code, "tail": tail},
    )
    runner = _RecordingRunner()
    asyncio.run(
        gate._run(
            client.test_factory,
            object(),  # chat_service: the recording runner never touches it
            runner,
            card_id=card_id,
            topic_id=uuid.UUID(tid),
            project_id=uuid.UUID(pid),
            command="pytest",
        )
    )
    assert len(runner.calls) == 1
    return runner.calls[0]


@pytest.mark.parametrize(
    ("exit_code", "event_type", "expect_in_line"),
    [
        # 检查跑了、红了 → 去改代码。
        (1, "gate_failed", "没通过"),
        # 检查根本没跑起来（check.sh --strict 的 exit 2）→ 去弄环境。对芝士来说
        # 这是完全相反的下一步，所以两个码必须分开 —— 见 gate.py 里那段注释。
        (gate.BLOCKED_EXIT_CODE, "gate_blocked", "没能跑起来"),
    ],
)
def test_gate_result_lands_as_one_line_event(
    client, monkeypatch, tmp_path, exit_code, event_type, expect_in_line
):
    output = "".join(f"E   assert {i} == {i + 1}\n" for i in range(200))
    call = _run_retired_gate(
        client, monkeypatch, tmp_path, exit_code=exit_code, tail=output
    )

    assert "\n" not in call["nudge_event"]
    assert len(call["nudge_event"]) <= 40
    assert expect_in_line in call["nudge_event"]
    meta = call["nudge_meta"]
    assert meta["event_type"] == event_type
    assert meta["severity"] == "error"
    assert meta["who"] == "cheese"
    # 检查输出的结尾原样收在 detail 里，1500 的上限没变。
    assert meta["detail"] == output[-1500:]
    # 给芝士的整段指令一个字没动 —— 它还是走 content 进 prompt。
    assert output[-1500:] in call["content"]


def test_gate_failed_and_gate_blocked_never_collapse_into_one_code(
    client, monkeypatch, tmp_path
):
    """守卫：有人图省事把两套话术合并时，这条会红。"""
    red = _run_retired_gate(client, monkeypatch, tmp_path, exit_code=1, tail="boom")
    blocked = _run_retired_gate(
        client, monkeypatch, tmp_path, exit_code=gate.BLOCKED_EXIT_CODE, tail="boom"
    )
    assert red["nudge_meta"]["event_type"] != blocked["nudge_meta"]["event_type"]
    assert red["nudge_event"] != blocked["nudge_event"]


# --------------------------------------------------------------------------
# 同步上游冲突
# --------------------------------------------------------------------------


def test_upstream_conflict_lands_as_one_line_event(client, monkeypatch):
    """冲突文件清单进 `meta.detail`，而且是**完整**清单 —— 给芝士的正文为了可读
    只列前 15 个，展开区不该跟着缩水。"""
    from app.domain.workspace import upstream_conflict

    pid = client.post("/api/projects", json={"name": "P"}).json()["data"]["id"]
    files = [f"pkg/mod_{i}.py" for i in range(20)]
    monkeypatch.setattr(
        ws, "prepare_upstream_conflict_resolution", lambda *_a, **_kw: files
    )
    runner = _RecordingRunner()

    async def _do() -> dict | None:
        async with client.test_factory() as session:
            out = await upstream_conflict.dispatch(
                session,
                uuid.UUID(pid),
                requested_by="alice",
                chat=object(),  # type: ignore[arg-type] — the runner records only
                runner=runner,  # type: ignore[arg-type]
            )
            await session.commit()
            return out

    assert asyncio.run(_do()) is not None
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert "\n" not in call["nudge_event"]
    assert len(call["nudge_event"]) <= 40
    assert "20" in call["nudge_event"]  # 几个文件，扫一眼就知道
    meta = call["nudge_meta"]
    assert meta["event_type"] == "upstream_conflict"
    assert meta["severity"] == "warn"
    assert meta["who"] == "cheese"
    # 完整 20 个，不是正文里那 15 个。
    assert meta["detail"].splitlines() == files
    assert files[19] not in call["nudge_event"]
