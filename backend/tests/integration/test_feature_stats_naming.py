"""功能数据页：智能命名那一页（`/admin/feature-stats/task-naming`）。

这一页不新增任何埋点：钱来自网关那把专用 key（`KeySpec.alias == "topic-naming"`），
动作来自 `task_titles` 这张本来就在的表。所以这里钉的是**读法**——每一件事都是
「算错了页面上看不出来」的那一类：

* **只算命名那把 key 的账**。网关的 `by_key` 里是全平台每一把 key，把别人的流量算
  进来，这一页就从「命名花了多少」变成「平台花了多少」，而两条线都画得出来。
* **问不到网关不等于花了零**。读不到时网关那几个数是 `None`（页面画破折号），不是
  0——0 读作「这个窗口没花钱」，是另一个意思。
* **答了、但这一窗口没有命名流量**才是 0，而那时成功率仍然是「没有值」：一个请求
  都没有，成功率没有分母。
* **答了、上面却没有那把 key** 也是「没有数」，不是 0。没有这把密钥，它花了多少
  就无从谈起；0 说的是「有这把密钥、这一窗口没花钱」。这两句在页面上读起来必须
  不一样。
* **分母**。「人后来改掉了多少」的分母是**有自动标题的任务**；人自己起名的任务不算
  平台被改掉，没被自动命名过的任务也不该出现在分母里。
* **窗口**。折线恒有 `days` 个点、缺的那天补 0；7 天看不见 40 天前那一行。

网关一律打桩（`httpx.MockTransport` 装进 `httpx.AsyncClient`，同
`test_task_naming.py` 的做法）——测试不该打到真网关上。写数据走
`client.test_factory` 那个库。
"""

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.core.config import settings
from app.domain.feature_stats.features import task_naming
from app.domain.room_task import naming
from app.domain.room_task.models import TaskTitle, TaskTitleSource
from tests.conftest import seed_user
from tests.integration.conftest import post_project, session_auth_headers

ADMIN = "naming-stats-admin"
STRANGER = "naming-stats-stranger"

#: 命名那把 key 与「别人的」一把 key。两个值都一眼能认出来，好断言页面上来的到底是
#: 哪一份——真把平台总量算进来，数字会大得藏不住。
NAMING_HASH = "hash-topic-naming"
OTHER_HASH = "hash-some-project"


@pytest.fixture
def as_admin(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "platform_admin_handles", [ADMIN])
    return ADMIN


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch):
    """两个模块级的缓存跨用例活着，不清就是上一条用例的数；任务是经 HTTP 建的，
    顺手把命名那条后台任务摘掉——它不该在别的用例背后往库里写标题。"""
    task_naming.forget()
    monkeypatch.setattr(naming, "nudge", lambda *args, **kwargs: None)
    yield
    task_naming.forget()


def _today() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _days_ago(days: int) -> datetime:
    """A moment inside the UTC day ``days`` back — same bucket, never midnight."""
    return _today() - timedelta(days=days) + timedelta(hours=1)


def _day_of(days: int) -> date:
    return (_today() - timedelta(days=days)).date()


# ---------- 网关打桩 ----------


def _metrics(
    *, spend: float, requests: int, failed: int, total: int, prompt: int = 0
) -> dict:
    return {
        "spend": spend,
        "api_requests": requests,
        "failed_requests": failed,
        "prompt_tokens": prompt,
        "completion_tokens": total - prompt,
        "cache_read_input_tokens": 0,
        "total_tokens": total,
    }


def _naming_day(spend: float = 0.12, requests: int = 3, failed: int = 1) -> dict:
    """One day of the naming key: three calls, one of them failed, 1,500 tokens."""
    return _metrics(
        spend=spend, requests=requests, failed=failed, total=1500, prompt=900
    )


def _other_day(spend: float = 500.0, requests: int = 100_000) -> dict:
    """The same day on somebody else's key — a hundred thousand requests, in case the
    page ever starts summing the whole platform."""
    return _metrics(spend=spend, requests=requests, failed=0, total=999_999, prompt=1)


