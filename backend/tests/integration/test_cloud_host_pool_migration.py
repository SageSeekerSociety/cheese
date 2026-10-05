"""Machines rented per room or session before the pool are adopted into it.

Each one still held becomes a draining host: it keeps the sessions on it, takes
no new one, and goes once nothing of anyone's is only there. Runs the real
``alembic upgrade`` from the revision before, against a scratch database seeded
the way production databases look.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.device.models import DeviceRow, DeviceTeamRow
from app.domain.device.supply import Supply
from app.domain.project.models import Project
from app.domain.team.models import Team
from app.domain.topic.models import Topic
from app.domain.user.repositories import UserRepository
from tests.conftest import _PG_BASE, _admin_recreate_db

_REVISION = "c4e7a2d91f30"
_PREVIOUS = "d72d0f566149"
_BACKEND = Path(__file__).resolve().parents[2]
_SPEC = {"cores": 8, "memory_mb": 16384, "disk_gb": 100}


def _alembic(db_name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_BACKEND,
        env={**os.environ, "DATABASE_URL": f"{_PG_BASE}/{db_name}"},
        capture_output=True,
        text=True,
    )


async def _drop(db_name: str) -> None:
    conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
    finally:
        await conn.close()


@pytest.fixture
def db_before_the_pool(_pg_schema):
    db_name = f"cheesex_pool_{uuid.uuid4().hex[:8]}"
    asyncio.run(_admin_recreate_db(db_name))
    step = _alembic(db_name, "upgrade", _PREVIOUS)
    assert step.returncode == 0, step.stderr
    try:
        yield db_name
    finally:
        asyncio.run(_drop(db_name))


def _machine(**values) -> dict:
    now = datetime.now(UTC)
    return {
        "machine_id": None,
        "customer_id": 7,
        "account_id": 9,
        "offering_id": 1,
        "login_user": "cheese",
        "cores": 2,
        "memory_mb": 4096,
        "disk_gb": 20,
        "status": "running",
        "ai_status": "disabled",
        "device_id": None,
        "topic_id": None,
        "session_id": None,
        "superseded_at": None,
        "released_at": None,
        "created_at": now,
        "updated_at": now,
        **values,
    }


async def _seed(db_name: str) -> dict:
    engine = create_async_engine(f"{_PG_BASE}/{db_name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    ids: dict = {}
    async with factory() as db:
        handle = f"pool-{uuid.uuid4().hex[:8]}"
        user = await UserRepository(db).create_user(
            username=handle, email=f"{handle}@example.com"
        )
        team = Team(
            name="Team",
            handle=f"t-{uuid.uuid4().hex[:8]}",
            intro="",
            description="",
            avatar_id=1,
            created_at=now,
            updated_at=now,
        )
        db.add(team)
        await db.flush()
        cloud_choice = {"name": None, "profile": "cloud", "device_id": None, **_SPEC}
        project = Project(
            name="P",
            owner_handle=handle,
            team_id=team.id,
            settings={"compute_configs": {"default": cloud_choice}, "x": 1},
        )
        db.add(project)
        await db.flush()
        room = Topic(project_id=project.id, title="Room", compute_config=cloud_choice)
        old_room = Topic(project_id=project.id, title="Old room")
        db.add_all([room, old_room])
        await db.flush()
        for device_id, supply in (
            ("dev-shared", Supply.cloud),
            ("dev-left", Supply.cloud),
            ("dev-room", Supply.cloud),
            ("dev-laptop", Supply.self_hosted),
        ):
            db.add(
                DeviceRow(
                    device_id=device_id,
                    name=device_id,
                    token=f"token-{device_id}",
                    owner_user_id=user.id,
                    supply=supply,
                    created_at=now,
                )
            )
            await db.flush()
            db.add(DeviceTeamRow(device_id=device_id, team_id=team.id))
        resource = str(room.resource_id or room.id)
        # Written as rows, not through today's model: the table is at the
        # revision before the pool, and the model has moved on since.
        sessions = {
            "working": (
                {"generation": "g-working", "choice": cloud_choice},
                {
                    "kind": "device",
                    "device_id": "dev-shared",
                    "resource_id": "r-working",
                    "room_resource_id": resource,
                    "status": "ready",
                },
            ),
            "pending": ({"generation": "g-pending", "choice": cloud_choice}, None),
            "leaving": (
                {
                    "generation": "g-leaving",
                    "choice": {
                        "profile": "device",
                        "device_id": "dev-laptop",
                        "name": None,
                    },
                    "retained_leases": [
                        {
                            "device_id": "dev-left",
                            "resource_id": "r-left",
                            "kind": "device",
                        }
                    ],
                },
                None,
            ),
        }
        session_ids = {handle: uuid.uuid4() for handle in sessions}
        for handle, (request, lease) in sessions.items():
            await db.execute(
                text(
                    "INSERT INTO agent_sessions (id, topic_id, agent_handle, "
                    "harness, execution_request, work_lease, created_at, "
                    "updated_at) VALUES (:id, :topic, :handle, 'claude-code', "
                    "CAST(:request AS json), CAST(:lease AS json), :now, :now)"
                ),
                {
                    "id": session_ids[handle],
                    "topic": room.id,
                    "handle": handle,
                    "request": json.dumps(request),
                    "lease": None if lease is None else json.dumps(lease),
                    "now": now,
                },
            )
        await db.commit()
        ids.update(
            project=project.id,
            room=room.id,
            old_room=old_room.id,
            room_resource=resource,
            working=session_ids["working"],
            pending=session_ids["pending"],
            leaving=session_ids["leaving"],
            team=team.id,
        )

    machines = {
        # The room's machine, rented by `working`, which `pending` was placed on.
        "shared": _machine(
            machine_id=101,
            hostname="p-1",
            device_id="dev-shared",
            topic_id=ids["room"],
            session_id=ids["working"],
        ),
        # `pending`'s own allocation, still being built.
        "building": _machine(
            machine_id=102,
            hostname="p-2",
            status="provisioning",
            topic_id=ids["room"],
            session_id=ids["pending"],
        ),
        # Left by `leaving` without pushing.
        "left": _machine(
            machine_id=103,
            hostname="p-3",
            device_id="dev-left",
            topic_id=ids["room"],
            session_id=ids["leaving"],
            superseded_at=datetime.now(UTC),
        ),
        # A room machine from before session leases.
        "room": _machine(
            machine_id=104,
            hostname="p-4",
            device_id="dev-room",
            topic_id=ids["old_room"],
        ),
        # Already released: nothing to adopt.
        "released": _machine(
            machine_id=105,
            hostname="p-5",
            topic_id=ids["room"],
            released_at=datetime.now(UTC),
        ),
    }
    conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + f"/{db_name}")
    try:
        for key, row in machines.items():
            row_id = uuid.uuid4()
            ids[f"machine:{key}"] = row_id
            columns = ["id", "project_id", "hostname", *row]
            columns = list(dict.fromkeys(columns))
            values = {"id": row_id, "project_id": ids["project"], **row}
            await conn.execute(
                f"INSERT INTO project_machines ({', '.join(columns)}) "
                f"VALUES ({', '.join(f'${n + 1}' for n in range(len(columns)))})",
                *(values[c] for c in columns),
            )
        for key, state in (("shared", "claimed"), ("released", "claimed")):
            await conn.execute(
                "INSERT INTO warm_machines (id, created_at, updated_at, state, "
                "create_request, attempts, claimed_machine_id) "
                "VALUES ($1, now(), now(), $2, '{}'::jsonb, 0, $3)",
                uuid.uuid4(),
                state,
                ids[f"machine:{key}"],
            )
        await conn.execute("INSERT INTO machine_limit (id, value) VALUES (1, 50)")
    finally:
        await conn.close()
    await engine.dispose()
    return ids


def test_held_machines_become_draining_hosts_with_their_sessions_homes(
    db_before_the_pool,
):
    ids = asyncio.run(_seed(db_before_the_pool))

    step = _alembic(db_before_the_pool, "upgrade", _REVISION)
    assert step.returncode == 0, step.stderr

    async def read():
        conn = await asyncpg.connect(
            _PG_BASE.replace("+asyncpg", "") + f"/{db_before_the_pool}"
        )
        try:
            hosts = {
                r["id"]: r
                for r in await conn.fetch(
                    "SELECT id, draining, device_id, machine_id FROM cloud_hosts"
                )
            }
            homes = await conn.fetch(
                "SELECT host_id, topic_id, session_id, resource_id, "
                "room_resource_id, left_at FROM cloud_host_homes"
            )
            teams = [
                r["device_id"]
                for r in await conn.fetch("SELECT device_id FROM device_team")
            ]
            warm = [
                r["claimed_host_id"]
                for r in await conn.fetch("SELECT claimed_host_id FROM warm_machines")
            ]
            limit_table = await conn.fetchval("SELECT to_regclass('machine_limit')")
            choices = {
                "project": await conn.fetchval(
                    "SELECT settings FROM projects WHERE id = $1", ids["project"]
                ),
                "room": await conn.fetchval(
                    "SELECT compute_config FROM topics WHERE id = $1", ids["room"]
                ),
                "session": await conn.fetchval(
                    "SELECT execution_request FROM agent_sessions WHERE id = $1",
                    ids["working"],
                ),
            }
            return hosts, homes, teams, warm, limit_table, choices
        finally:
            await conn.close()

    hosts, homes, teams, warm, limit_table, choices = asyncio.run(read())

    held = {ids[f"machine:{k}"] for k in ("shared", "building", "left", "room")}
    assert set(hosts) == held
    assert all(host["draining"] for host in hosts.values())

    by_session = {
        (home["session_id"], home["host_id"]): home
        for home in homes
        if home["session_id"] is not None
    }
    working = by_session[(ids["working"], ids["machine:shared"])]
    assert working["resource_id"] == "r-working" and working["left_at"] is None
    pending = by_session[(ids["pending"], ids["machine:building"])]
    assert pending["resource_id"] == "g-pending" and pending["left_at"] is None
    left = by_session[(ids["leaving"], ids["machine:left"])]
    assert left["resource_id"] == "r-left" and left["left_at"] is not None
    [room_dir] = [home for home in homes if home["session_id"] is None]
    assert room_dir["host_id"] == ids["machine:room"]
    assert room_dir["topic_id"] == ids["old_room"]
    assert len(homes) == 4

    # Hosts left the team's pool; the person's own device did not.
    assert teams == ["dev-laptop"]
    # A warm claim follows its machine into the pool; one whose machine was
    # released before has nothing to follow.
    assert sorted(warm, key=str) == sorted([ids["machine:shared"], None], key=str)
    assert limit_table is None
    assert json.loads(choices["project"]) == {
        "compute_configs": {
            "default": {"name": None, "profile": "cloud", "device_id": None}
        },
        "x": 1,
    }
    assert not set(_SPEC) & set(json.loads(choices["room"]))
    assert not set(_SPEC) & set(json.loads(choices["session"])["choice"])
