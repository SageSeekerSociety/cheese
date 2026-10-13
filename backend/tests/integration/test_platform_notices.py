"""平台提示统一契约 —— 平台在房间里说的每一句都是「一行 + 可展开」.

## 这些测试在钉什么

平台自己在房间里说话原来有两条路。一条是正常的系统事件（`kind=event,
author_type=platform`，前端一行灰字）。另一条更糟：`runner.submit(author="system")`
落下的其实是一条 **`author_type=human`** 的聊天消息 —— 前端把它当真人发言渲染，
完整气泡、名字显示成 "system"。CI 失败播报走的就是这条，最长能往房间里铺 4000
字符。

所以本文件的**核心断言不是「消息变短了」，而是「房间里不再多出一条人说的话」** ——
那是数据形状的改变，不是显示的改变，短不短是前端那张卡的事。

第二条硬约束是产品定的：**信息不能丢，只能收起来**。所以每条用例都会把原文
（CI 日志 / 检查输出 / 冲突文件清单）**一字不差**地从 `meta.detail` 里取回来。
只断言"变短了"是不够的 —— 那样把长文删掉也能过。

## 为什么闸门那两条不走 HTTP

`review/gate.py` 的 runner 已随 `采纳即合并`（#296）整个退役（#718 又删掉了
配置它的设置），话术的用例也一起删了 —— 没有任何代码路径还会产生
`gate_failed` / `gate_blocked`，也就没有话术可钉。这两个状态仍留在卡片枚举和前端
里，因为历史行带着它们，得渲染得出来。
"""

from app.domain.review import github_pr
from app.domain.review.services import _NUDGE_TAIL_LIMIT
from tests.conftest import wait_work_idle
from tests.delivery import delivery_task_id
from tests.integration.conftest import room_agent_seat

# 复用 PR 采纳那套 fake GitHub 装置 —— 本文件测的是同一条真实路径的另一端
# （房间里落下什么块），没有理由再造一套。
from tests.integration.test_accept_pr import (
    _accept,
    _arm,
    _cards,
    _poll,
    _protect,
    _ready_card,
)
from tests.integration.test_accept_pr import (
    app_world as _app_world_fixture,
)

app_world = _app_world_fixture


def _pr_card(client, app_world):
    """(fake, tid, cid, number)：一张等采纳、骑着 PR 的卡（#718）。"""
    _pid, tid, cid, number, _head = _ready_card(client, app_world)
    return app_world["fake"], tid, cid, number


def _blocks(client, topic_id: str) -> list[dict]:
    task_id = str(delivery_task_id(client, topic_id))
    response = client.get(f"/topics/{task_id}/blocks")
    assert response.status_code == 200, response.text
    blocks = response.json()["data"]["data"]
    assert all(block["conversation_id"] == task_id for block in blocks)
    return blocks


def _by_a_person(b: dict) -> bool:
    """这一条是一个人说的：档位说「参与者」，而署名不是芝士的。

    档位只分得出参与者和平台，「是人还是芝士」在署名上 —— 这条测试要的正是后者。

    判的是「档位就是 participant」，不是「不叫平台那一档的名字」：平台那一档有两个
    名字（新行写 `platform`，存量行还是 `system`），列名字的写法会漏掉其中一个，而
    漏掉的那一支正好落进「一个人说的」——这份文件整个是在防这件事。
    """
    return b["author_type"] == "participant" and not b["author"].startswith("cheese")


def _wait_for_event(client, topic_id: str, event_type: str, *, timeout: float = 10.0):
    """等那条系统事件落库。

    `runner.submit` 是 fire-and-forget，所以单靠 `wait_work_idle()` 会有竞态：
    轮次还没注册进 runner 时它就返回了（test_upstream.py 里有一次 CI 首跑就挂的
    记录）。这里跟 test_accept_gate_orphan.py 一样，轮询到出现为止。
    """
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        wait_work_idle()
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
    assert block["author_type"] == "platform"
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


def test_ci_failure_lands_as_one_line_event_not_a_fake_human_message(client, app_world):
    """CI 播报以前是一条 `author_type=human`、作者叫 "system" 的聊天消息，正文
    最多 4000 字符。现在：房间里**没有**新的人类消息，只有一行系统事件，日志
    一字不差躺在 `meta.detail` 里。"""
    fake, tid, _cid, number = _pr_card(client, app_world)
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
    assert [b for b in fresh if _by_a_person(b)] == [], "平台又伪装成人在房间里发言了"

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


