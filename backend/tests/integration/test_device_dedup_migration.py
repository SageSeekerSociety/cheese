"""d45f136655ef deletes only never-seen, unreferenced duplicates of a device.

Failed enrollments left extra ``device`` rows under the same owner and name.
The migration archives and deletes those, and keeps every row that was ever
seen or is referred to, every row that has no namesake, and one row of every
name, so the environment picker shows each machine once.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "ad230848b32a"
AFTER = "d45f136655ef"
ARCHIVE = "device_dedup_archive_20261010"

_T0 = datetime(2026, 8, 7, 18, 0, tzinfo=UTC)


def _user(db: ReplayDatabase, name: str) -> int:
    return db.fetchval(
        'INSERT INTO "user" (username, email, created_at, updated_at)'
        " VALUES ($1, $2, now(), now()) RETURNING id",
        name,
        f"{name}@example.com",
    )


def _device(
    db: ReplayDatabase,
    owner: int,
    name: str,
    minute: int,
    *,
    seen: bool = False,
    teams: tuple[int, ...] = (),
    hosted: bool = False,
) -> str:
    device_id = uuid.uuid4().hex[:12]
    db.execute(
        "INSERT INTO device (device_id, name, token, owner_user_id, created_at,"
        " supply, last_seen_at) VALUES ($1, $2, $3, $4, $5, 'cloud', $6)",
        device_id,
        name,
        uuid.uuid4().hex,
        owner,
        _T0 + timedelta(minutes=minute),
        _T0 + timedelta(days=1) if seen else None,
    )
    for team in teams:
        db.execute(
            "INSERT INTO device_team (id, device_id, team_id, created_at,"
            " updated_at) VALUES ($1, $2, $3, now(), now())",
            uuid.uuid4(),
            device_id,
            team,
        )
    if hosted:
        db.execute(
            "INSERT INTO hosted_device (device_id, owner_user_id) VALUES ($1, $2)",
            device_id,
            owner,
        )
    return device_id


def _devices(db: ReplayDatabase) -> set[str]:
    return {r["device_id"] for r in db.fetch("SELECT device_id FROM device")}


def _seed(db: ReplayDatabase) -> dict:
    alice, bob = _user(db, "alice-dedup"), _user(db, "bob-dedup")
    _, room = seed_room(db)
    s: dict = {}

    # Five attempts at one machine, none of them ever connected: the four
    # older ones go, the newest stays so the name is still there.
    s["failed"] = [
        _device(db, alice, "microcloud-1", m, teams=(9_100_001,), hosted=True)
        for m in range(5)
    ]
    # A name whose later row connected: every unseen namesake goes.
    s["stale"] = _device(db, alice, "cloud-2", 0, teams=(9_100_002,))
    s["online"] = _device(db, alice, "cloud-2", 1, seen=True, teams=(9_100_002,))
    s["stale_newer"] = _device(db, alice, "cloud-2", 2, teams=(9_100_002,))
    # Never seen, but a room is pinned to it: it stays.
    s["pinned"] = _device(db, alice, "cloud-3", 0)
    s["pinned_twin"] = _device(db, alice, "cloud-3", 1, seen=True)
    db.execute(
        "INSERT INTO device_topic (topic_id, device_id) VALUES ($1, $2)",
        room,
        s["pinned"],
    )
    # Never seen, but a channel's compute choice names it: it stays.
    s["chosen"] = _device(db, alice, "cloud-4", 0)
    s["chosen_twin"] = _device(db, alice, "cloud-4", 1, seen=True)
    db.execute(
        "UPDATE topics SET compute_config = $1::json WHERE id = $2",
        json.dumps({"profile": "device", "device_id": s["chosen"]}),
        room,
    )
    # The only row for its name: it stays though nothing ever used it.
    s["unique"] = _device(db, alice, "laptop", 0)
    # Same name, different owners: not duplicates of each other.
    s["alice_box"] = _device(db, alice, "box", 0)
    s["bob_box"] = _device(db, bob, "box", 1)
    # The only row binding its team: it stays, so the team keeps the machine.
    s["team_only"] = _device(db, bob, "cloud-5", 0, teams=(9_100_003,))
    s["team_twin"] = _device(db, bob, "cloud-5", 1, seen=True, teams=(9_100_004,))
    return s


def test_only_never_seen_unreferenced_duplicates_are_archived_and_deleted():
    with database_at(BEFORE) as db:
        s = _seed(db)
        before = _devices(db)

        db.upgrade(AFTER)

        gone = {*s["failed"][:4], s["stale"], s["stale_newer"]}
        assert before - _devices(db) == gone
        archived = db.fetch(
            f"SELECT source_table, device_id, row_data, archived_at FROM {ARCHIVE}"
        )
        by_table: dict[str, set[str]] = {}
        for row in archived:
            by_table.setdefault(row["source_table"], set()).add(row["device_id"])
            assert row["archived_at"] is not None
        assert by_table["device"] == gone
        assert by_table["device_team"] == gone
        assert by_table["hosted_device"] == set(s["failed"][:4])
        assert not set(by_table) - {"device", "device_team", "hosted_device"}
        names = {
            json.loads(r["row_data"])["name"]
            for r in archived
            if r["source_table"] == "device"
        }
        assert names == {"microcloud-1", "cloud-2"}
        assert (
            db.fetchval(
                "SELECT count(*) FROM device_team WHERE device_id = $1",
                s["failed"][4],
            )
            == 1
        )

        # The downgrade puts every archived row back; upgrading again deletes
        # the same rows.
        db.downgrade(BEFORE)
        assert _devices(db) == before
        bound = "SELECT count(*) FROM device_team WHERE team_id = $1"
        assert db.fetchval(bound, 9_100_001) == 5
        assert db.fetchval("SELECT count(*) FROM hosted_device") == 5
        db.upgrade(AFTER)
        assert before - _devices(db) == gone
