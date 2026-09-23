"""管理看板：三条路由数出来的数对不对，以及谁拿不到它们。

这一批要钉的就两件事，每一件都写在一条用例里：

* **窗口的边界**。窗口是半开的 `[since, until)`，所以窗口外那一条（更老的那条）
  必须**不**进来；窗口内那一条必须进来。只断言「有数」的话，一条把所有历史都算
  进去的实现照样能过。
* **缺的天补 0**。窗口里没有数据的那几天必须**在** series 里、值是 0 —— 不补的话
  折线会把 7 天画成 2 天，而断点处是一条平滑的线，没有人看得出来。所以断言的是
  整条 series 的**逐日取值**，不是「某一天对上了」。

三个分类各有一次「数得对」的用例，三条路由各有一次「非管理员拿不到」（403）。

**写数据要写在 `client` 那个库**（`client.test_factory`），不能拿 `db_session`：
那个夹具绑的是 `settings.database_url`，`client` 绑的是 `TEST_DATABASE_URL`，是两个
库 —— 用 `db_session` 改数据是**静默无效**的，用例照样过。`test_feedback.py` 的
`_backdate` 那段 docstring 记着这个坑的完整来龙去脉。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.domain.device.models import DeviceRow
from app.domain.feedback.models import Feedback, FeedbackStatus, FeedbackTimeline
from app.domain.machine.models import WarmMachine
from app.domain.project.models import Project
from app.domain.review.models import AcceptCard, AcceptStatus
from app.domain.topic.models import Topic, TopicKind
from app.domain.usage.models import ResourceUsage
from app.domain.user.models import User
from tests.conftest import seed_user
from tests.integration.conftest import session_auth_headers

#: 放进管理员名单的那个 handle。和其它反馈用例一样：平台管理员是平台级的事实，
#: 和任何项目角色无关，所以它刻意不是任何东西的成员。
ADMIN = "stats-admin"

STRANGER = "stats-stranger"
REPORTER = "stats-reporter"

#: 看板默认的窗口就是 7 天，用例全部按它算下标。
DAYS = 7


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    """让 ``ADMIN`` 成为这一条用例里的平台管理员。

    `admin_handles()` 每次重读 settings，就是为了这个改动不用重启进程。
    """
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


# --- 时间：独立算一遍，不复用生产代码 -----------------------------------------


def _today() -> datetime:
    """今天（UTC）的午夜 —— 和窗口的起点、series 的键同一把尺子。"""
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _days_ago(days: int) -> datetime:
    """``days`` 天前的**当天中午**。

    取中午而不是午夜：午夜正好压在 UTC 日界上，差几秒就是前一天，而这条用例要断言
    的正是「落在哪一天」。中午离两边的界都有 12 小时。
    """
    return _today() - timedelta(days=days) + timedelta(hours=12)


def _mark_agent(client, feedback_id: str) -> None:
    """把一行标成 agent 提的（服务层在提案路径上会写，这里直接落库）。

    和 `_stamp_feedback` 同一套写法：**必须写在 `client` 那个库**
    （`client.test_factory`），不能开一个新的 session —— 见文件头那段
    关于两个库的警告。
    """
    fid = uuid.UUID(feedback_id)

    async def _go() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(Feedback).where(Feedback.id == fid).values(author_is_agent=True)
            )
            await session.commit()

    asyncio.run(_go())


def _stamp_feedback(client, at: datetime, *feedback_ids: str) -> None:
    """把几条反馈的 `created_at` 按到 ``at``。写在 `client` 那个库里。"""
    ids = [uuid.UUID(x) for x in feedback_ids]

    async def _go() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(Feedback).where(Feedback.id.in_(ids)).values(created_at=at)
            )
            await session.commit()

    asyncio.run(_go())


def _stamp_timeline(client, at: datetime, feedback_id: str, status: str) -> None:
    """把某条反馈的某一次状态变迁按到 ``at``。

    按的是 `at` 而不是 `created_at` —— 时间线那一列就叫 `at`，而这一整条用例问的
    正是「什么时候到过这里」。
    """
    fid = uuid.UUID(feedback_id)

    async def _go() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(FeedbackTimeline)
                .where(
                    FeedbackTimeline.feedback_id == fid,
                    FeedbackTimeline.status == FeedbackStatus(status),
                )
                .values(at=at)
            )
            await session.commit()

    asyncio.run(_go())


def _stamp_user(client, at: datetime, handle: str) -> None:
    """把某个账号的注册时间按到 ``at`` —— 「按天新增」那一列读的就是它。"""

    async def _go() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(User).where(User.username == handle).values(created_at=at)
            )
            await session.commit()

    asyncio.run(_go())


def _seed_usage(client, rows: list[tuple[datetime, uuid.UUID, int, float]]) -> None:
    """`(created_at, project_id, total_tokens, cost_usd)` 四元组，直接写一张用量行。

    没有哪条路由能造出这个形状（计量代理写的是 `/v1/messages` 那条路），所以这里直接
    写库 —— 和别的用例直接造行是同一件事。走 ORM 而不是裸 INSERT：`id` 是
    `UuidPk`，由 SQLAlchemy 生成，库里没有默认值。
    """

    async def _go() -> None:
        async with client.test_factory() as session:
            for at, project_id, tokens, cost in rows:
                session.add(
                    ResourceUsage(
                        project_id=project_id,
                        model="m",
                        input_tokens=0,
                        output_tokens=tokens,
                        total_tokens=tokens,
                        cost_usd=cost,
                        kind="chat",
                        route="gateway",
                        created_at=at,
                        updated_at=at,
                    )
                )
            await session.commit()

    asyncio.run(_go())


def _seed_machine_stock(client, owner_handle: str) -> None:
    """一台自托管设备 + 一台热机 —— 「存量」那四个数的样本。

    不需要任何东西真的连上来：这四个计数读的是台账，不是在线状态（`device` 表里
    根本没有那一列）。
    """

    async def _go() -> None:
        async with client.test_factory() as session:
            owner = (
                await session.execute(
                    select(User.id).where(User.username == owner_handle)
                )
            ).scalar_one()
            session.add(
                DeviceRow(
                    device_id="stats-device-1",
                    name="一台设备",
                    token="stats-token-1",
                    owner_user_id=owner,
                    created_at=datetime.now(UTC),
                )
            )
            session.add(WarmMachine(create_request={}, state="preparing"))
            await session.commit()

    asyncio.run(_go())


# --- 造数据走真接口 -----------------------------------------------------------


def _project(client, handle: str) -> str:
    return client.post(
        "/projects", json={"name": "看板项目"}, headers=session_auth_headers(handle)
    ).json()["data"]["id"]


def _report(client, handle: str, **body) -> dict:
    payload = {"title": "按钮点了没反应", **body}
    r = client.post("/feedback", json=payload, headers=session_auth_headers(handle))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _set_status(client, handle: str, feedback_id: str, status: str) -> dict:
    r = client.post(
        f"/admin/feedback/{feedback_id}/status",
        json={"status": status},
        headers=session_auth_headers(handle),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _stats(client, handle: str, kind: str, **params) -> dict:
    r = client.get(
        f"/admin/stats/{kind}", params=params, headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _admin_ids(client, handle: str, **params) -> set[str]:
    r = client.get(
        "/admin/feedback", params=params, headers=session_auth_headers(handle)
    )
    assert r.status_code == 200, r.text
    return {row["id"] for row in r.json()["data"]["data"]}


# --- 反馈 ---------------------------------------------------------------------


def test_feedback_series_counts_the_window_and_fills_the_days_in_between(
    client, as_admin
):
    """窗口内那两天各一天，中间那两天在而且是 0，窗口外那条不在。

    断言整条 series 而不是某一天：只查「今天是不是 1」的话，一个把 7 天画成 2 天、
    或者把窗口外也算进来的实现都能过。
    """
    _report(client, REPORTER)
    three_days_ago = _report(client, REPORTER, title="三天前那条")
    _stamp_feedback(client, _days_ago(3), three_days_ago["id"])
    too_old = _report(client, REPORTER, title="一个月前那条")
    _stamp_feedback(client, _days_ago(30), too_old["id"])

    body = _stats(client, as_admin, "feedback", days=DAYS)

    assert body["days"] == DAYS
    series = body["series"]
    assert len(series) == DAYS
    # 最早的一天在前，最后一天是今天。
    assert series[0]["date"] == (_today() - timedelta(days=DAYS - 1)).date().isoformat()
    assert series[-1]["date"] == _today().date().isoformat()
    # 逐日：第 3 天一条、今天一条；中间那天**在**、值是 0；30 天前那条一天也不占。
    assert [s["created"] for s in series] == [0, 0, 0, 1, 0, 0, 1]
    # total 是全量口径（不收窗口），三条都在里面。
    assert body["total"]["all"] == 3
    assert body["total"]["unassigned"] == 3


def test_the_admin_board_counts_every_visibility_not_just_public(client, as_admin):
    """**这是这次要钉的那个 bug**：看板此前走 `public_counts`（被 `PUBLIC_ONLY`
    收窄），于是私密、Agent 发现、安全问题全没进数 —— 而旁边的 `series` 是全量，
    卡片和曲线各答各的问题，两边各自都看着对。

    这条用四种可见性各建一条，断言四个都进数、并且四栏加起来不等于总数（`agent`
    是来源，和公开/私密重叠 —— 那是筛选，不是划分）。
    """
    # 公开
    _report(client, REPORTER, title="公开那条")
    # 私密
    _report(client, REPORTER, title="私密那条", visibility="private")
    # Agent 发现（来源是 agent，可见性可以是公开/私密任一）。
    # `author_is_agent` 不是创建字段 —— 它由服务层按「提的人是不是 agent」写下来
    # （agent 提案走 `/feedback/proposals`），这里直接改库最省事：这条用例钉的是
    # **计数**会不会漏掉它，不是「怎么成为 agent」。
    agent_row = _report(client, REPORTER, title="Agent 提的")
    _mark_agent(client, agent_row["id"])
    # 安全问题（security 标志，由管理员分诊时打上 —— 走 PATCH，不是别的路）
    sec = _report(client, REPORTER, title="安全那条", visibility="private")
    r = client.patch(
        f"/admin/feedback/{sec['id']}",
        json={"security": True},
        headers=session_auth_headers(as_admin),
    )
    assert r.status_code == 200, r.text

    body = _stats(client, as_admin, "feedback", days=DAYS)

    assert body["total"]["all"] == 4
    # **重叠是故意的**：Agent 那条默认可见性是 public，所以它同时进「公开」和
    # 「Agent 发现」两栏；安全那条被 `security` 从「私密」里挤出去（私密那一栏的判据
    # 和队列一样是 `private AND NOT security`），落进「安全问题」。四栏加起来是 5，
    # 比总数多 1 —— 这就是「筛选不是划分」，页面上那句口径说的也是它。
    assert body["columns"]["public"] == 2
    assert body["columns"]["private"] == 1
    assert body["columns"]["agent"] == 1
    assert body["columns"]["security"] == 1
    assert sum(body["columns"].values()) == body["total"]["all"] + 1
    # 四级状态加起来等于总数（这是划分，和 columns 不同）。
    assert sum(body["status"].values()) == body["total"]["all"]


def test_the_deployed_count_sits_beside_the_resolved_pair(client, as_admin):
    """`deployed` 是一个**另外**给的数，`resolved` 那一栏的口径不动。

    `resolved` 装的是「修复 + 上线」这一对（`_tab_where` 的 docstring 明写着这是决定
    而不是记账错误）。这一条同时把两件事钉住：上线被数出来了，而且它照样算在
    `resolved` 里 —— 谁哪天把 `deployed` 从那个集合里摘出去，这里就红。
    """
    row = _report(client, REPORTER)
    _set_status(client, as_admin, row["id"], "deployed")

    body = _stats(client, as_admin, "feedback", days=DAYS)

    # 三个切口各是一组，不再挤在一个扁平字典里。
    assert set(body["total"]) == {
        "all",
        "open",
        "closed",
        "unassigned",
        "urgent_open",
    }
    assert set(body["columns"]) == {"public", "private", "agent", "security"}
    assert set(body["status"]) == {
        "received",
        "in_progress",
        "resolved",
        "deployed",
    }
    assert body["status"]["deployed"] == 1
    # 「已修复」那一级只数**现在停在 resolved** 的，和用户侧 `resolved` 栏（修复+上线
    # 那一对）不是一个口径 —— 那个是筛选，这个是划分（四级加起来等于 total.all）。
    assert body["status"]["resolved"] == 0
    assert body["total"]["closed"] == 1
    # 上线也是「办完了」，所以它从「还没人管」里出去了 —— 同一个 `CLOSED_STATUSES`。
    assert body["total"]["unassigned"] == 0


def test_a_transition_is_counted_on_its_own_day_and_the_list_filters_by_it(
    client, as_admin
):
    """时间线上的状态变迁：序列按**变迁那天**算，列表筛选也按它。

    窗口是半开的，所以 20 天前那次上线落在 7 天窗口外 —— 序列里一天都不占，而把
    `deployed_since` 放到 30 天前它就回来了。两件事都要断言，只断言「筛得出来」
    的话，一条不过滤的实现照样能过。
    """
    resolved_today = _report(client, REPORTER, title="今天修的")
    _set_status(client, as_admin, resolved_today["id"], "resolved")

    deployed_long_ago = _report(client, REPORTER, title="很久以前上的")
    _set_status(client, as_admin, deployed_long_ago["id"], "deployed")
    _stamp_timeline(client, _days_ago(20), deployed_long_ago["id"], "deployed")

    body = _stats(client, as_admin, "feedback", days=DAYS)

    assert [s["resolved"] for s in body["series"]] == [0, 0, 0, 0, 0, 0, 1]
    # 20 天前那次上线不在 7 天窗口里 —— 少判上界或者不看变迁时间的实现会把它算进来。
    assert [s["deployed"] for s in body["series"]] == [0] * DAYS

    # 列表筛选：`since` 按提交时间收，另外两个按时间线收，别当成一组。
    assert _admin_ids(client, as_admin, deployed_since=_days_ago(30).isoformat()) == {
        deployed_long_ago["id"]
    }
    assert (
        _admin_ids(client, as_admin, deployed_since=_days_ago(7).isoformat()) == set()
    )
    assert _admin_ids(client, as_admin, resolved_since=_days_ago(1).isoformat()) == {
        resolved_today["id"]
    }
    # `since` 问的是提交时间：两条都是今天提的，所以两个都在。
    assert _admin_ids(client, as_admin, since=_days_ago(1).isoformat()) == {
        resolved_today["id"],
        deployed_long_ago["id"],
    }
    assert _admin_ids(client, as_admin, since=_days_ago(-1).isoformat()) == set()


# --- 用量 ---------------------------------------------------------------------


def test_usage_totals_series_and_top_projects_read_the_window(client, as_admin):
    """总量、按天序列、top-N 项目三块读同一个窗口，窗口外那笔不在任何一个里。

    `unpriced_tokens` 单独断言：`cost_usd = 0.0` 的意思是「算不出价钱」，不是免费，
    两笔 token 里有一笔是这种。把它混进 `cost_usd` 或者漏掉，看板上就是几百万
    token 上印一个 $0。
    """
    project = uuid.UUID(_project(client, REPORTER))
    _seed_usage(
        client,
        [
            (_days_ago(0), project, 100, 1.0),
            (_days_ago(0), project, 50, 0.0),
            (_days_ago(3), project, 300, 2.0),
            # 窗口外：`days=7` 收不到它。它要是进来了，下面每个数都会大一圈。
            (_days_ago(30), project, 9999, 99.0),
        ],
    )

    body = _stats(client, as_admin, "usage", days=DAYS)

    assert body["days"] == DAYS
    assert body["totals"] == {
        "tokens": 450,
        "calls": 3,
        "cost_usd": 3.0,
        "unpriced_tokens": 50,
    }
    assert len(body["series"]) == DAYS
    assert [s["tokens"] for s in body["series"]] == [0, 0, 0, 300, 0, 0, 150]
    assert [s["calls"] for s in body["series"]] == [0, 0, 0, 1, 0, 0, 2]
    assert [s["cost_usd"] for s in body["series"]] == [0, 0, 0, 2.0, 0, 0, 1.0]
    assert body["top_projects"] == [
        {
            "project_id": str(project),
            "name": "看板项目",
            "tokens": 450,
            "cost_usd": 3.0,
        }
    ]


# --- 平台 ---------------------------------------------------------------------


def test_platform_reports_accounts_by_day_and_machine_stock(client, as_admin):
    """账号的存量与按天新增、以及设备/机器的**存量**。

    `new` 和 series 里那些 `created` 是同一份读数的两个形状，所以两个断言一起写：
    一个新号落在窗口内、一个落在窗口外、一个就是此刻。
    """
    # 起点先读一遍、后面按差值断言，不写死数字：夹具自己会种一个 agent 账号，而它
    # 也是「窗口内新建的」—— 把它算进来是对的，把它排除掉才是错的。绝对值断言会让
    # 这条用例在夹具改动时红，而红的原因和它要钉的东西无关。
    base = _stats(client, as_admin, "platform", days=DAYS)["people"]
    base_series = [row["created"] for row in base["series"]]

    seed_user(client, "stats-new-a")
    seed_user(client, "stats-new-b")
    seed_user(client, "stats-new-old")
    _stamp_user(client, _days_ago(2), "stats-new-a")
    _stamp_user(client, _days_ago(40), "stats-new-old")
    _seed_machine_stock(client, "stats-new-b")

    body = _stats(client, as_admin, "platform", days=DAYS)

    assert body["days"] == DAYS
    people = body["people"]
    assert people["total"] == base["total"] + 3
    assert people["new"] == base["new"] + 2
    # `admin_handles` 是判据的唯一一处：这一条用例把名单换成了 ADMIN 一个人。
    assert people["admins"] == 1
    # 逐日（在起点那份之上叠加）：两天前一个、今天一个；中间那天**在**、值是 0；
    # 40 天前那个不占任何一天 —— 少了最后这句，不看窗口的实现照样能过。
    deltas = [0, 0, 0, 0, 1, 0, 1]
    assert len(base_series) == len(people["series"]) == DAYS
    assert [s["created"] for s in people["series"]] == [
        before + delta for before, delta in zip(base_series, deltas, strict=True)
    ]
    assert people["series"][-1]["date"] == _today().date().isoformat()
    # 四个数都是台账行数，不是在线数（`device` 表没有那一列，见仓储的 docstring）。
    assert body["machines"] == {
        "devices": 1,
        "hosted_devices": 0,
        "warm_machines": 1,
        "project_machines": 0,
    }


# --- 三条路由都要管理员 -------------------------------------------------------


@pytest.mark.parametrize("kind", ["feedback", "usage", "platform", "performance"])
def test_a_non_admin_cannot_read_any_of_the_three(client, as_admin, kind):
    """四条路由共用 `PlatformAdminDep`，所以四条一起试。

    只试一条的话，「新加的那条忘了挂门」是可想象的一种改法 —— 按分类 parametrize，
    每一类都被问一遍（**加一类就要加一个词**：这一条第一次就是漏了 performance）。
    顺带断言管理员那一侧是 200：403 也可能是路由根本没挂上。
    """
    r = client.get(f"/admin/stats/{kind}", headers=session_auth_headers(STRANGER))
    assert r.status_code == 403, r.text
    assert "data" not in r.json()

    allowed = client.get(f"/admin/stats/{kind}", headers=session_auth_headers(as_admin))
    assert allowed.status_code == 200, allowed.text


# --- 第四类：性能（进程内，不是历史） -----------------------------------------


def test_performance_reads_the_metrics_the_middleware_now_writes(client, as_admin):
    """第四类报的是**这一刻**的接口耗时，而且数来自中间件真的在记的那些。

    三件事一起钉：

    * **它在记**。这一条用例自己刚刚发过请求，所以 `/admin/stats/performance` 必须
      能看到至少一条路由 —— 指标定义了却没人调用，正是这一格坏掉的方式（`/metrics`
      曾经返回 7 个恒为 0 的指标，而全仓找不到一处 `.observe(`）。
    * **标签是路由模板，不是原始路径**。按原始路径打标签的话，每个 UUID 一条时间
      序列，而直方图永远留在进程内存里 —— 这一条要看到 `{` 出现在 route 里。
    * **没有样本的分位数是 `None`，不是 0**。0 是一个读数（「真的很快」），None 是
      「这一格没有数据」；画成同一个数会让一条没人访问过的路由以 0ms 排在最前面。
    """
    # 先制造一次真实流量（打一条读接口），这样注册表里一定有东西。
    assert client.get("/feedback/meta").status_code == 200

    r = client.get("/admin/stats/performance", headers=session_auth_headers(as_admin))
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    # 这一类的口径必须写在响应里，否则会被当成「整个平台的、有历史的」数。
    assert data["routes_registered"] >= data["routes_with_samples"] >= 1
    assert data["routes"], data
    assert isinstance(data["uptime_seconds"], (int, float))
    assert data["active_requests"] >= 0
    assert "recent_ms" in data["loop_lag"]

    row = data["routes"][0]
    assert row["method"] and row["route"]
    assert row["count"] >= 1
    assert set(row) >= {
        "method",
        "route",
        "status",
        "count",
        "error_count",
        "p50",
        "p95",
        "p99",
        "spark",
    }
    # 状态码是**属性**不是身份：一行里就是一个端点的 2xx/3xx/4xx/5xx 各多少。
    assert set(row["status"]) == {"2xx", "3xx", "4xx", "5xx"}

    # **每一条注册过的端点都占一行**，没被访问过的也在（`count: 0`、分位数 None）——
    # 这正是「很多 api 都没显示」的那件事：老实现只列有样本的前 12 条。
    assert data["routes_registered"] >= 50, data["routes_registered"]
    never_hit = [r for r in data["routes"] if r["count"] == 0]
    assert never_hit, "至少应有一条从未被访问的路由占着一行"
    assert all(r["p95"] is None for r in never_hit), never_hit[:3]

    # **数值**也要看一眼，不能只看「有这个键」。这一条是补的：`quantile` 曾经把
    # 逐桶的计数当成累加的，于是样本落在两个以上桶里的路由 p95 永远返回最后一个桶的
    # 上界 —— 也就是每条接口都报 10 秒 —— 而这一条用例当时只断言形状，一路绿着过去。
    # 这里的请求是本机打本机，界取得很宽（5 秒），它拦不住较真，但拦得住「返回了桶的
    # 上界」这一类。数值本身由 `test_core_utils` 那条按已知分布断言。
    assert row["p50"] is not None and 0 <= row["p50"] < 5000
    assert row["p95"] is not None and 0 <= row["p95"] < 5000

    # 路由模板：至少有一条带参数的路由是 `{...}` 而不是一个真 uuid。这条用例自己
    # 打的都是固定路径，所以另发一条带 id 的（404 也算流量，中间件照样记）。
    client.get("/feedback/00000000-0000-4000-8000-000000000000")
    again = client.get(
        "/admin/stats/performance", headers=session_auth_headers(as_admin)
    )
    routes = [row["route"] for row in again.json()["data"]["routes"]]
    assert any("{" in route for route in routes), routes
    assert not any("0000-4000-8000" in route for route in routes), routes

    # **没进路由表的那些路径也只能占一个标签**。它们不是用户流量，是扫描器和拼错的
    # 地址 —— 路径由外面随手写，按原始路径打标签等于让公网决定这个进程内存里长多少
    # 条时间序列。上面那条带上 uuid 的走的是**匹配上的**路由（`/feedback/{id}` 存在），
    # 所以这一条另打几条**谁也匹配不上**的。
    for word in ("zzz-one", "zzz-two", "zzz-three"):
        assert client.get(f"/{word}/inspect.php").status_code == 404
    after = client.get(
        "/admin/stats/performance", headers=session_auth_headers(as_admin)
    )
    labels = [row["route"] for row in after.json()["data"]["routes"]]
    assert not any("zzz" in label for label in labels), labels
    assert labels.count("(unmatched)") <= 1, labels

    # **正在处理的请求数扣掉了读它的这一条**。这条用例是串行打的，所以取快照的这一刻
    # 除了它自己之外没有任何请求在飞 —— 报 0 才是真的 0，报 1 会让空闲的平台看着像
    # 「有一条请求一直没处理完」。
    assert after.json()["data"]["active_requests"] == 0


# --- 上一窗口合计（prev）：环比差的数据源 -------------------------------------
#
# `prev` 是 `[since-days, since)` 这个上一等长窗口的**同一口径**合计 —— KPI 卡的
# 「较上周期 ±%」从这里出。这一组钉三件事，每一件都是一种能悄悄上线的坏法：
#
# * **跨 UTC 日界的种子必须落在 prev 那一侧**。7 天窗口的 prev 是再往前 7 天，
#   边界挪几个小时（按本地时区切天、或写成 6 天）就会把它算进当前窗口或两个窗口外。
# * **prev 的口径和当前窗口同一把尺子**。两边各写一份判据的话，环比比的是两个
#   不同的数 —— 比如「解决」在曲线上是「几条反馈到过 resolved」（distinct），
#   在 prev 上变成「时间线上有几行」。
# * **prev 为 0 时字段在、值是 0**，不是整个键缺席 —— 前端按「键在就画 delta」
#   接线，键缺席和「上一周期是零」在屏幕上必须长得不一样（后者也不画 delta，
#   但那是 `prev=0` 的语义，不是「后端还没这个字段」）。


def _room_with_card(client, *, decided_at: datetime) -> None:
    """一张落在 ``decided_at`` 决议的采纳卡 —— 北极星环比的样本。

    用例钉的是**计数**，不走递卡流程（同 `test_admin_stats_new_sections.py` 的
    `_room_with_cards`：直接插行最快也最准）。
    """

    async def _seed() -> None:
        async with client.test_factory() as s:
            project = Project(
                name=f"prev-{uuid.uuid4().hex[:8]}", owner_handle=REPORTER
            )
            s.add(project)
            await s.flush()
            room = Topic(
                project_id=project.id,
                title="prev 用例",
                kind=TopicKind.topic,
                created_at=decided_at,
            )
            s.add(room)
            await s.flush()
            s.add(
                AcceptCard(
                    topic_id=room.id,
                    reviewer_handle=REPORTER,
                    routing_reason="最懂",
                    status=AcceptStatus.accepted,
                    decided_at=decided_at,
                    created_at=decided_at,
                )
            )
            await s.commit()

    asyncio.run(_seed())


def test_feedback_prev_counts_the_previous_window_with_the_same_ruler(client, as_admin):
    """prev 窗口里新建一条、解决一条；当前窗口各一条；两个窗口外一条。

    断言 `prev` 只收上一窗口那两个，而且「解决」数的是反馈条数（distinct），
    不是时间线行数 —— 后者是同一条反馈改来改去时会虚高的那份。
    """
    # 当前窗口：新建一条、今天解决一条。
    current = _report(client, REPORTER, title="本窗口新建")
    _set_status(client, as_admin, current["id"], "resolved")
    # 上一窗口（7 天窗口的 prev 是第 8–14 天）：新建一条 + 解决一条。
    prev_created = _report(client, REPORTER, title="上一窗口新建")
    _stamp_feedback(client, _days_ago(10), prev_created["id"])
    prev_resolved = _report(client, REPORTER, title="上一窗口解决")
    _set_status(client, as_admin, prev_resolved["id"], "resolved")
    _stamp_timeline(client, _days_ago(9), prev_resolved["id"], "resolved")
    # 两个窗口外：谁都不该收它。
    too_old = _report(client, REPORTER, title="一个月前")
    _stamp_feedback(client, _days_ago(30), too_old["id"])

    body = _stats(client, as_admin, "feedback", days=DAYS)

    # prev_created 的创建落在 prev；prev_resolved 的**创建**落在当前（今天提的），
    # 只有它的解决落在 prev —— 两个计数各按各自的时间轴，这正是「同一把尺子」。
    assert body["prev"] == {"created": 1, "resolved": 1}


def test_feedback_prev_is_present_and_zero_when_the_previous_window_is_empty(
    client, as_admin
):
    """上一窗口什么都没有时，`prev` 在、值是 0 —— 前端据此不画 delta。

    键整个缺席是另一回事（旧后端还没有这个字段），两者在屏幕上必须分得开。
    """
    _report(client, REPORTER, title="只有当前窗口这条")

    body = _stats(client, as_admin, "feedback", days=DAYS)

    assert body["prev"] == {"created": 0, "resolved": 0}


def test_usage_prev_totals_read_the_previous_window(client, as_admin):
    """用量 prev 是 `platform_totals` 平移一个窗口 —— 同一口径的三个数。

    prev 里没有 `unpriced_tokens`：环比那三张卡是 token / 调用 / 成本，prev 的
    形状与它们一一对应。
    """
    project = uuid.UUID(_project(client, REPORTER))
    _seed_usage(
        client,
        [
            (_days_ago(0), project, 100, 1.0),
            # 上一窗口（第 8 天）的两笔：一笔有价、一笔算不出价。
            (_days_ago(8), project, 200, 2.0),
            (_days_ago(8), project, 50, 0.0),
            # 两个窗口外。
            (_days_ago(30), project, 9999, 99.0),
        ],
    )

    body = _stats(client, as_admin, "usage", days=DAYS)

    assert body["prev"] == {"tokens": 250, "calls": 2, "cost_usd": 2.0}


def test_platform_prev_new_counts_accounts_created_in_the_previous_window(
    client, as_admin
):
    """`people.prev_new`：上一窗口新增的账号数，和 `new` 同一把尺子。"""
    # 夹具自己会种一个 agent 账号（落在当前窗口），所以起点先读一遍、后面按差值
    # 断言 —— 同 `test_platform_reports_accounts_by_day_and_machine_stock` 的理由：
    # 绝对值断言会在夹具改动时红，而红的原因和这条用例要钉的东西无关。
    before = _stats(client, as_admin, "platform", days=DAYS)["people"]

    seed_user(client, "stats-prev-window")
    _stamp_user(client, _days_ago(9), "stats-prev-window")

    body = _stats(client, as_admin, "platform", days=DAYS)

    assert body["people"]["prev_new"] == before["prev_new"] + 1
    # 当前窗口的 `new` 不该把上一窗口那个算进去：它先落进当前窗口（`seed_user`
    # 的 created_at 是此刻）、再被按走 —— 一增一减，`new` 回到起点才对。
    assert body["people"]["new"] == before["new"]


def test_product_prev_total_counts_cards_decided_in_the_previous_window(
    client, as_admin
):
    """北极星：total 数当前窗口决议的卡，prev_total 数上一窗口的。

    顺带钉住 `total` 是**窗口口径**（和 series、和卡片标签同一把尺子）：一个
    全量合计夹在两个窗口口径中间，环比比的就是两个不同的数。
    """
    _room_with_card(client, decided_at=_days_ago(2))  # 当前窗口
    _room_with_card(client, decided_at=_days_ago(9))  # 上一窗口
    _room_with_card(client, decided_at=_days_ago(30))  # 两个窗口外

    body = _stats(client, as_admin, "product", days=DAYS)

    assert body["north_star"]["total"] == 1
    assert body["north_star"]["prev_total"] == 1
    assert [row["accepted"] for row in body["north_star"]["series"]] == [
        0,
        0,
        0,
        0,
        1,
        0,
        0,
    ]
