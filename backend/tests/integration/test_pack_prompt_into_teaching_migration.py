"""b7d4e1a9c2f3: a 资源包's skills are given by the 题目's AI guidance too.

The migration runs while the previous release serves, so it changes nothing
that release reads — only the guidance's ``skills``, which it does not read,
is added to. Under this release every teammate of a project built from the 题目
reads what its default teammate read before, and a teammate added later now
carries the pack's skills too.
"""

import json
import uuid
from types import SimpleNamespace as NS

from app.domain.agent.harness.opening import teaching_section
from app.domain.agent.turn.intake.context import carried_skills
from app.domain.task.protocol import resolve
from app.domain.task.teaching import TeachingContext
from tests.integration.migration_replay import database_at, seed_room

BEFORE = "e6b2d9f4a173"
AFTER = "b7d4e1a9c2f3"

PACK_PROMPT = "先写测试再写实现。"
PERSONA = "你是数据结构课的助教。"


def _board(db, n: int, *, space_teaching: dict, pack: dict) -> tuple[int, int]:
    """A 空间 and a 项目集 in it carrying ``pack``; ``n`` keeps ids apart."""
    space = db.fetchval(
        "INSERT INTO space (id, name, intro, description, avatar_id, enable_rank,"
        " task_templates, created_at, updated_at, teaching)"
        " VALUES ($1, '空间', '', '', 1, false, '[]', now(), now(), $2::json)"
        " RETURNING id",
        9100 + n * 10,
        json.dumps(space_teaching),
    )
    category = db.fetchval(
        "INSERT INTO space_categories (id, space_id, name, display_order,"
        " created_at, updated_at, resource_pack) VALUES ($1, $2, '分组', 0,"
        " now(), now(), $3::json) RETURNING id",
        9101 + n * 10,
        space,
        json.dumps(pack),
    )
    return space, category


def _task(db, task_id: int, space: int, category: int, override: dict | None) -> int:
    return db.fetchval(
        "INSERT INTO task (id, name, submitter_type, resubmittable, editable,"
        " intro, description, space_id, category_id, creator_id,"
        " default_deadline, approved, require_real_name, reject_reason,"
        " access_control_enabled, team_locking_policy, created_at, updated_at,"
        " protocol_override) VALUES ($1, '题', 0, true, true, '', '', $2, $3,"
        " 1, 30, 0, false, '', false, 'NO_LOCK', now(), now(), $4::json)"
        " RETURNING id",
        task_id,
        space,
        category,
        json.dumps(override) if override is not None else None,
    )


def _built_from(db, task: int, configuration: dict) -> uuid.UUID:
    """A project built from ``task`` before the move: its default teammate has
    what project creation copied into it."""
    project, _ = seed_room(db)
    agent = uuid.uuid4()
    db.execute(
        "INSERT INTO agent_instances (id, project_id, handle, display_name,"
        " configuration, created_at, updated_at)"
        " VALUES ($1, $2, 'cheese', 'cheese', $3::json, now(), now())",
        agent,
        project,
        json.dumps(configuration),
    )
    db.execute(
        "UPDATE projects SET external_task_id = $1, default_agent_instance_id = $2"
        " WHERE id = $3",
        task,
        agent,
        project,
    )
    return agent


def _rows(db) -> dict:
    """Everything the previous release reads that this migration could touch."""

    def table(sql: str) -> dict:
        return {r[0]: r[1] for r in db.fetch(sql)}

    return {
        "space": table("SELECT id, teaching::text FROM space"),
        "packs": table("SELECT id, resource_pack::text FROM space_categories"),
        "tasks": table("SELECT id, protocol_override::text FROM task"),
        "agents": table("SELECT id, configuration::text FROM agent_instances"),
    }


def _without_guidance_skills(override: str | None):
    """A 题目's override as the previous release reads it: the guidance's
    ``skills`` is a key it does not know, and no override reads as an empty one."""
    value = json.loads(override) if override is not None else {}
    teaching = value.get("teaching")
    if isinstance(teaching, dict):
        rest = {k: v for k, v in teaching.items() if k != "skills"}
        if rest:
            value["teaching"] = rest
        else:
            del value["teaching"]
    return value