def _key_row(
    *,
    alias: str = task_naming.KEY_ALIAS,
    token: str = NAMING_HASH,
    spend: float = 1.25,
    max_budget: float | None = 10.0,
    budget_duration: str | None = "30d",
) -> dict:
    return {
        "token": token,
        "key_alias": alias,
        "user_id": None,
        "spend": spend,
        "max_budget": max_budget,
        "budget_duration": budget_duration,
        "blocked": False,
        "created_at": "2026-09-01T00:00:00Z",
    }


def _usage_day(
    *, naming_metrics: dict | None, other_metrics: dict | None = None
) -> dict:
    """One entry of `/user/daily/activity/aggregated`. ``naming_metrics=None`` 表示
    这一天命名那把 key 在网关的 `by_key` 里根本没有（没有流量时网关就是这样）。"""
    api_keys: dict[str, dict] = {}
    if naming_metrics is not None:
        api_keys[NAMING_HASH] = {"metrics": naming_metrics}
    if other_metrics is not None:
        api_keys[OTHER_HASH] = {"metrics": other_metrics}
    return {
        "date": _day_of(0).isoformat(),
        "metrics": _metrics(spend=0.0, requests=0, failed=0, total=0),
        "breakdown": {"model_groups": {}, "api_keys": api_keys},
    }


def _install_gateway(
    monkeypatch: pytest.MonkeyPatch, keys: list[dict], days: list[dict]
):
    """Point `settings` at a stubbed admin API and return a switch for 「连不上」."""

    class Switch:
        unreachable = False
        #: 打过的路径，用来钉「没问过的就别编」。见没有命名 key 那条用例。
        paths: list[str] = []

    switch = Switch()

    def handler(request: httpx.Request) -> httpx.Response:
        switch.paths.append(request.url.path)
        if switch.unreachable:
            raise httpx.ConnectError("no route to the gateway")
        if request.url.path == "/key/list":
            return httpx.Response(200, json={"keys": keys, "total_pages": 1})
        if request.url.path == "/user/daily/activity/aggregated":
            return httpx.Response(200, json={"results": days, "metadata": {}})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    class Stubbed(real):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Stubbed)
    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-master")
    return switch


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch):
    """A gateway that answered: the naming key did something, somebody else did more."""
    return _install_gateway(
        monkeypatch,
        [
            _key_row(),
            _key_row(alias="project-x", token=OTHER_HASH, spend=999.0, max_budget=None),
        ],
        [_usage_day(naming_metrics=_naming_day(), other_metrics=_other_day())],
    )


def _no_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_gateway_admin_base", None)
    monkeypatch.setattr(settings, "llm_gateway_admin_key", None)


# ---------- 房间与标题 ----------


def _login(client) -> dict[str, str]:
    return {"Authorization": f"Bearer {seed_user(client, 'alice')}"}


def _tasks(client, count: int) -> list[uuid.UUID]:
    """``count`` tasks of one project, each with a name a person gave it.

    The title text does not matter — the ``task_titles`` rows below carry the
    source and the moment this page reads. The tasks are real because
    ``task_titles.task_id`` is a foreign key.
    """
    headers = _login(client)
    created = post_project(client, json={"name": "P"}, headers=headers, owner="alice")
    assert created.status_code == 200, created.text
    room = created.json()["data"]["root_topic_id"]
    out = []
    for index in range(count):
        task = client.post(
            f"/topics/{room}/tasks", json={"title": f"任务 {index}"}, headers=headers
        )
        assert task.status_code == 200, task.text
        out.append(uuid.UUID(task.json()["data"]["id"]))
    return out


def _seed_titles(client, *rows: dict) -> None:
    async def _seed() -> None:
        async with client.test_factory() as s:
            for spec in rows:
                s.add(TaskTitle(**spec))
            await s.commit()

    asyncio.run(_seed())


