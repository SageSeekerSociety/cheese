"""Cloud compute is charged in credits for the time a sandbox runs (#2320 计费).

A sandbox costs a fixed price per running hour, from start to idle stop, out of
the same credits and the same payer as the model calls of its project: the
project's team, or the owner's personal team for their own project. Nothing is
charged while it sleeps. With the credits spent no sandbox starts, and a
running one stops once its room's turn is over. With no price set, no cloud
sandbox starts at all. A person's own device is never charged.

The cloud here is the one ``test_sandbox_idle_stop`` builds: hosts are
directories on this machine and a fake MicroCloud; the price is the suite's
(``CLOUD_SANDBOX_CREDITS_PER_HOUR=12``, tests/conftest.py).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.domain.agent.models import AgentTurn
from app.domain.block.models import Block, BlockKind
from app.domain.machine import metering
from app.domain.machine.runner import ComputeMeterSweeper
from app.domain.project.models import Project
from app.domain.team.services import team_service
from app.domain.usage.ledger import Ledger, payer_for_project
from app.domain.usage.models import ComputeRun, ResourceUsage
from app.domain.usage.report import LINES, UsageReport
from app.domain.usage.services import UsageService
from app.domain.user.services import user_by_handle
from tests.integration.test_device_owner_told import _machines, _room_on_a_device
from tests.integration.test_sandbox_idle_stop import cloud as cloud
from tests.integration.test_sandbox_idle_stop import (
    home_of,
    run,
    sweep,
    time_passes,
    tool_call,
    working_on,
)

PRICE = 12.0  # credits per sandbox-hour, as the suite sets it


@pytest.fixture(autouse=True)
def _credits_checked_now(monkeypatch):
    # Whether a payer still has credits is asked once a minute per process.
    monkeypatch.setattr(metering, "_checked_at", None)


def meter(case) -> dict:
    return run(case, ComputeMeterSweeper(case.client.test_request_factory).sweep)


def runs_of(case) -> list[ComputeRun]:
    async def read():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(ComputeRun)
                    .where(ComputeRun.project_id == case.project_id)
                    .order_by(ComputeRun.started_at)
                )
            )

    return run(case, read)


def charges(case) -> list[ResourceUsage]:
    async def read():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(ResourceUsage)
                    .where(
                        ResourceUsage.project_id == case.project_id,
                        ResourceUsage.route == "compute",
                    )
                    .order_by(ResourceUsage.created_at)
                )
            )

    return run(case, read)


def ran_for(case, minutes: int):
    """The open run started ``minutes`` ago and has not been charged since."""

    async def age():
        async with case.client.test_request_factory() as db:
            then = datetime.now(UTC) - timedelta(minutes=minutes)
            await db.execute(
                update(ComputeRun)
                .where(
                    ComputeRun.project_id == case.project_id,
                    ComputeRun.ended_at.is_(None),
                )
                .values(started_at=then, billed_until=then)
            )
            await db.commit()

    run(case, age)


def team_of(case) -> int:
    async def read():
        async with case.client.test_request_factory() as db:
            return (await db.get(Project, case.project_id)).team_id

    return run(case, read)


def credits_used(case) -> float:
    async def read():
        async with case.client.test_request_factory() as db:
            return (await UsageService(db).project_credits(case.project_id))[
                "credits_used"
            ]

    return run(case, read)


def spend_everything(case):
    async def spend():
        async with case.client.test_request_factory() as db:
            payer = await payer_for_project(db, case.project_id)
            await Ledger(db).charge(payer, 10_000)
            await db.commit()

    run(case, spend)


def room_says(case, seat) -> list[str]:
    async def read():
        async with case.client.test_request_factory() as db:
            return list(
                await db.scalars(
                    select(Block.content)
                    .where(
                        Block.conversation_id == seat.room,
                        Block.kind == BlockKind.event,
                    )
                    .order_by(Block.created_at)
                )
            )

    return run(case, read)


def test_a_sandbox_is_charged_for_the_time_it_runs_and_not_while_it_sleeps(cloud):
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    assert meter(cloud)["opened"] == 1

    # Ninety minutes of running are charged while it still runs, in whole
    # minutes, to the project's team.
    ran_for(cloud, 90)
    assert meter(cloud)["charged"] == 1
    [first] = charges(cloud)
    assert first.credits == pytest.approx(90 / 60 * PRICE)
    assert first.team_id == team_of(cloud)
    assert first.conversation_id == seat.room
    assert (first.kind, first.total_tokens) == ("sandbox", 0)

    # Idle, it sleeps; its last partial minute is charged as a whole one.
    time_passes(cloud, seat, timedelta(minutes=11))
    assert sweep(cloud)["asleep"] == 1
    meter(cloud)
    [only] = runs_of(cloud)
    assert only.ended_at == home_of(cloud, seat).stopped_at
    total = 90 / 60 * PRICE + PRICE / 60
    assert sum(c.credits for c in charges(cloud)) == pytest.approx(total)

    # Asleep it costs nothing, however long.
    meter(cloud)
    assert len(charges(cloud)) == 2
    assert [r.ended_at is not None for r in runs_of(cloud)] == [True]

    # Woken by the next tool call, it is charged again from then on.
    assert tool_call(cloud, seat)["target"]["device_id"] == "host-a"
    assert meter(cloud)["opened"] == 1
    assert [r.ended_at is None for r in runs_of(cloud)] == [False, True]

    # The credits are the team's, the same ones its model calls draw on, and
    # its usage page shows them as 算力.
    assert credits_used(cloud) == pytest.approx(total)

    team_id = team_of(cloud)

    async def team_page():
        async with cloud.client.test_request_factory() as db:
            team = await team_service(db).get_team(team_id)
            return await UsageReport(db).team(team.id, team.plan_key)

    page = run(cloud, team_page)
    assert page["lines"]["compute"] == pytest.approx(total)
    assert page["lines"]["collab"] == 0


def test_a_persons_own_project_pays_from_their_personal_credits(cloud):
    async def owned_by_alice():
        async with cloud.client.test_request_factory() as db:
            alice = await user_by_handle(db, "alice")
            personal = await team_service(db).ensure_personal_team(alice.id)
            (await db.get(Project, cloud.project_id)).team_id = personal.id
            await db.commit()
            return personal.id

    personal = run(cloud, owned_by_alice)
    seat = cloud.seats[0]
    working_on(cloud, seat, "host-a")
    meter(cloud)
    ran_for(cloud, 30)
    meter(cloud)

    [charge] = charges(cloud)
    assert charge.team_id == personal
    assert charge.credits == pytest.approx(PRICE / 2)

    async def my_page():
        async with cloud.client.test_request_factory() as db:
            team = await team_service(db).get_team(personal)
            return await UsageReport(db).team(team.id, team.plan_key, lines=LINES)

    assert run(cloud, my_page)["lines"]["compute"] == pytest.approx(PRICE / 2)


def test_spent_credits_start_no_sandbox_and_stop_a_running_one_after_its_turn(cloud):
    working, newcomer = cloud.seats[0], cloud.seats[1]
    working_on(cloud, working, "host-a")
    meter(cloud)

    async def turn_runs():
        async with cloud.client.test_request_factory() as db:
            turn = AgentTurn(
                id=uuid.uuid4(),
                conversation_id=working.room,
                continuation_id=uuid.uuid4(),
                author="alice",
                started_at=datetime.now(UTC),
            )
            db.add(turn)
            await db.commit()
            return turn.id

    turn = run(cloud, turn_runs)
    spend_everything(cloud)

    # No new sandbox starts, and nothing is asked of the provider for it.
    created = len(cloud.provider.created)
    answer = tool_call(cloud, newcomer)
    assert "额度已用完" in answer["unavailable"]
    assert home_of(cloud, newcomer) is None
    assert len(cloud.provider.created) == created

    # The running one finishes the turn it is in.
    sweep(cloud)
    assert home_of(cloud, working).stopped_at is None

    async def turn_ends():
        async with cloud.client.test_request_factory() as db:
            await db.execute(
                update(AgentTurn)
                .where(AgentTurn.id == turn)
                .values(stopped_at=datetime.now(UTC))
            )
            await db.commit()

    run(cloud, turn_ends)
    metering._checked_at = None
    assert sweep(cloud)["asleep"] == 1
    assert home_of(cloud, working).stopped_at is not None
    assert (
        "额度已用完，沙箱已停止。文件都留着，有了额度后下一条消息会唤醒它。"
        in room_says(cloud, working)
    )
    meter(cloud)
    assert all(r.ended_at is not None for r in runs_of(cloud))

    # And it is not woken while the credits stay spent.
    assert "额度已用完" in tool_call(cloud, working)["unavailable"]
    assert home_of(cloud, working).stopped_at is not None


def test_with_no_price_set_no_cloud_sandbox_starts(cloud, monkeypatch, caplog):
    monkeypatch.setattr(settings, "cloud_sandbox_credits_per_hour", None)
    seat = cloud.seats[0]

    with caplog.at_level(logging.ERROR, logger="cheese.usage.compute"):
        answer = tool_call(cloud, seat)

    assert answer["unavailable"] == (
        "云端沙箱暂时无法启动：平台还没有设定云端算力的价格。对话和平台工具仍可用。"
    )
    assert home_of(cloud, seat) is None
    assert cloud.provider.created == []
    assert any(
        "CLOUD_SANDBOX_CREDITS_PER_HOUR" in record.getMessage()
        for record in caplog.records
    )
    meter(cloud)
    assert runs_of(cloud) == [] and charges(cloud) == []


@pytest.mark.anyio
async def test_a_persons_own_device_is_never_charged(client, monkeypatch):
    # Neither a price nor credits stand between a session and its own device.
    monkeypatch.setattr(settings, "cloud_sandbox_credits_per_hour", None)
    room = await _room_on_a_device(client, "alice")
    _machines(monkeypatch)
    async with client.test_factory() as db:
        await Ledger(db).charge(await payer_for_project(db, room.project_id), 10_000)
        await db.commit()

    leased = client.post(room.lease_path, headers=room.agent, json={"env": {}})
    assert leased.status_code == 200, leased.text
    assert leased.json()["data"]["target"]["device_id"] == room.device_id

    await ComputeMeterSweeper(client.test_factory).sweep()
    async with client.test_factory() as db:
        assert (
            await db.scalars(
                select(ComputeRun).where(ComputeRun.project_id == room.project_id)
            )
        ).all() == []
        assert (
            await db.scalars(
                select(ResourceUsage).where(
                    ResourceUsage.project_id == room.project_id,
                    ResourceUsage.route == "compute",
                )
            )
        ).all() == []
