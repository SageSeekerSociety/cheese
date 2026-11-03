"""Merging another deployment's export into this one (``scripts/merge_deployment``).

Two deployments that share a history up to a point: the source's people and
their work arrive here under this deployment's ids, the people both have are not
doubled, what the source changed since wins only where this side did not change
it later, and a batch can be taken back to exactly the database it found.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from app.core.config import settings
from app.core.crypto import Purpose, decrypt, encrypt
from scripts import merge_deployment
from scripts.merge_deployment import Refused, run_import, run_revert
from tests.conftest import _CLIENT_DB_NAME, _PG_BASE, _create_and_migrate

SOURCE_DB = f"{_CLIENT_DB_NAME}_merge_source"
FORK = datetime(2026, 1, 1, tzinfo=UTC)
AFTER = datetime(2026, 3, 1, tzinfo=UTC)
LATER = datetime(2026, 4, 1, tzinfo=UTC)
#: When dave registered on the source: not when carol did here.
DAVE = datetime(2026, 3, 2, tzinfo=UTC)
AGENTS = "agents.example.invalid"


def _dsn(name: str) -> str:
    return _PG_BASE.replace("+asyncpg", "") + f"/{name}"


async def _empty(conn: asyncpg.Connection) -> None:
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        " AND tablename NOT IN ('alembic_version', 'spatial_ref_sys', 'plans')"
    )
    await conn.execute(
        "TRUNCATE "
        + ", ".join(f'"{t[0]}"' for t in tables)
        + " RESTART IDENTITY CASCADE"
    )


async def _user(conn, uid, name, email, created, updated=None, password="h0"):
    await conn.execute(
        'INSERT INTO "user" (id, username, email, hashed_password, created_at, updated_at)'
        " OVERRIDING SYSTEM VALUE VALUES ($1, $2, $3, $4, $5::timestamptz, $6::timestamptz)",
        uid,
        name,
        email,
        password,
        created,
        updated or created,
    )


async def _space(conn):
    await conn.execute(
        "INSERT INTO space (id, name, intro, description, enable_rank, task_templates,"
        " created_at, updated_at) VALUES (1, 's', '', '', false, '[]', $1::timestamptz, $1::timestamptz)",
        FORK,
    )
    await conn.execute(
        "INSERT INTO space_categories (id, space_id, name, display_order, created_at, updated_at)"
        " VALUES (1, 1, 'c', 0, $1::timestamptz, $1::timestamptz)",
        FORK,
    )


async def _task(conn, tid, creator, created):
    await conn.execute(
        "INSERT INTO task (id, name, intro, description, creator_id, space_id, category_id,"
        " submitter_type, approved, default_deadline, resubmittable, editable, require_real_name,"
        " reject_reason, team_locking_policy, created_at, updated_at, published_at)"
        " VALUES ($1, 'a task', '', 'what to do', $2, 1, 1, 0, 1, 0, true, true, false, '',"
        " 'none', $3::timestamptz, $3::timestamptz, $3::timestamptz)",
        tid,
        creator,
        created,
    )


async def _member(conn, mid, task, user, created):
    await conn.execute(
        "INSERT INTO task_membership (id, task_id, member_id, participant_uuid, approved, is_team,"
        " email, phone, completion_status, created_at, updated_at)"
        " VALUES ($1, $2, $3, gen_random_uuid(), 1, false, '', '', 'none', $4::timestamptz,"
        " $4::timestamptz)",
        mid,
        task,
        user,
        created,
    )


async def _material(conn, mid, uploader, created):
    await conn.execute(
        "INSERT INTO material (id, type, url, name, uploader_id, created_at,"
        " download_count, meta) VALUES ($1, 'file', $2, 'notes', $3, $4, 0, '{}')",
        mid,
        f"/uploads/materials/{mid}-{uploader}.pdf",
        uploader,
        created,
    )


@pytest.fixture
async def deployments(db_factory, monkeypatch, tmp_path):
    """This deployment (the test's database) and a source that shares its
    first rows. Returns (source connection, target connection, uploads dirs)."""
    await asyncio.to_thread(_create_and_migrate, SOURCE_DB, f"{_PG_BASE}/{SOURCE_DB}")
    src = await asyncpg.connect(_dsn(SOURCE_DB))
    tgt = await asyncpg.connect(_dsn(_CLIENT_DB_NAME))
    await _empty(src)
    here_uploads = tmp_path / "uploads-here"
    source_uploads = tmp_path / "uploads-source"
    here_uploads.mkdir()
    source_uploads.mkdir()
    monkeypatch.setattr(merge_deployment, "async_session_factory", db_factory)
    monkeypatch.setattr(settings, "storage_local_path", str(here_uploads))
    monkeypatch.setattr(settings, "files_store", "local")
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "workspaces"))

    for conn in (src, tgt):
        await _space(conn)
        # Both have alice, bob, frank and task 9 from before they ran apart.
        await _user(conn, 1, "alice", "alice@example.com", FORK)
        await _user(conn, 2, "bob", "bob@example.com", FORK)
        await _user(conn, 6, "frank", "frank@example.com", FORK)
        await _task(conn, 9, 1, FORK)
        await conn.execute(
            "INSERT INTO user_profile (id, user_id, nickname, intro, avatar_id,"
            " created_at, updated_at) VALUES (1, 1, 'alice', '', 0, $1, $1)",
            FORK,
        )
    # Since then, here: carol registered (taking id 3), bob renamed himself
    # later than anything the source did to him, frank changed his password.
    await _user(tgt, 3, "carol", "carol@example.com", AFTER)
    # ... alice joined task 9 here too, a team took the handle "dave", and
    # material 7 was uploaded.
    await _member(tgt, 30, 9, 1, AFTER)
    await tgt.execute(
        "INSERT INTO team (id, name, intro, description, avatar_id, created_at,"
        " updated_at, handle) VALUES (900, 'd', '', '', 0, $1, $1, 'dave')",
        AFTER,
    )
    await _material(tgt, 7, 1, AFTER)
    # A team here was deleted; its handle stays its own.
    await tgt.execute(
        "INSERT INTO team (id, name, intro, description, avatar_id, created_at,"
        " updated_at, deleted_at, handle) VALUES (901, 'o', '', '', 0, $1, $1, $1, 'crew')",
        AFTER,
    )
    await tgt.execute(
        'UPDATE "user" SET username = $1, updated_at = $2::timestamptz WHERE id = 2',
        "bobby",
        LATER,
    )
    await tgt.execute(
        'UPDATE "user" SET hashed_password = $1, updated_at = $2::timestamptz WHERE id = 6',
        "frank-here",
        AFTER,
    )
    # Since then, on the source: alice and bob changed their passwords, dave
    # registered (taking id 3 there), erin registered with carol's address
    # (the same person), an agent account appeared, and dave published a task
    # the others joined. Dave verified his real name.
    await src.execute(
        'UPDATE "user" SET hashed_password = $1 WHERE id = 1', "alice-source"
    )
    await src.execute(
        'UPDATE "user" SET hashed_password = $1, updated_at = $2::timestamptz WHERE id = 2',
        "bob-source",
        AFTER,
    )
    await _user(src, 3, "dave", "dave@example.com", DAVE)
    await _user(src, 4, "erin", "Carol@Example.com", AFTER)
    await _user(src, 5, "helper", f"helper@{AGENTS}", AFTER)
    await src.execute(
        "INSERT INTO user_real_name_identities (id, user_id, encrypted, real_name, student_id,"
        " grade, major, class_name, created_at, updated_at)"
        " VALUES (1, 3, true, $1, $2, $3, $4, $5, $6::timestamptz, $6::timestamptz)",
        *[
            encrypt(Purpose.REALNAME, f"dave-{c}", bound_to=f"user:3:{c}")
            for c in ("real_name", "student_id", "grade", "major", "class_name")
        ],
        AFTER,
    )
    # alice picked a new avatar on the source.
    await src.execute(
        "INSERT INTO avatar (id, url, name, created_at, avatar_type, usage_count)"
        " VALUES (5, '', 'me.png', $1, 'upload', 1)",
        AFTER,
    )
    await src.execute("UPDATE user_profile SET avatar_id = 5 WHERE id = 1")
    await _material(src, 7, 3, DAVE)
    await src.execute(
        "INSERT INTO team (id, name, intro, description, avatar_id, created_at,"
        " updated_at, handle) VALUES (950, 'c', '', '', 0, $1, $1, 'crew')",
        DAVE,
    )
    await _task(src, 10, 3, AFTER)
    # The task hands its AI teammate the material dave uploaded.
    await src.execute(
        "UPDATE task SET protocol_override = $1::json WHERE id = 10",
        json.dumps({"teaching": {"material_ids": [7]}}),
    )
    await _member(src, 23, 9, 1, DAVE)
    await _member(src, 20, 10, 3, AFTER)
    await _member(src, 21, 10, 4, AFTER)
    await _member(src, 22, 10, 5, AFTER)
    for conn in (src, tgt):
        # As on a running deployment, each id sequence is past every id.
        for sequence, table in [
            ("user_id_seq1", '"user"'),
            ("task_seq", "task"),
            ("task_membership_seq", "task_membership"),
            ("team_seq", "team"),
            ("material_id_seq", "material"),
            ("avatar_id_seq", "avatar"),
            ("user_profile_id_seq", "user_profile"),
            ("user_real_name_identities_id_seq", "user_real_name_identities"),
        ]:
            await conn.execute(
                f"SELECT setval('{sequence}', coalesce(max(id), 1)) FROM {table}"
            )
    (source_uploads / "attachments").mkdir()
    (source_uploads / "attachments" / "brief.pdf").write_bytes(b"from the source")
    (source_uploads / "attachments" / "shared.pdf").write_bytes(b"the source's bytes")
    (here_uploads / "attachments").mkdir()
    (here_uploads / "attachments" / "shared.pdf").write_bytes(b"this side's bytes")
    try:
        yield src, tgt, here_uploads, source_uploads
    finally:
        await src.close()
        await tgt.close()


MANIFEST = {
    # Rows written on the source since: shared ones, and dave, who is new there.
    "changed": {"user": [1, 2, 3], "user_profile": [1]},
    "skip_account_email_domains": [AGENTS],
}


async def _import(source_uploads, *, batch="b1", dry=False):
    return await run_import(
        batch=batch,
        source_url=_dsn(SOURCE_DB),
        source_uploads=source_uploads,
        manifest=MANIFEST,
        native=None,
        dry=dry,
    )


async def _snapshot(conn: asyncpg.Connection) -> dict[str, list[str]]:
    """Every row of every table a merge writes, plus the uploads it may touch."""
    tables = [k.table for k in merge_deployment.KINDS] + sorted(
        merge_deployment.DERIVED
    )
    out = {}
    for t in tables:
        rows = await conn.fetch(
            f'SELECT to_jsonb(x)::text AS r FROM "{t}" x ORDER BY 1'
        )
        out[t] = [r["r"] for r in rows]
    for seq in ("task_seq", "task_membership_seq", "task_package_revisions_id_seq"):
        out[seq] = [
            str(tuple(await conn.fetchrow(f"SELECT last_value, is_called FROM {seq}")))
        ]
    return out


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


async def test_the_sources_people_and_work_arrive_under_this_deployments_ids(
    deployments,
):
    src, tgt, here_uploads, source_uploads = deployments

    await _import(source_uploads)

    users = {r["username"]: r for r in await tgt.fetch('SELECT * FROM "user"')}
    # dave's id 3 is carol's here: he arrives under another one, and as a
    # team here already answers to "dave", under another name too.
    dave = await tgt.fetchval(
        "SELECT id FROM \"user\" WHERE email = 'dave@example.com'"
    )
    assert users[f"dave_{dave}"]["id"] == dave
    assert dave != 3 and users["carol"]["id"] == 3
    # erin is carol (same address): no second account.
    assert "erin" not in users
    assert (
        len([u for u in users.values() if u["email"].lower() == "carol@example.com"])
        == 1
    )
    # The agent account and what names it stay behind.
    assert "helper" not in users
    task = await tgt.fetchrow("SELECT * FROM task WHERE id = 10")
    assert task["creator_id"] == dave
    members = sorted(
        r["member_id"]
        for r in await tgt.fetch(
            "SELECT member_id FROM task_membership WHERE task_id = 10"
        )
    )
    assert members == sorted([dave, 3])
    # His real name opens with this deployment's key, bound to his id here.
    identity = await tgt.fetchrow(
        "SELECT * FROM user_real_name_identities WHERE user_id = $1", dave
    )
    assert (
        decrypt(
            Purpose.REALNAME, identity["real_name"], bound_to=f"user:{dave}:real_name"
        )
        == "dave-real_name"
    )
    # Changed on the source only: the source wins. Changed here later: this side
    # stays. Changed here and not listed as changed there: this side stays.
    assert users["alice"]["hashed_password"] == "alice-source"
    assert users["bobby"]["hashed_password"] == "h0"
    assert users["frank"]["hashed_password"] == "frank-here"
    # alice is on task 9 once, though both sides added her.
    assert (
        await tgt.fetchval(
            "SELECT count(*) FROM task_membership WHERE task_id = 9 AND member_id = 1"
        )
        == 1
    )
    # The task's teaching names dave's material under its id here.
    material = await tgt.fetchval(
        "SELECT id FROM material WHERE uploader_id = $1", dave
    )
    assert material != 7
    override = json.loads(task["protocol_override"])
    assert override["teaching"]["material_ids"] == [material]
    # dave's team arrives under a handle nobody here has ever had.
    assert await tgt.fetchval("SELECT handle FROM team WHERE id = 950") == "team-950"
    # alice's profile shows the avatar she picked there.
    assert (
        await tgt.fetchval(
            "SELECT a.name FROM user_profile p JOIN avatar a ON a.id = p.avatar_id"
            " WHERE p.user_id = 1"
        )
        == "me.png"
    )
    # The new task has its first version, as a task published here has.
    assert (
        await tgt.fetchval(
            "SELECT number FROM task_package_revisions WHERE task_id = 10"
        )
        == 1
    )
    # A file not here is copied; one here is never overwritten.
    files = _files(here_uploads)
    assert files["attachments/brief.pdf"] == b"from the source"
    assert files["attachments/shared.pdf"] == b"this side's bytes"


async def test_a_batch_comes_in_once_and_its_revert_leaves_the_database_as_it_was(
    deployments,
):
    src, tgt, here_uploads, source_uploads = deployments
    before, before_files = await _snapshot(tgt), _files(here_uploads)

    await _import(source_uploads)
    after = await _snapshot(tgt)
    again = await _import(source_uploads)
    assert again.get("already") is True
    assert await _snapshot(tgt) == after

    await run_revert(batch="b1", dry=False)

    assert await _snapshot(tgt) == before
    assert _files(here_uploads) == before_files


async def test_a_revert_refuses_while_a_merged_row_has_changed_since(deployments):
    src, tgt, here_uploads, source_uploads = deployments
    await _import(source_uploads)
    await tgt.execute(
        "UPDATE \"user\" SET username = 'dave2' WHERE email = 'dave@example.com'"
    )

    with pytest.raises(Refused):
        await run_revert(batch="b1", dry=False)

    assert await tgt.fetchval("SELECT count(*) FROM task WHERE id = 10") == 1


async def test_a_dry_run_writes_nothing(deployments):
    src, tgt, here_uploads, source_uploads = deployments
    before, before_files = await _snapshot(tgt), _files(here_uploads)

    report = await _import(source_uploads, dry=True)

    assert report["kinds"]["task"]["inserted"] == 1
    assert await _snapshot(tgt) == before
    assert _files(here_uploads) == before_files
    assert await tgt.fetchval("SELECT count(*) FROM deployment_merge_batches") == 0
    assert json.dumps(report, default=str)


async def test_the_same_export_under_another_batch_name_brings_nothing_new(deployments):
    src, tgt, here_uploads, source_uploads = deployments
    await _import(source_uploads, batch="b1")
    after = await _snapshot(tgt)

    await _import(source_uploads, batch="b2")

    assert await _snapshot(tgt) == after


async def test_a_revert_refuses_while_rows_made_since_name_what_it_brought(deployments):
    src, tgt, here_uploads, source_uploads = deployments
    await _import(source_uploads)
    # frank joins the task that came with the batch.
    await _member(tgt, 99, 10, 6, LATER)

    with pytest.raises(Refused):
        await run_revert(batch="b1", dry=False)

    assert await tgt.fetchval("SELECT count(*) FROM task WHERE id = 10") == 1
