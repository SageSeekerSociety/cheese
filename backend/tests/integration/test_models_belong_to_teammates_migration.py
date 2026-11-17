"""Models move onto teammates (c4e8a2f61d93).

The rules, as stated before the migration was written:

- a project's subagent default becomes what each of its teammates uses for
  subagents, unless the teammate already set its own; a teammate on a member's
  own account is left alone; the project no longer keeps the setting;
- a project made from a new-user starter whose main model is still its pack's
  model hands that model to its default teammate for tasks (unless that
  teammate chose one) and goes back to 「自动」;
- a starter project whose main model somebody changed, and a project made from
  any other 题目, keep their main model;
- running it again changes nothing.
"""

import json
import uuid

import asyncpg

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "e5a17c3d9b42"
AFTER = "c4e8a2f61d93"

_STARTER_PACK = {
    "compute_credits": 50,
    "model": "deepseek-flash",
    "once_per_person": True,
}


def _teammate(
    db: ReplayDatabase, project: uuid.UUID, configuration: dict, *, own: bool = False
) -> uuid.UUID:
    instance = uuid.uuid4()

    async def work(conn: asyncpg.Connection) -> None:
        await conn.execute(
            "INSERT INTO agent_instances (id, project_id, handle, configuration,"
            " display_name, name_source, is_active, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4::json, 'm', 'human', true, now(), now())",
            instance,
            project,
            f"mate-{instance.hex[:8]}",
            json.dumps(configuration),
        )
        if own:
            user_id = await conn.fetchval(
                'INSERT INTO "user" (username, email, created_at, updated_at)'
                " VALUES ($1::varchar, $1::varchar || '@example.test', now(), now())"
                " RETURNING id",
                f"owner-{instance.hex[:8]}",
            )
            await conn.execute(
                "INSERT INTO own_agents (instance_id, owner_user_id, harness)"
                " VALUES ($1, $2, 'claude-code')",
                instance,
                user_id,
            )

    db.run(work)
    return instance


def _task(db: ReplayDatabase, task_id: int, pack: dict) -> int:
    return db.fetchval(
        "INSERT INTO task (id, name, submitter_type, resubmittable, editable,"
        " intro, description, space_id, category_id, creator_id,"
        " default_deadline, approved, require_real_name, reject_reason,"
        " access_control_enabled, team_locking_policy, created_at, updated_at,"
        " protocol_override) VALUES ($1, 't', 0, true, true, '', '', 1, 1,"
        " 1, 30, 0, false, '', false, 'NO_LOCK', now(), now(), $2::json)"
        " RETURNING id",
        task_id,
        json.dumps({"resource_pack": pack}),
    )


def _project(
    db: ReplayDatabase, settings: dict, *, task: int | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """A project with ``settings``, made from ``task``, and its default
    teammate (configured with nothing)."""
    project, _room = seed_room(db)
    teammate = _teammate(db, project, {"body": "", "model": None})
    db.execute(
        "UPDATE projects SET settings = $2::json, external_task_id = $3,"
        " default_agent_instance_id = $4 WHERE id = $1",
        project,
        json.dumps(settings),
        task,
        teammate,
    )
    return project, teammate


def _settings(db: ReplayDatabase, project: uuid.UUID) -> dict:
    return json.loads(
        db.fetchval("SELECT settings::text FROM projects WHERE id = $1", project)
    )


def _configuration(db: ReplayDatabase, instance: uuid.UUID) -> dict:
    return json.loads(
        db.fetchval(
            "SELECT configuration::text FROM agent_instances WHERE id = $1", instance
        )
    )


def test_a_projects_subagent_default_moves_onto_its_teammates():
    with database_at(BEFORE) as db:
        project, default = _project(
            db, {"default_model": "glm", "default_subagent_model": "sonnet"}
        )
        chose = _teammate(db, project, {"subagent_model": "opus"})
        own = _teammate(db, project, {"model": "sonnet"}, own=True)
        version = db.fetchval(
            "SELECT version FROM agent_instances WHERE id = $1", default
        )

        db.upgrade(AFTER)

        assert _settings(db, project) == {"default_model": "glm"}
        assert _configuration(db, default)["subagent_model"] == "sonnet"
        assert _configuration(db, chose)["subagent_model"] == "opus"
        assert "subagent_model" not in _configuration(db, own)
        # An editor open on the old configuration is refused, not obeyed.
        assert (
            db.fetchval("SELECT version FROM agent_instances WHERE id = $1", default)
            == version + 1
        )

        db.downgrade(BEFORE)
        db.upgrade(AFTER)
        assert _configuration(db, default)["subagent_model"] == "sonnet"
        assert (
            db.fetchval("SELECT version FROM agent_instances WHERE id = $1", default)
            == version + 1
        )


def test_a_starter_project_hands_its_pack_model_to_its_teammate_for_tasks():
    with database_at(BEFORE) as db:
        listed = _task(db, 91_001, _STARTER_PACK)
        remade = _task(db, 91_002, _STARTER_PACK)
        other = _task(db, 91_003, {"compute_credits": 10, "model": "deepseek-flash"})
        db.execute(
            "INSERT INTO onboarding_starters (key, space_id, task_id, created_at)"
            " VALUES ('web', 1, $1, now())",
            listed,
        )
        starter, starter_mate = _project(
            db, {"default_model": "deepseek-flash"}, task=listed
        )
        # A starter made again on a new board: the old 题目 is no longer listed,
        # but its pack is the starters' own.
        older, older_mate = _project(
            db, {"default_model": "deepseek-flash"}, task=remade
        )
        changed, changed_mate = _project(db, {"default_model": "glm"}, task=listed)
        unrelated, unrelated_mate = _project(
            db, {"default_model": "deepseek-flash"}, task=other
        )
        plain, _ = _project(db, {"default_model": "deepseek-flash"})
        chose, chose_mate = _project(
            db, {"default_model": "deepseek-flash"}, task=listed
        )
        db.execute(
            "UPDATE agent_instances SET configuration = $2::json WHERE id = $1",
            chose_mate,
            json.dumps({"task_model": "glm"}),
        )

        db.upgrade(AFTER)

        for project, mate in ((starter, starter_mate), (older, older_mate)):
            assert "default_model" not in _settings(db, project)
            assert _configuration(db, mate)["task_model"] == "deepseek-flash"
        assert "default_model" not in _settings(db, chose)
        assert _configuration(db, chose_mate)["task_model"] == "glm"
        assert _settings(db, changed) == {"default_model": "glm"}
        assert "task_model" not in _configuration(db, changed_mate)
        assert _settings(db, unrelated) == {"default_model": "deepseek-flash"}
        assert "task_model" not in _configuration(db, unrelated_mate)
        assert _settings(db, plain) == {"default_model": "deepseek-flash"}

        db.downgrade(BEFORE)
        db.upgrade(AFTER)
        assert _configuration(db, starter_mate)["task_model"] == "deepseek-flash"
        assert "default_model" not in _settings(db, starter)