def test_ci_failure_records_the_whole_instruction_for_the_task_session(
    client, app_world, stub_hooks
):
    """Keep the full instruction durable until the task's parent is known.

    This producer test checks committed intent, not a native input receipt.
    """
    fake, tid, _cid, number = _pr_card(client, app_world)
    fake.check_state_by_sha[fake.prs[number]["head_sha"]] = (
        "failure",
        "pytest: 3 failed",
    )
    _poll(client)
    _wait_for_event(client, tid, "ci_failed")
    # The same failing PR is polled again; both event and intent stay singular.
    _poll(client)
    assert (
        sum(
            (b.get("meta") or {}).get("event_type") == "ci_failed"
            for b in _blocks(client, tid)
        )
        == 1
    )
    wait_work_idle()

    # The producer commits the full instruction for the task's own session, in
    # the task's conversation.

    import uuid as _uuid

    from sqlalchemy import select

    from app.domain.delivery.models import Delivery

    task_id = str(delivery_task_id(client, tid))

    async def recorded_instruction():
        async with client.test_factory() as db:
            rows = list(
                await db.scalars(
                    select(Delivery).where(
                        Delivery.conversation_id == _uuid.UUID(task_id)
                    )
                )
            )
            matching = [
                row for row in rows if "pytest: 3 failed" in row.payload["content"]
            ]
            assert len(matching) == 1
            row = matching[0]
            assert str(row.conversation_id) == task_id
            assert row.agent_instance_id is not None
            return row.payload["content"]

    prompt = client.portal.call(recorded_instruction)
    assert "pytest: 3 failed" in prompt
    # Recovery names the task workspace and updates its existing PR. It must
    # retain human approval instead of promising an unconditional merge.
    tasks = client.get(f"/topics/{tid}/tasks").json()["data"]["data"]
    assert f"cheese worktree {tasks[0]['id']}" in prompt
    assert "cheese push-fix" in prompt
    assert "采纳由人决定" in prompt
    # The native CLI supplies credentials when fetching the full logs.
    assert "gh api repos/acme/widgets/actions/jobs/" in prompt
    assert "repos/acme/widgets/actions/jobs/" in prompt


# --------------------------------------------------------------------------
# PR 全绿但 GitHub 拒绝合并
# --------------------------------------------------------------------------


def test_merge_refused_lands_as_one_line_event(client, app_world):
    # #718：轮询器只替 armed 的卡调合并 API，所以「全绿但被拒」出现在布防之后。
    fake, tid, cid, number = _pr_card(client, app_world)
    pid = client.get(f"/topics/{tid}").json()["data"]["project_id"]
    _protect(client, pid, auto_merge_allowed=True)
    assert _arm(client, cid, "alice").status_code == 200
    reason = "HTTP 405：Merge commits are not allowed on this repository"
    fake.check_state_by_sha[fake.prs[number]["head_sha"]] = ("success", "全部通过")
    fake.merge_blocked_by_number[number] = reason

    before = {b["id"] for b in _blocks(client, tid)}
    _poll(client)
    event = _wait_for_event(client, tid, "merge_refused")

    fresh = [b for b in _blocks(client, tid) if b["id"] not in before]
    assert [b for b in fresh if _by_a_person(b)] == []
    _assert_is_a_platform_notice(
        event, event_type="merge_refused", severity="error", who="cheese"
    )
    # GitHub 的原话原样收在展开区里，不是摘要。
    assert event["meta"]["detail"] == reason
    assert "405" not in event["content"]


# --------------------------------------------------------------------------
# 已采纳的 PR 离开合并队列但没合并
# --------------------------------------------------------------------------