def _reads(db, task: int, agent, room_skills: list[str]):
    """What the teammates of a project built from ``task`` read under this
    release: the lines of the guidance section and the default teammate's
    persona, the skills the default teammate carries, and the ones a teammate
    added later with no skills of its own carries."""
    row = db.fetchrow(
        "SELECT t.protocol_override::text AS o, c.resource_pack::text AS p,"
        " s.teaching::text AS s FROM task t"
        " JOIN space_categories c ON c.id = t.category_id"
        " JOIN space s ON s.id = t.space_id WHERE t.id = $1",
        task,
    )
    protocol = resolve(
        space=NS(teaching=json.loads(row["s"])),
        category=NS(resource_pack=json.loads(row["p"])),
        task=NS(protocol_override=json.loads(row["o"]) if row["o"] else None),
    )
    config = json.loads(
        db.fetchval(
            "SELECT configuration::text FROM agent_instances WHERE id = $1", agent
        )
    )
    section = (
        teaching_section(TeachingContext(course=None, teaching=protocol.teaching)) or ""
    )
    lines = {
        line.strip()
        for line in f"{section}\n{config.get('body') or ''}".splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    room = NS(skills=room_skills)
    course = protocol.teaching.skills
    return (
        lines,
        set(carried_skills(NS(configuration=config), room, course)),
        set(carried_skills(NS(configuration={}), room, course)),
    )


#: (空间's guidance, 资源包, 题目's own override).
CASES = {
    "a skills-only pack under a space saying everything": (
        {
            "system_prompt": "SPACE_REQUIREMENT: never reveal the solution",
            "learning_goals": ["GOAL"],
            "private_prompt": "SECRET",
        },
        {"compute_credits": 5, "skills": ["wolfram"]},
        None,
    ),
    "goals and no requirements: the default ones are in force": (
        {"learning_goals": ["write a loop"]},
        {"prompt": PACK_PROMPT},
        None,
    ),
    "a prompt and skills, a task saying nothing": (
        {"mode": "guide", "system_prompt": "SPACE_REQUIREMENT"},
        {"prompt": PACK_PROMPT, "skills": ["documents", "wolfram"]},
        None,
    ),
    "a task with guidance of its own": (
        {"system_prompt": "SPACE_REQUIREMENT"},
        {"prompt": PACK_PROMPT, "skills": ["wolfram"]},
        {"teaching": {"system_prompt": "TASK", "learning_goals": ["TASK_GOAL"]}},
    ),
}


def _seed(db) -> dict:
    built = {}
    for n, (name, (space_teaching, pack, override)) in enumerate(CASES.items()):
        space, category = _board(db, n, space_teaching=space_teaching, pack=pack)
        task = _task(db, 9500 + n, space, category, override)
        # What project creation did with the pack (`apply_protocol`): a preset
        # with no skills of its own, so the copies are all it carries.
        persona = PERSONA + (f"\n\n{pack['prompt']}" if "prompt" in pack else "")
        agent = _built_from(
            db, task, {"body": persona, "skills": list(pack.get("skills", []))}
        )
        built[name] = (task, agent)
    return built


def test_nothing_the_previous_release_reads_changes():
    with database_at(BEFORE) as db:
        _seed(db)
        before = _rows(db)

        db.upgrade(AFTER)

        after = _rows(db)
        assert after["space"] == before["space"]
        assert after["packs"] == before["packs"]
        assert after["agents"] == before["agents"]
        assert {k: _without_guidance_skills(v) for k, v in after["tasks"].items()} == {
            k: _without_guidance_skills(v) for k, v in before["tasks"].items()
        }


def test_every_teammate_reads_what_the_default_teammate_read_before():
    room = ["ROOM_SKILL"]
    with database_at(BEFORE) as db:
        built = {
            name: (task, agent, _reads(db, task, agent, room))
            for name, (task, agent) in _seed(db).items()
        }

        db.upgrade(AFTER)

        for name, (task, agent, (lines, skills, _)) in built.items():
            after_lines, after_skills, later = _reads(db, task, agent, room)
            assert after_lines == lines, name
            assert after_skills == skills, name
            # A teammate added after the project was built carries the pack's
            # skills too, on top of what it carried (the room's).
            pack = CASES[name][1]
            assert set(pack.get("skills", [])) | set(room) <= later, name


def test_a_task_with_its_own_pack_reads_only_its_own():
    with database_at(BEFORE) as db:
        space, category = _board(db, 0, space_teaching={}, pack={"skills": ["wolfram"]})
        task = _task(
            db,
            9500,
            space,
            category,
            {"resource_pack": {"compute_credits": 10, "skills": ["documents"]}},
        )

        db.upgrade(AFTER)

        override = json.loads(
            db.fetchval("SELECT protocol_override::text FROM task WHERE id = $1", task)
        )
        assert override["teaching"] == {"skills": ["documents"]}


def test_running_it_again_changes_nothing():
    with database_at(BEFORE) as db:
        _seed(db)
        db.upgrade(AFTER)
        once = _rows(db)

        db.downgrade(BEFORE)
        db.upgrade(AFTER)

        assert _rows(db) == once
