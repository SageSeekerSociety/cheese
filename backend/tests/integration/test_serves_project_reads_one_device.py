"""Whether a project may use a machine costs the same however many it has.

Every tool call a session makes on a self-hosted machine asks this
(`machine.session_work`). It used to load every machine the project may use,
each with its own queries, to find the one it was asked about: on dev on
2026-10-04 a project with 71 machines paid about 70 ms per tool call.
"""

import time
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.device.models import DeviceRow, DeviceTeamRow, HostedDeviceRow
from app.domain.device.wiring import sql_device_service
from app.domain.project.models import Project
from app.domain.user.models import User
from tests.integration.conftest import a_team

pytestmark = pytest.mark.anyio

MACHINES = 300


async def _project_with_machines(session, count):
    team_id = await a_team(session)
    project = Project(team_id=team_id, name=f"P{count}", owner_handle="o")
    owner = User(
        username=f"owner{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.test",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add_all([project, owner])
    await session.flush()
    ids = [f"dev-{uuid.uuid4().hex[:12]}" for _ in range(count)]
    session.add_all(
        DeviceRow(
            device_id=did,
            name=did,
            token=f"tok-{did}",
            owner_user_id=owner.id,
            created_at=datetime.now(UTC),
        )
        for did in ids
    )
    await session.flush()
    session.add_all(
        HostedDeviceRow(device_id=did, owner_user_id=owner.id) for did in ids
    )
    session.add_all(DeviceTeamRow(device_id=did, team_id=team_id) for did in ids)
    await session.flush()
    return project.id, ids


async def _timed(work):
    start = time.perf_counter()
    result = await work
    return result, time.perf_counter() - start


async def test_a_project_with_many_machines_answers_as_fast_as_one_with_one(
    db_factory,
):
    async with db_factory() as session:
        small, [only] = await _project_with_machines(session, 1)
        large, machines = await _project_with_machines(session, MACHINES)
        devices = sql_device_service(session)
        # Prepared statements are in place before anything is timed.
        await devices.serves_project(only, small)
        await devices.serves_project(machines[-1], large)

        # The quickest of a few reads each: one slow pass is the machine, not
        # the query.
        one = min(
            [(await _timed(devices.serves_project(only, small)))[1] for _ in range(5)]
        )
        reads = [
            await _timed(devices.serves_project(machines[-1], large)) for _ in range(3)
        ]
        yes = all(answer for answer, _ in reads)
        many = min(took for _, took in reads)
        no = await devices.serves_project(only, large)
        await session.rollback()

    assert yes is True
    assert no is False
    assert many < 5 * one, (
        f"{MACHINES} machines took {many * 1000:.1f} ms, one took {one * 1000:.1f} ms"
    )