def test_leaving_the_merge_queue_wakes_the_task_once(client, app_world, monkeypatch):
    """PR #3496：出队后卡片仍写着「已采纳」，26 分钟没人发现。

    出队是 GitHub 在队列里判了检查不过，或有人把这条取出。这和 GitHub 直接拒绝
    合并一样，是「卡住了、芝士在任务目录里能动手」，所以任务对话里要出现一条
    merge_refused 级别的通知；同一次出队只出现一条（60 秒一拍的轮询不能刷屏）。
    """
    fake = app_world["fake"]
    _pid, tid, cid, number, head = _ready_card(client, app_world)
    fake.check_state_by_sha[head] = ("success", "green")

    async def enqueue(**kwargs):
        return github_pr.MergeResult(queued=True)

    async def not_in_queue(**kwargs):
        return False

    monkeypatch.setattr(fake, "merge_pull_request", enqueue)
    monkeypatch.setattr(fake, "merge_queue_entry", not_in_queue, raising=False)
    assert _accept(client, cid).status_code == 200
    assert "等待队列检查" in _cards(client, tid)[0]["note"]

    before = {b["id"] for b in _blocks(client, tid)}
    _poll(client)
    event = _wait_for_event(client, tid, "merge_refused")

    # 卡本身没动：还在等，没被自动重新采纳，也没被算成已合并。
    assert _cards(client, tid)[0]["status"] == "pending"
    _assert_is_a_platform_notice(
        event, event_type="merge_refused", severity="error", who="cheese"
    )
    assert f"#{number}" in event["content"]
    fresh = [b for b in _blocks(client, tid) if b["id"] not in before]
    assert [b for b in fresh if _by_a_person(b)] == [], "平台又伪装成人在房间里说话"

    # 再轮询两拍：这次出队没有变，房间里仍只有那一条。
    _poll(client)
    _poll(client)
    assert (
        sum(
            (b.get("meta") or {}).get("event_type") == "merge_refused"
            for b in _blocks(client, tid)
        )
        == 1
    )

    # 任务被叫醒：完整指令落进任务自己的会话，说清出队和要做的事。
    wait_work_idle()

    import uuid as _uuid

    from sqlalchemy import select

    from app.domain.delivery.models import Delivery

    task_id = str(delivery_task_id(client, tid))

    async def recorded_instruction() -> str:
        async with client.test_factory() as db:
            rows = list(
                await db.scalars(
                    select(Delivery).where(
                        Delivery.conversation_id == _uuid.UUID(task_id)
                    )
                )
            )
            matching = [row for row in rows if "离开合并队列" in row.payload["content"]]
            assert len(matching) == 1
            assert matching[0].agent_instance_id is not None
            return matching[0].payload["content"]

    prompt = client.portal.call(recorded_instruction)
    assert f"cheese worktree {task_id}" in prompt
    assert "cheese push-fix" in prompt
    assert "重新入队" in prompt
    assert "重新采纳" in prompt


# --------------------------------------------------------------------------
# 「调度座位」：设了以后，这两类事件也在它那里开工一轮
# --------------------------------------------------------------------------


