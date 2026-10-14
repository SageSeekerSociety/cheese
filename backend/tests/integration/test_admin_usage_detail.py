"""用量看板里「谁在用、哪个项目用哪个模型、什么时候、缓存命中多少」。

要钉的规则：

* 缓存命中率 = 缓存读 / 输入，只算记了缓存的行。一类行（通路 + kind）在窗口里第一次
  记下缓存读之前的行，缓存那一栏的 0 可能只是没记（网关路修复之前就是这样），不进
  分母；一次缓存读都没记过的那类行没有命中率，不是 0%。
* 项目里的花费归到触发那一轮的人（发那条消息的人）；项目外的花费归到行上记的人；
  平台自己发起、没有人触发的花费不摊给任何人，单独给出合计。
* 最近的调用最新的在前；每小时的序列只在短窗口给，长窗口不给，而不是给一串 0。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.usage.models import ResourceUsage
from app.domain.user.models import User, UserProfile
from tests.conftest import seed_user
from tests.integration.conftest import post_message, session_auth_headers
from tests.integration.test_admin_stats import (
    DAYS,
    REPORTER,
    _project,
    _stats,
    as_admin,  # noqa: F401 — the fixture, used by name
)
from tests.integration.test_feedback import _seed_profiles

ASKER = "usage-asker"


def _ago(**delta) -> datetime:
    return datetime.now(UTC) - timedelta(**delta)


def _row(**fields) -> ResourceUsage:
    """One ledger row, every token column filled the way the ledger fills it:
    ``input_tokens`` includes the cache reads."""
    at = fields.pop("at", _ago(minutes=5))
    input_tokens = fields.pop("input_tokens", 0)
    output_tokens = fields.pop("output_tokens", 0)
    return ResourceUsage(
        model=fields.pop("model", "m"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cache_read_tokens=fields.pop("cache_read_tokens", 0),
        cost_usd=fields.pop("cost_usd", 0.0),
        kind=fields.pop("kind", "chat"),
        route=fields.pop("route", "gateway"),
        created_at=at,
        updated_at=at,
        **fields,
    )


def _seed(client, *rows: ResourceUsage) -> None:
    async def _go() -> None:
        async with client.test_factory() as session:
            session.add_all(rows)
            await session.commit()

    asyncio.run(_go())


def _user_id(client, handle: str) -> int:
    async def _go() -> int:
        async with client.test_factory() as session:
            return (
                await session.execute(select(User.id).where(User.username == handle))
            ).scalar_one()

    return asyncio.run(_go())


def _named(client, handle: str, nickname: str) -> None:
    """Give ``handle`` the profile name the dashboard shows beside the handle."""

    async def _go() -> None:
        async with client.test_factory() as session:
            user_id = (
                await session.execute(select(User.id).where(User.username == handle))
            ).scalar_one()
            now = datetime.now(UTC)
            session.add(
                UserProfile(
                    user_id=user_id,
                    nickname=nickname,
                    intro="",
                    avatar_id=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    asyncio.run(_go())


def _room(client, project_id: str) -> str:
    response = client.post(
        "/topics",
        json={"project_id": project_id, "title": "用量"},
        headers=session_auth_headers(REPORTER),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


def test_cache_hit_counts_only_rows_that_recorded_the_cache(client, as_admin):  # noqa: F811
    project = uuid.UUID(_project(client, REPORTER))
    _seed(
        client,
        # Gateway turns: the first one landed before the gateway recorded cache
        # reads, so its 0 says nothing.
        _row(
            project_id=project,
            model="gw-model",
            at=_ago(hours=5),
            input_tokens=1000,
        ),
        _row(
            project_id=project,
            model="gw-model",
            at=_ago(hours=1),
            input_tokens=1000,
            cache_read_tokens=900,
        ),
        _row(
            project_id=project,
            model="sub-model",
            route="subscription",
            input_tokens=1000,
            cache_read_tokens=500,
        ),
        # A supply that never recorded a cache read: no ratio, not 0%.
        _row(project_id=project, model="own-model", route="own", input_tokens=100),
        # Outside the window.
        _row(
            project_id=project,
            model="gw-model",
            at=_ago(days=30),
            input_tokens=9999,
            cache_read_tokens=9999,
        ),
    )

    body = _stats(client, as_admin, "usage", days=DAYS)

    assert body["cache"] == {
        "hit_ratio": pytest.approx(0.7),
        "input_tokens": 2000,
        "cache_read_tokens": 1400,
        "uncounted_input_tokens": 1100,
    }
    hits = {row["model"]: row["cache_hit"] for row in body["by_model"]}
    assert hits == {
        "gw-model": pytest.approx(0.9),
        "sub-model": pytest.approx(0.5),
        "own-model": None,
    }
    assert body["top_projects"][0]["cache_hit"] == pytest.approx(0.7)


def test_each_project_lists_the_models_it_used(client, as_admin):  # noqa: F811
    busy = uuid.UUID(_project(client, REPORTER))
    _seed(
        client,
        _row(project_id=busy, model="a", input_tokens=300, cost_usd=3.0),
        _row(project_id=busy, model="b", input_tokens=100, cost_usd=0.5),
        _row(project_id=busy, model="a", input_tokens=200, cost_usd=2.0),
    )

    body = _stats(client, as_admin, "usage", days=DAYS)

    assert body["top_projects"][0]["models"] == [
        {"model": "a", "tokens": 500, "cost_usd": 5.0},
        {"model": "b", "tokens": 100, "cost_usd": 0.5},
    ]


def test_spend_goes_to_whoever_triggered_it_and_the_platform_keeps_its_own(
    client,
    as_admin,  # noqa: F811
):
    project_id = _project(client, REPORTER)
    project = uuid.UUID(project_id)
    said = post_message(
        client, _room(client, project_id), REPORTER, {"content": "跑一下"}
    )
    turn = uuid.UUID(said.get("turn_id") or said["id"])
    seed_user(client, ASKER)
    asker = _user_id(client, ASKER)
    _named(client, REPORTER, "报告人")
    _named(client, ASKER, "提问的人")
    _seed(
        client,
        # Two rows of the turn REPORTER's message started (the gateway lands one
        # per model).
        _row(project_id=project, turn_id=turn, input_tokens=400, cost_usd=4.0),
        _row(project_id=project, turn_id=turn, input_tokens=100, cost_usd=1.0),
        # ASKER's own question outside any project: the row names them.
        _row(
            user_id=asker,
            kind="docs_ask",
            input_tokens=50,
            cost_usd=0.5,
        ),
        # A turn the platform started (a resend, a dream): nobody's message.
        _row(project_id=project, turn_id=uuid.uuid4(), input_tokens=70, cost_usd=0.7),
        _row(project_id=project, input_tokens=30, cost_usd=0.3),
    )

    body = _stats(client, as_admin, "usage", days=DAYS)

    people = [
        (row["handle"], row["name"], row["tokens"], row["calls"], row["cost_usd"])
        for row in body["top_users"]
    ]
    assert people == [
        (REPORTER, "报告人", 500, 2, pytest.approx(5.0)),
        (ASKER, "提问的人", 50, 1, pytest.approx(0.5)),
    ]
    # 这一屏也画脸：这一组没人挑过头像、也没有队友，两栏都该是「没有」，不是默认图。
    assert [(row["avatar_id"], row["is_agent"]) for row in body["top_users"]] == [
        (None, False),
        (None, False),
    ]
    assert body["unattributed"] == {
        "tokens": 100,
        "calls": 2,
        "cost_usd": pytest.approx(1.0),
    }


def test_a_top_user_who_picked_an_avatar_reports_its_id(client, as_admin):  # noqa: F811
    """触发榜上挑过头像的人给素材 id：头像在档案上，不解析就永远没有。"""
    ids = _seed_profiles(client, {"usage-picker": "predefined"})
    uid = _user_id(client, "usage-picker")
    _seed(client, _row(user_id=uid, kind="docs_ask", input_tokens=10, cost_usd=0.1))

    body = _stats(client, as_admin, "usage", days=DAYS)

    row = next(r for r in body["top_users"] if r["handle"] == "usage-picker")
    assert row["avatar_id"] == ids["usage-picker"]
    assert row["is_agent"] is False


def test_recent_calls_come_newest_first_with_who_and_where(client, as_admin):  # noqa: F811
    project_id = _project(client, REPORTER)
    project = uuid.UUID(project_id)
    said = post_message(
        client, _room(client, project_id), REPORTER, {"content": "再跑"}
    )
    turn = uuid.UUID(said.get("turn_id") or said["id"])
    _seed(
        client,
        _row(project_id=project, model="old", at=_ago(hours=3), input_tokens=10),
        _row(
            project_id=project,
            turn_id=turn,
            model="new",
            at=_ago(minutes=1),
            input_tokens=1000,
            output_tokens=20,
            cache_read_tokens=800,
            cost_usd=0.25,
        ),
    )

    recent = _stats(client, as_admin, "usage", days=DAYS)["recent"]

    assert [row["model"] for row in recent] == ["new", "old"]
    newest = recent[0]
    assert (
        newest["project_id"],
        newest["project"],
        newest["handle"],
        newest["route"],
        newest["input_tokens"],
        newest["output_tokens"],
        newest["cache_read_tokens"],
        newest["cost_usd"],
    ) == (project_id, "看板项目", REPORTER, "gateway", 1000, 20, 800, 0.25)
    assert newest["at"].endswith("+08:00")
    # Before the first recorded cache read of its kind, a row's 0 is unknown.
    assert recent[1]["cache_read_tokens"] is None


def test_hourly_usage_is_given_in_beijing_time_for_a_short_window_only(
    client,
    as_admin,  # noqa: F811
):
    project = uuid.UUID(_project(client, REPORTER))
    at = _ago(hours=2).replace(minute=30, second=0, microsecond=0)
    _seed(client, _row(project_id=project, at=at, input_tokens=42, cost_usd=0.1))

    short = _stats(client, as_admin, "usage", days=DAYS)
    longer = _stats(client, as_admin, "usage", days=30)

    hours = short["hourly"]
    beijing = timedelta(hours=8)
    seeded = (at.replace(minute=0) + beijing).strftime("%Y-%m-%dT%H:00:00+08:00")
    assert [h["tokens"] for h in hours if h["hour"] == seeded] == [42]
    assert sum(h["tokens"] for h in hours) == 42
    # Up to the hour that is running now, never into hours that have not happened.
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + beijing
    assert hours[-1]["hour"] == now.strftime("%Y-%m-%dT%H:00:00+08:00")
    assert longer["hourly"] is None