def _auto(task: uuid.UUID, *, stage: str = "name", days: int = 1) -> dict:
    return {
        "task_id": task,
        "title": f"自动 {stage}",
        "source": TaskTitleSource.auto,
        "reason": stage,
        "created_at": _days_ago(days),
    }


def _human(task: uuid.UUID, *, reason: str = "rename", days: int = 1) -> dict:
    return {
        "task_id": task,
        "title": f"人写的 {reason}",
        "source": TaskTitleSource.human,
        "reason": reason,
        "by": "alice",
        "created_at": _days_ago(days),
    }


def _report(client, admin: str, **params) -> dict:
    r = client.get(
        "/admin/feature-stats/task-naming",
        params=params,
        headers=session_auth_headers(admin),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


# ---------- 门与目录 ----------


def test_the_catalogue_offers_the_naming_page(client, as_admin):
    body = client.get(
        "/admin/feature-stats", headers=session_auth_headers(as_admin)
    ).json()["data"]

    ids = [feature["id"] for feature in body["features"]]
    assert "task-naming" in ids


def test_a_stranger_cannot_read_the_report(client, as_admin):
    headers = session_auth_headers(STRANGER)
    assert (
        client.get("/admin/feature-stats/task-naming", headers=headers).status_code
        == 403
    )


def test_days_is_bounded(client, as_admin):
    headers = session_auth_headers(as_admin)
    for days in (0, 91):
        assert (
            client.get(
                "/admin/feature-stats/task-naming",
                params={"days": days},
                headers=headers,
            ).status_code
            == 400
        )


# ---------- 钱：只算命名那把 key ----------


def test_the_numbers_are_the_naming_keys_and_nobody_elses(client, as_admin, gateway):
    """网关那一份里还有别人的 key，页面上只许出现命名那一份。"""
    numbers = _report(client, as_admin, days=30)["numbers"]

    assert numbers["calls"] == {
        "value": 3,
        "failed": 1,
        "success_rate": pytest.approx(2 / 3),
    }
    assert numbers["tokens"] == {
        "value": 1500,
        "prompt": 900,
        "completion": 600,
        "cache_read": 0,
    }
    assert numbers["cost"]["usd"] == pytest.approx(0.12)
    assert numbers["cost"]["source"] == "gateway"


def test_two_days_of_the_window_are_added_up(client, as_admin, monkeypatch):
    """窗口是两个日期之间的一整段，不是网关默认的那一天。"""
    switch = _install_gateway(
        monkeypatch,
        [_key_row()],
        [
            _usage_day(naming_metrics=_naming_day(spend=0.10, requests=2, failed=1)),
            _usage_day(naming_metrics=_naming_day(spend=0.02, requests=1, failed=0)),
        ],
    )
    assert switch is not None

    numbers = _report(client, as_admin, days=30)["numbers"]

    assert numbers["calls"]["value"] == 3
    assert numbers["calls"]["failed"] == 1
    assert numbers["cost"]["usd"] == pytest.approx(0.12)


def test_the_key_budget_rides_next_to_the_spend(client, as_admin, monkeypatch):
    """额度是那个会静默让命名停下的东西，得和花费挨着画。"""
    _install_gateway(
        monkeypatch,
        [_key_row(spend=1.25, max_budget=10.0, budget_duration="30d")],
        [_usage_day(naming_metrics=_naming_day())],
    )

    cost = _report(client, as_admin, days=30)["numbers"]["cost"]

    assert cost["budget_usd"] == pytest.approx(10.0)
    assert cost["budget_duration"] == "30d"
    assert cost["key_spend_usd"] == pytest.approx(1.25)


def test_a_gateway_we_cannot_ask_is_null_not_zero(client, as_admin, monkeypatch):
    """网关没配：数字是「没读到」，不是「零」。动作那一半照常。"""
    _no_gateway(monkeypatch)
    (task,) = _tasks(client, 1)
    _seed_titles(client, _auto(task))

    numbers = _report(client, as_admin, days=30)["numbers"]

    assert numbers["calls"] == {"value": None, "failed": None, "success_rate": None}
    assert numbers["tokens"] == {
        "value": None,
        "prompt": None,
        "completion": None,
        "cache_read": None,
    }
    assert numbers["cost"]["usd"] is None
    assert numbers["cost"]["source"] == "unavailable"
    assert numbers["cost"]["budget_usd"] is None
    # 动作那一半读的是库，读不到网关不影响它。
    assert numbers["renames"]["value"] == 1


def test_a_gateway_that_errors_is_also_unknown_not_zero(client, as_admin, gateway):
    """连不上：同一个 `None` 集合，不是一排 0（一排 0 读作「这个月没花钱」）。"""
    gateway.unreachable = True
    (task,) = _tasks(client, 1)
    _seed_titles(client, _auto(task))

    numbers = _report(client, as_admin, days=30)["numbers"]

    assert numbers["calls"]["value"] is None
    assert numbers["cost"]["usd"] is None
    assert numbers["cost"]["source"] == "unavailable"
    assert numbers["renames"]["value"] == 1


def test_a_gateway_that_answered_with_no_naming_traffic_is_zero(
    client, as_admin, monkeypatch
):
    """网关答了话、key 在、这一窗口命名一个请求都没有：**这才是 0**。

    和上一条配对：这条能报 0，判据不是「数字看着像零」，而是那把 key 确实存在
    （额度那一行画得出来）。成功率仍然是「没有值」——一个请求都没有，没有分母。
    """
    _install_gateway(
        monkeypatch,
        [_key_row()],
        # 没有流量时，网关的 by_key 里根本没有这张 key。
        [_usage_day(naming_metrics=None, other_metrics=_other_day())],
    )

    numbers = _report(client, as_admin, days=30)["numbers"]

    assert numbers["cost"]["source"] == "gateway"
    assert numbers["cost"]["budget_usd"] == pytest.approx(10.0)
    assert numbers["calls"]["value"] == 0
    assert numbers["calls"]["success_rate"] is None
    assert numbers["tokens"]["value"] == 0
    assert numbers["cost"]["usd"] == pytest.approx(0.0)


def test_a_gateway_without_the_naming_key_is_unknown_not_zero(
    client, as_admin, monkeypatch
):
    """网关答了话、上面却没有那把 key（命名从没被铸过，或者别名变了）。

    **这是「没有数」，不是 0**：0 说的是「这把密钥在网关那儿有账、这个窗口一次
    没花」，而这里根本没有这把密钥 —— 它花了多少无从谈起。两者在页面上必须读起
    来不一样，否则读到的是「命名一分钱没花」。
    """
    switch = _install_gateway(
        monkeypatch, [_key_row(alias="project-x", token=OTHER_HASH)], []
    )
    (task,) = _tasks(client, 1)
    _seed_titles(client, _auto(task))

    numbers = _report(client, as_admin, days=30)["numbers"]

    assert numbers["cost"]["source"] == "no-key"
    assert numbers["calls"] == {"value": None, "failed": None, "success_rate": None}
    assert numbers["tokens"]["value"] is None
    assert numbers["cost"]["usd"] is None
    assert numbers["cost"]["budget_usd"] is None
    # 没有这把 key 就没有它的用量可问：连那次请求都不该发出去。
    assert "/user/daily/activity/aggregated" not in switch.paths
    # 库里那一半照常：网关读不到什么，不影响「写了多少、被改掉多少」。
    assert numbers["renames"]["value"] == 1


# ---------- 动作：标题落在哪几个阶段、被谁改掉 ----------


def _stage_fixture(client) -> None:
    """Four tasks covering every bucket the page counts.

    ``one`` 自动命名、校准、跟随，然后被一个人改掉；``two`` 只被自动命名过；
    ``three`` 是人自己起的名字，平台从没命名过；``four`` 是芝士提议的标题，后来
    被人改掉了。
    """
    one, two, three, four = _tasks(client, 4)
    _seed_titles(
        client,
        _auto(one, stage="name", days=4),
        _auto(one, stage="calibrate", days=3),
        _auto(one, stage="follow", days=2),
        _human(one, reason="rename", days=0),
        _auto(two, stage="name", days=3),
        _human(three, reason="rename", days=3),
        _auto(four, stage="proposal", days=3),
        _human(four, reason="rename", days=1),
    )


def test_titles_are_counted_by_stage_and_by_who_wrote_them(
    client, as_admin, monkeypatch
):
    _no_gateway(monkeypatch)
    _stage_fixture(client)

    numbers = _report(client, as_admin, days=30)["numbers"]

    # 提议时的标题算平台的，但不属于三个阶段里的任何一个。
    assert numbers["renames"] == {"value": 5, "name": 2, "calibrate": 1, "follow": 1}
    assert numbers["person_edits"] == {"value": 3}


def test_the_override_share_counts_tasks_a_person_touched_after_the_platform(
    client, as_admin, monkeypatch
):
    """分母是**有自动标题的任务**：人自己起名的任务不进分母，没被改的不进分子。"""
    _no_gateway(monkeypatch)
    _stage_fixture(client)

    overridden = _report(client, as_admin, days=30)["numbers"]["overridden"]

    # one 与 four 被改掉了；two 没有；three 平台没命名过，不算。
    assert overridden == {"value": 2, "named": 3, "share": pytest.approx(2 / 3)}


def test_a_share_with_no_denominator_is_null(client, as_admin, monkeypatch):
    """一个自动标题都没有：比例是「没有值」，不是 0%。"""
    _no_gateway(monkeypatch)
    (task,) = _tasks(client, 1)
    _seed_titles(client, _human(task, reason="rename", days=1))

    overridden = _report(client, as_admin, days=30)["numbers"]["overridden"]

    assert overridden == {"value": 0, "named": 0, "share": None}


# ---------- 折线与窗口 ----------


def test_the_trend_is_dense_over_the_window(client, as_admin, monkeypatch):
    """折线恒有 days 个点、最早在前、缺的那天是 0（判据是窗口，不是有数据的天）。"""
    _no_gateway(monkeypatch)
    one, two = _tasks(client, 2)
    _seed_titles(
        client,
        _auto(one, stage="name", days=2),
        _auto(two, stage="name", days=2),
        _human(one, reason="rename", days=0),
    )

    trend = _report(client, as_admin, days=7)["trend"]

    assert len(trend) == 7
    assert trend[0]["date"] == _day_of(6).isoformat()
    assert trend[-1]["date"] == _day_of(0).isoformat()
    by_day = {row["date"]: row for row in trend}
    assert by_day[_day_of(2).isoformat()] == {
        "date": _day_of(2).isoformat(),
        "auto": 2,
        "person": 0,
    }
    assert by_day[_day_of(0).isoformat()] == {
        "date": _day_of(0).isoformat(),
        "auto": 0,
        "person": 1,
    }
    # 中间那天没有任何标题落下来 —— 是 0，不是缺行。
    assert by_day[_day_of(1).isoformat()] == {
        "date": _day_of(1).isoformat(),
        "auto": 0,
        "person": 0,
    }


def test_the_window_bounds_what_is_counted(client, as_admin, monkeypatch):
    """7 天看不见 40 天前的行，90 天看得见。"""
    _no_gateway(monkeypatch)
    (task,) = _tasks(client, 1)
    _seed_titles(
        client,
        _auto(task, stage="name", days=40),
        _human(task, reason="rename", days=1),
    )

    assert _report(client, as_admin, days=7)["numbers"]["renames"]["value"] == 0
    assert _report(client, as_admin, days=7)["numbers"]["person_edits"]["value"] == 1
    assert _report(client, as_admin, days=90)["numbers"]["renames"]["value"] == 1
    assert _report(client, as_admin, days=90)["start"] == _day_of(89).isoformat()