def _task_deliveries(client, topic_id: str) -> list:
    """Every row of the task's own delivery ledger, oldest first — the ledger
    the runner turns into turns. Ordered so that no test depends on which row
    the database happens to hand back first."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.domain.delivery.models import Delivery

    task_id = str(delivery_task_id(client, topic_id))

    async def _read():
        async with client.test_factory() as db:
            return list(
                await db.scalars(
                    select(Delivery)
                    .where(Delivery.conversation_id == _uuid.UUID(task_id))
                    .order_by(Delivery.recorded_at, Delivery.dedup_key)
                )
            )

    return client.portal.call(_read)


def _dispatch_rows(rows: list, event_type: str) -> list:
    """The 调度座位 rows for one event. `eventType` is what tells the two
    apart: the notice template itself names both events (「可以合并」和
    「离开合并队列」), so searching the text matches either one."""
    return [r for r in rows if r.payload.get("eventType") == event_type]


def _second_seat(client, pid: str) -> str:
    """Another agent in this project, seated nowhere: only the project's
    「调度座位」 setting can address it, which is the point — it is not the
    agent that works the task."""
    import uuid as _uuid

    from app.domain.agent_instance.services import AgentInstanceService
    from app.domain.identity.handles import agent_instance_handle

    async def _make() -> str:
        async with client.test_factory() as db:
            made = await AgentInstanceService(db).create(
                project_id=_uuid.UUID(pid),
                handle="dispatcher",
                type_name=None,
                display_name="Dispatcher",
            )
            await db.commit()
            return agent_instance_handle(made.id)

    return client.portal.call(_make)


def test_the_dispatch_seat_is_woken_on_ready_and_on_queue_exit(
    client, app_world, monkeypatch
):
    """设了「调度座位」= 「可以合并」和「离开合并队列」也给它记一条投递。

    不设（默认）时这两条路径和以前一字不差 —— 上面那条出队用例跑的就是那个状态，
    它断言的「同一次出队只有一条」正是靠这里没有第二条投递。

    设了以后投递落在**这句话刚落下的那段对话**里（这里是任务会话）：项目设置只说
    一个 handle，平台手里只有这一段对话，事件出在哪就在哪儿告诉它。走的是点名、
    定时投递共用的那本投递账本（`delivery/agent.record_agent`），不是站内信 ——
    要的是这一轮真的跑起来，不是收件箱里多一行。

    这里的座位是项目里另一个队友（下一条用例是它正好就是这条任务的席位）：两类
    事件各投一条，正文里是**这一条事件自己的那句话**，落到这张卡上就是它的号。
    """
    fake = app_world["fake"]
    pid, tid, cid, number, head = _ready_card(client, app_world)
    seat = _second_seat(client, pid)
    _protect(client, pid, dispatch_seat=seat)

    # ① 可以合并（`accept_ready`）
    fake.check_state_by_sha[head] = ("success", "全绿")
    _poll(client)
    _wait_for_event(client, tid, "accept_ready")
    wait_work_idle()
    ready = _dispatch_rows(_task_deliveries(client, tid), "accept_ready")
    assert len(ready) == 1
    assert ready[0].recipient_handle == seat
    assert ready[0].agent_instance_id is not None
    assert f"PR #{number} 可以合并了" in ready[0].payload["content"]

    # ② 离开合并队列（`merge_refused`）
    async def enqueue(**kwargs):
        return github_pr.MergeResult(queued=True)

    async def not_in_queue(**kwargs):
        return False

    monkeypatch.setattr(fake, "merge_pull_request", enqueue)
    monkeypatch.setattr(fake, "merge_queue_entry", not_in_queue, raising=False)
    assert _accept(client, cid).status_code == 200
    _poll(client)
    _wait_for_event(client, tid, "merge_refused")
    wait_work_idle()
    left = _dispatch_rows(_task_deliveries(client, tid), "merge_refused")
    assert len(left) == 1
    assert left[0].recipient_handle == seat
    # 出队那一条带着要动手的那两句和这张卡的号：怎么看、怎么修。
    assert f"PR #{number} 离开了合并队列但未合并" in left[0].payload["content"]
    assert "cheese push-fix" in left[0].payload["content"]


def test_a_dispatch_seat_that_works_the_task_is_woken_once(
    client, app_world, monkeypatch
):
    """调度座位就是这条任务自己的席位时，一次出队只叫它一轮。

    任务自己的席位本来就会为出队收到一份完整指令（怎么看、在任务目录里怎么修），
    比调度座位那份两行摘要更全。两份都投，同一个 agent 为同一件事跑两轮，第二轮
    手里的话还更少。座位是它时不再投第二份；「可以合并」那条没有别人投过，照旧。
    """
    fake = app_world["fake"]
    pid, tid, cid, _number, head = _ready_card(client, app_world)
    seat = room_agent_seat(client, tid)  # 这条任务自己的那条线
    task_id = str(delivery_task_id(client, tid))
    _protect(client, pid, dispatch_seat=seat)

    fake.check_state_by_sha[head] = ("success", "全绿")
    _poll(client)
    _wait_for_event(client, tid, "accept_ready")
    wait_work_idle()
    assert len(_dispatch_rows(_task_deliveries(client, tid), "accept_ready")) == 1

    async def enqueue(**kwargs):
        return github_pr.MergeResult(queued=True)

    async def not_in_queue(**kwargs):
        return False

    monkeypatch.setattr(fake, "merge_pull_request", enqueue)
    monkeypatch.setattr(fake, "merge_queue_entry", not_in_queue, raising=False)
    before = {row.id for row in _task_deliveries(client, tid)}
    assert _accept(client, cid).status_code == 200
    _poll(client)
    _wait_for_event(client, tid, "merge_refused")
    wait_work_idle()

    fresh = [r for r in _task_deliveries(client, tid) if r.id not in before]
    # 调度座位那一路没有多投一条。
    assert _dispatch_rows(fresh, "merge_refused") == []
    # 叫它的是任务自己那份完整指令，还是原来那一条。
    assert len(fresh) == 1
    assert fresh[0].recipient_handle == seat
    assert f"cheese worktree {task_id}" in fresh[0].payload["content"]


# --------------------------------------------------------------------------
# 只记不跑的 runner（被下面几组用例共用）
# --------------------------------------------------------------------------
