"""`GET /throughput/seats`: 每个席位生成得有多快，按席位 × 模型 × 供给分开。

要钉的规则：

* 口径是**窗口吞吐**：`output_tokens ÷ 窗口秒数`，把空档也算进分母。按轮速率
  （一轮的 token 除以这一轮的秒数）2026-10-15 拆掉了：网关那一层的分子是整把
  虚拟 key 自上次结算以来的增量，不是这一轮的产出，两者配不成一对。
* `output_tokens = 0` 的行不算 —— 那是「这轮没拿到用量」的占位标记，不是实跑。
* 供给（`resource_usage.route`）分开列，所以被限流的席位看得出是速率掉了，
  不是没在跑。
* 还开着的轮次（`stopped_at IS NULL`）不算：它的产出到此刻为止，除以到此刻为止的
  时长会把一个正在跑的轮次报成慢。
* 秒数为 0 的一轮照算 token，秒数按 0 记。
* `calls` 加的是 `sum(calls)`，不是行数 —— 网关一栏能把多次调用灌成一行。
* 只在平台自己的项目里读得到。
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.domain.agent.models import AgentTurn
from app.domain.usage.models import ResourceUsage
from tests.integration.conftest import (
    post_project,
    room_agent_headers,
    session_auth_headers,
)

DEV = "tps-dev"


def _room(client, project: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project, "title": "快慢"},
        headers=session_auth_headers(DEV),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


@pytest.fixture
def platform_room(client, monkeypatch) -> tuple[str, str]:
    project = post_project(
        client, json={"name": "知是"}, headers=session_auth_headers(DEV)
    ).json()["data"]["id"]
    monkeypatch.setattr(
        settings, "docs_dev_repositories", [f"project-{uuid.UUID(project).hex}/code"]
    )
    return project, _room(client, project)


def _seed(client, room: str, specs: list[dict]) -> list[uuid.UUID]:
    """轮次，以及挂在这几轮上的用量行。

    一个 spec 是一轮：`seat`、`started_at`、可选 `stopped_at`（缺了就是这一轮还
    开着）、`usages`（`output_tokens` / `model` / `route` / `calls`，缺的补默认）。
    """
    turn_ids: list[uuid.UUID] = []

    async def write() -> None:
        async with client.test_factory() as session:
            for spec in specs:
                turn_id = uuid.uuid4()
                turn_ids.append(turn_id)
                started = spec["started_at"]
                stopped = spec.get("stopped_at")
                session.add(
                    AgentTurn(
                        id=turn_id,
                        conversation_id=uuid.UUID(room),
                        continuation_id=turn_id,
                        author="alice",
                        content="做点事",
                        agent_handle=spec.get("seat"),
                        started_at=started,
                        stopped_at=stopped,
                    )
                )
                for usage in spec.get("usages", []):
                    tokens = usage.get("output_tokens", 0)
                    session.add(
                        ResourceUsage(
                            conversation_id=uuid.UUID(room),
                            turn_id=turn_id,
                            model=usage.get("model", "m"),
                            output_tokens=tokens,
                            input_tokens=usage.get("input_tokens", 0),
                            total_tokens=tokens,
                            route=usage.get("route", "gateway"),
                            calls=usage.get("calls", 1),
                            created_at=started,
                            updated_at=started,
                        )
                    )
            await session.commit()

    client.portal.call(write)
    return turn_ids


def _throughput(client, topic: str, **params) -> dict:
    response = client.get(
        "/throughput/seats",
        params={"topic": topic, **params},
        headers=room_agent_headers(client, topic),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _row(data: dict, **match) -> dict:
    hits = [
        r for r in data["rows"] if all(r[key] == value for key, value in match.items())
    ]
    assert len(hits) == 1, f"expected one row for {match}, got {len(hits)}"
    return hits[0]


def test_a_group_reports_its_turns_its_tokens_and_its_window_throughput(
    client, platform_room
):
    """一组报的是这几轮的合计产出，和它占窗口的吞吐；不报任何按轮的速率。"""
    _, room = platform_room
    now = datetime.now(UTC)
    _seed(
        client,
        room,
        [
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=2),
                "stopped_at": now - timedelta(hours=2) + timedelta(seconds=100),
                "usages": [
                    {
                        "output_tokens": 1000,
                        "model": "claude-sonnet-5",
                        "route": "subscription",
                    }
                ],
            },
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(hours=1) + timedelta(seconds=50),
                "usages": [
                    {
                        "output_tokens": 1000,
                        "model": "claude-sonnet-5",
                        "route": "subscription",
                    }
                ],
            },
        ],
    )

    data = _throughput(client, room, window="24h")

    row = _row(data, seat="cheese", model="claude-sonnet-5", supply="subscription")
    assert row["turns"] == 2
    assert row["output_tokens"] == 2000
    assert row["turn_seconds"] == 150.0
    # 窗口吞吐把空档也算进分母。
    assert row["window_tps"] == round(2000 / 86400, 4)


def test_supply_is_split_so_a_throttled_seat_shows_a_fall_not_silence(
    client, platform_room
):
    """同一个席位同一个模型走两条供给时是两行：掉速的是被限流的那条，另一条照常。"""
    _, room = platform_room
    now = datetime.now(UTC)
    _seed(
        client,
        room,
        [
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=2),
                "stopped_at": now - timedelta(hours=2) + timedelta(seconds=100),
                "usages": [{"output_tokens": 1000, "route": "subscription"}],
            },
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(hours=1) + timedelta(seconds=10),
                "usages": [{"output_tokens": 500, "route": "gateway"}],
            },
        ],
    )

    data = _throughput(client, room, window="24h")

    throttled = _row(data, seat="cheese", model="m", supply="subscription")
    fine = _row(data, seat="cheese", model="m", supply="gateway")
    assert throttled["output_tokens"] == 1000
    assert throttled["window_tps"] == round(1000 / 86400, 4)
    assert fine["output_tokens"] == 500
    assert fine["window_tps"] == round(500 / 86400, 4)
    assert len(data["rows"]) == 2


def test_a_zero_token_filler_row_is_not_a_run(client, platform_room):
    """`output_tokens = 0` 的行是「这轮没拿到用量」的补写标记，放进来会造出假的 0
    速率和假的模型归属。"""
    _, room = platform_room
    now = datetime.now(UTC)
    _seed(
        client,
        room,
        [
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(hours=1) + timedelta(seconds=10),
                "usages": [
                    {"output_tokens": 1000, "model": "真跑的"},
                    # 占位行：model 写的是席位设的模型，不代表实际服务它的模型。
                    {"output_tokens": 0, "model": "席位配置的"},
                ],
            }
        ],
    )

    data = _throughput(client, room, window="24h")

    assert [r["model"] for r in data["rows"]] == ["真跑的"]
    assert data["rows"][0]["output_tokens"] == 1000
    assert data["rows"][0]["window_tps"] == round(1000 / 86400, 4)


def test_a_turn_still_running_is_left_out(client, platform_room):
    """`stopped_at` 空的轮次产出到此刻为止，除以到此刻为止的时长会报成慢。"""
    _, room = platform_room
    now = datetime.now(UTC)
    _seed(
        client,
        room,
        [
            {
                "seat": "cheese",
                "started_at": now - timedelta(minutes=5),
                "usages": [{"output_tokens": 9999}],
            },
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(hours=1) + timedelta(seconds=10),
                "usages": [{"output_tokens": 1000}],
            },
        ],
    )

    data = _throughput(client, room, window="24h")

    assert data["rows"][0]["turns"] == 1
    assert data["rows"][0]["output_tokens"] == 1000
    assert data["rows"][0]["window_tps"] == round(1000 / 86400, 4)


def test_a_zero_length_turn_counts_its_tokens_and_zero_seconds(
    client, platform_room
):
    """起止同一刻的一轮，token 照算，秒数按 0 记 —— 它不额外占轮内时间。"""
    _, room = platform_room
    now = datetime.now(UTC)
    same = now - timedelta(hours=1)
    _seed(
        client,
        room,
        [
            {
                "seat": "cheese",
                "started_at": same,
                "stopped_at": same,
                "usages": [{"output_tokens": 700}],
            },
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=2),
                "stopped_at": now - timedelta(hours=2) + timedelta(seconds=10),
                "usages": [{"output_tokens": 1000}],
            },
        ],
    )

    data = _throughput(client, room, window="24h")

    row = data["rows"][0]
    assert row["turns"] == 2
    assert row["output_tokens"] == 1700
    assert row["turn_seconds"] == 10.0
    assert row["window_tps"] == round(1700 / 86400, 4)


def test_calls_are_summed_not_counted_as_rows(client, platform_room):
    """网关一栏能把多次调用灌成一行，所以调用次数是 `sum(calls)`。"""
    _, room = platform_room
    now = datetime.now(UTC)
    _seed(
        client,
        room,
        [
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(hours=1) + timedelta(seconds=10),
                "usages": [
                    {"output_tokens": 500, "model": "a", "calls": 7},
                    {"output_tokens": 500, "model": "b", "calls": 1},
                ],
            }
        ],
    )

    data = _throughput(client, room, window="24h")

    assert _row(data, model="a")["calls"] == 7
    assert _row(data, model="b")["calls"] == 1
    # 汇总那一行也照 `calls` 加，不是数几行。
    assert data["seats"][0]["calls"] == 8
    assert data["seats"][0]["turns"] == 1


def test_the_seat_rollup_lumps_every_model_and_supply(client, platform_room):
    """「每个席位有多快」是一句话的事，所以席位汇总把模型和供给都归进去。"""
    _, room = platform_room
    now = datetime.now(UTC)
    _seed(
        client,
        room,
        [
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=3),
                "stopped_at": now - timedelta(hours=3) + timedelta(seconds=100),
                "usages": [
                    {"output_tokens": 1000, "model": "a", "route": "subscription"}
                ],
            },
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=2),
                "stopped_at": now - timedelta(hours=2) + timedelta(seconds=50),
                "usages": [
                    {"output_tokens": 1000, "model": "a", "route": "subscription"}
                ],
            },
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(hours=1) + timedelta(seconds=10),
                "usages": [{"output_tokens": 500, "model": "b", "route": "gateway"}],
            },
            {
                "seat": "cheese-mimo",
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(hours=1) + timedelta(seconds=5),
                "usages": [{"output_tokens": 100, "model": "c", "route": "native"}],
            },
        ],
    )

    data = _throughput(client, room, window="24h")

    seats = {row["seat"]: row for row in data["seats"]}
    assert set(seats) == {"cheese", "cheese-mimo"}
    lump = seats["cheese"]
    assert lump["turns"] == 3
    assert lump["output_tokens"] == 2500
    assert lump["turn_seconds"] == 160.0
    assert lump["window_tps"] == round(2500 / 86400, 4)
    assert seats["cheese-mimo"]["turns"] == 1
    assert seats["cheese-mimo"]["output_tokens"] == 100


def test_the_window_holds_rows_back(client, platform_room):
    _, room = platform_room
    now = datetime.now(UTC)
    _seed(
        client,
        room,
        [
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=2),
                "stopped_at": now - timedelta(hours=2) + timedelta(seconds=10),
                "usages": [{"output_tokens": 1000}],
            },
            {
                "seat": "cheese",
                "started_at": now - timedelta(days=3),
                "stopped_at": now - timedelta(days=3) + timedelta(seconds=10),
                "usages": [{"output_tokens": 9000}],
            },
        ],
    )

    day = _throughput(client, room, window="24h")
    assert day["rows"][0]["turns"] == 1
    assert day["rows"][0]["output_tokens"] == 1000

    week = _throughput(client, room, window="7d")
    assert week["rows"][0]["turns"] == 2
    assert week["rows"][0]["output_tokens"] == 10000
    assert datetime.fromisoformat(week["now"]) - datetime.fromisoformat(
        week["since"]
    ) == timedelta(days=7)


def test_a_turn_of_a_seat_nobody_picked_up_still_shows_up(client, platform_room):
    """`agent_handle` 空是没人认领的一轮，不是没有这一轮。"""
    _, room = platform_room
    now = datetime.now(UTC)
    _seed(
        client,
        room,
        [
            {
                "seat": None,
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(hours=1) + timedelta(seconds=10),
                "usages": [{"output_tokens": 1000}],
            }
        ],
    )

    data = _throughput(client, room, window="24h")

    assert data["rows"][0]["seat"] == ""
    assert data["rows"][0]["output_tokens"] == 1000
    assert data["rows"][0]["window_tps"] == round(1000 / 86400, 4)


def test_the_row_carries_no_per_turn_rate(client, platform_room):
    """按轮速率拆掉之后，行里只剩产出和窗口吞吐。

    一轮产出 1000 token 用 10 秒（100 tps 的按轮速率），另一轮 1 token 用 0.001 秒
    （把分位顶飞的样本）。这两个数一个都不报：网关那一层的分子不是这一轮的产出，
    拿这一轮的秒数当分母就是把两件事凑一对（模块 docstring）。"""
    _, room = platform_room
    now = datetime.now(UTC)
    _seed(
        client,
        room,
        [
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=2),
                "stopped_at": now - timedelta(hours=2) + timedelta(seconds=10),
                "usages": [{"output_tokens": 1000, "model": "m"}],
            },
            {
                "seat": "cheese",
                "started_at": now - timedelta(hours=1),
                "stopped_at": now - timedelta(hours=1) + timedelta(milliseconds=1),
                "usages": [{"output_tokens": 1, "model": "m"}],
            },
        ],
    )

    data = _throughput(client, room, window="24h")

    row = data["rows"][0]
    assert set(row) == {
        "seat",
        "model",
        "supply",
        "turns",
        "output_tokens",
        "calls",
        "turn_seconds",
        "window_tps",
    }, sorted(row)
    assert row["turns"] == 2
    assert row["output_tokens"] == 1001
    assert row["window_tps"] == round(1001 / 86400, 4)
    assert set(data["seats"][0]) == set(row)


def test_a_room_in_another_project_is_refused(client, platform_room):
    other = post_project(
        client, json={"name": "别的"}, headers=session_auth_headers(DEV)
    ).json()["data"]["id"]
    elsewhere = _room(client, other)

    response = client.get(
        "/throughput/seats",
        params={"topic": elsewhere},
        headers=room_agent_headers(client, elsewhere),
    )
    assert response.status_code == 403, response.text
    assert "知是代码仓库" in response.text
