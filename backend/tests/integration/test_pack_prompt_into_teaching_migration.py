"""b7d4e1a9c2f3: a 资源包's prompt and skills become the 题目's AI guidance.

Each 题目 gets the prompt and skills of the 资源包 it was reading — its own
when it overrides the 资源包, its 项目集's otherwise. What every teammate reads
stays what it was: the same requirements, goals, private guidance and skills
reach the default teammate a project already has, only from the guidance
instead of from its own configuration, and a teammate added later now gets
them too.
"""

import json
import uuid
from types import SimpleNamespace as NS

from app.domain.agent.harness.opening import DEFAULT_TEACHING_PROMPT, teaching_section
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


def _override(db, task_id: int) -> dict:
    return json.loads(
        db.fetchval("SELECT protocol_override::text FROM task WHERE id = $1", task_id)
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


def _configuration(db, agent) -> dict:
    return json.loads(
        db.fetchval(
            "SELECT configuration::text FROM agent_instances WHERE id = $1", agent
        )
    )


def _reads(db, task: int, agent) -> tuple[set[str], set[str], set[str]]:
    """What the teammates of a project built from ``task`` read, as the
    platform resolves it: the lines of the guidance section and the default
    teammate's persona (headings aside — where a line sits may move), the
    skills the default teammate carries, and the ones a teammate added later
    carries."""
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
    config = _configuration(db, agent)
    section = (
        teaching_section(TeachingContext(course=None, teaching=protocol.teaching)) or ""
    )
    lines = {
        line.strip()
        for line in f"{section}\n{config.get('body') or ''}".splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    room = NS(skills=[])
    course = protocol.teaching.skills
    return (
        lines,
        set(carried_skills(NS(configuration=config), room, course)),
        set(carried_skills(NS(configuration={}), room, course)),
    )


#: (空间's guidance, 资源包, 题目's own override): the shapes a 题目 may be in.
CASES = {
    "a skills-only pack under a space's requirements": (
        {"system_prompt": "SPACE_REQUIREMENT: never reveal the solution"},
        {"compute_credits": 5, "skills": ["wolfram"]},
        None,
    ),
    "goals and no requirements: the default ones are in force": (
        {"learning_goals": ["write a loop"]},
        {"prompt": PACK_PROMPT},
        None,
    ),
    "a space saying everything, a task saying nothing": (
        {
            "mode": "guide",
            "system_prompt": "SPACE_REQUIREMENT",
            "learning_goals": ["GOAL"],
            "private_prompt": "SECRET",
        },
        {"prompt": PACK_PROMPT, "skills": ["documents", "wolfram"]},
        None,
    ),
    "a task with guidance of its own": (
        {"system_prompt": "SPACE_REQUIREMENT"},
        {"prompt": PACK_PROMPT, "skills": ["wolfram"]},
        {"teaching": {"system_prompt": "TASK", "learning_goals": ["TASK_GOAL"]}},
    ),
}


def test_every_teammate_reads_what_the_default_teammate_read_before():
    with database_at(BEFORE) as db:
        built = {}
        for n, (name, (space_teaching, pack, override)) in enumerate(CASES.items()):
            space, category = _board(db, n, space_teaching=space_teaching, pack=pack)
            task = _task(db, 9500 + n, space, category, override)
            # What project creation did with the pack (`apply_protocol`).
            persona = PERSONA + (f"\n\n{pack['prompt']}" if "prompt" in pack else "")
            skills = list(dict.fromkeys(["documents", *pack.get("skills", [])]))
            agent = _built_from(db, task, {"body": persona, "skills": skills})
            built[name] = (task, agent, _reads(db, task, agent))

        db.upgrade(AFTER)

        for name, (task, agent, before) in built.items():
            lines, skills, _ = before
            after_lines, after_skills, later = _reads(db, task, agent)
            assert after_lines == lines, name
            assert after_skills == skills, name
            # A teammate added after the project was built carries the pack's
            # skills too, which it never did.
            pack = CASES[name][1]
            assert set(pack.get("skills", [])) <= later, name


def test_the_default_requirements_stay_in_front_of_the_moved_prompt():
    with database_at(BEFORE) as db:
        space, category = _board(
            db,
            0,
            space_teaching={"learning_goals": ["write a loop"]},
            pack={"prompt": PACK_PROMPT},
        )
        task = _task(db, 9500, space, category, None)

        db.upgrade(AFTER)

        said = _override(db, task)["teaching"]["system_prompt"]
        assert said.startswith(DEFAULT_TEACHING_PROMPT) and said.endswith(PACK_PROMPT)


def test_a_task_with_its_own_pack_reads_only_its_own():
    with database_at(BEFORE) as db:
        space, category = _board(
            db,
            0,
            space_teaching={},
            pack={"prompt": PACK_PROMPT, "skills": ["wolfram"]},
        )
        task = _task(
            db,
            9500,
            space,
            category,
            {"resource_pack": {"compute_credits": 10, "prompt": "这道题自己的。"}},
        )

        db.upgrade(AFTER)

        assert _override(db, task)["teaching"] == {"system_prompt": "这道题自己的。"}


def test_the_previous_release_still_reads_the_pack_while_it_serves():
    """The migration runs under the previous release, which builds projects
    from the 资源包: its keys stay for a later migration to remove."""
    pack = {"compute_credits": 50, "prompt": PACK_PROMPT, "skills": ["wolfram"]}
    with database_at(BEFORE) as db:
        _, category = _board(db, 0, space_teaching={}, pack=pack)

        db.upgrade(AFTER)

        assert (
            json.loads(
                db.fetchval(
                    "SELECT resource_pack::text FROM space_categories WHERE id = $1",
                    category,
                )
            )
            == pack
        )


def test_an_edited_persona_and_a_projects_own_guidance_are_left_alone():
    with database_at(BEFORE) as db:
        space, category = _board(
            db,
            0,
            space_teaching={},
            pack={"prompt": PACK_PROMPT, "skills": ["wolfram"]},
        )
        task = _task(db, 9500, space, category, None)
        edited = _built_from(db, task, {"body": "改过的人设", "skills": ["wolfram"]})
        owned = _built_from(
            db, task, {"body": f"{PERSONA}\n\n{PACK_PROMPT}", "skills": ["wolfram"]}
        )
        db.execute(
            "UPDATE projects SET settings = $1::json"
            " WHERE default_agent_instance_id = $2",
            json.dumps({"teaching": {"system_prompt": "PROJECT_GUIDANCE"}}),
            owned,
        )

        db.upgrade(AFTER)

        assert _configuration(db, edited) == {"body": "改过的人设", "skills": []}
        assert _configuration(db, owned) == {
            "body": f"{PERSONA}\n\n{PACK_PROMPT}",
            "skills": ["wolfram"],
        }


def test_a_tasks_blank_default_teammate_becomes_inherit():
    """The page's 「继承」 stored ``""``, which read as "no teammate"; it now
    clears the 题目's own so its 项目集's default is in force."""
    with database_at(BEFORE) as db:
        space, category = _board(db, 0, space_teaching={}, pack={})
        task = _task(db, 9500, space, category, {"default_role": ""})

        db.upgrade(AFTER)

        assert "default_role" not in _override(db, task)


def test_running_it_again_changes_nothing():
    with database_at(BEFORE) as db:
        space, category = _board(
            db,
            0,
            space_teaching={"learning_goals": ["GOAL"], "private_prompt": "SECRET"},
            pack={"prompt": PACK_PROMPT, "skills": ["wolfram"]},
        )
        task = _task(db, 9500, space, category, None)
        agent = _built_from(
            db, task, {"body": f"{PERSONA}\n\n{PACK_PROMPT}", "skills": ["wolfram"]}
        )
        db.upgrade(AFTER)
        once = (_override(db, task), _configuration(db, agent))

        db.downgrade(BEFORE)
        db.upgrade(AFTER)

        assert (_override(db, task), _configuration(db, agent)) == once
