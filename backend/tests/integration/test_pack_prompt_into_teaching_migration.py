"""b7d4e1a9c2f3: a 资源包's prompt and skills become the 题目's AI guidance.

Each 题目 gets the prompt and skills of the 资源包 it was reading — its own
when it overrides the 资源包, its 项目集's otherwise — and every 资源包 keeps
only its compute. What each teammate reads stays what it was: a 题目 that had
no guidance of its own keeps the 空间's learning goals and private guidance,
and a default teammate that had the prompt in its persona reads it once.
"""

import json
import uuid

from tests.integration.migration_replay import database_at, seed_room

BEFORE = "e6b2d9f4a173"
AFTER = "b7d4e1a9c2f3"

PACK_PROMPT = "先写测试再写实现。"


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


def _board(db, *, space_teaching: dict) -> tuple[int, int]:
    space = db.fetchval(
        "INSERT INTO space (id, name, intro, description, avatar_id, enable_rank,"
        " task_templates, created_at, updated_at, teaching)"
        " VALUES (9101, '空间', '', '', 1, false, '[]', now(), now(), $1::json)"
        " RETURNING id",
        json.dumps(space_teaching),
    )
    category = db.fetchval(
        "INSERT INTO space_categories (id, space_id, name, display_order,"
        " created_at, updated_at, resource_pack) VALUES (9102, $1, '分组', 0,"
        " now(), now(), $2::json) RETURNING id",
        space,
        json.dumps(
            {
                "compute_credits": 50,
                "prompt": PACK_PROMPT,
                "skills": ["documents", "wolfram"],
            }
        ),
    )
    return space, category


def test_a_pack_prompt_and_skills_move_into_the_guidance_of_the_task_reading_it():
    with database_at(BEFORE) as db:
        space, category = _board(db, space_teaching={"mode": "guide"})
        inherits = _task(
            db,
            9103,
            space,
            category,
            {"teaching": {"system_prompt": "只用标准库。", "skills": ["documents"]}},
        )
        own_pack = _task(
            db,
            9105,
            space,
            category,
            {"resource_pack": {"compute_credits": 10, "prompt": "这道题自己的。"}},
        )

        db.upgrade(AFTER)

        assert _override(db, inherits)["teaching"] == {
            "system_prompt": "只用标准库。\n\n先写测试再写实现。",
            "skills": ["documents", "wolfram"],
        }
        # A 题目 with its own 资源包 was never reading its 项目集's.
        assert _override(db, own_pack) == {
            "resource_pack": {"compute_credits": 10},
            "teaching": {"system_prompt": "这道题自己的。"},
        }
        assert json.loads(
            db.fetchval(
                "SELECT resource_pack::text FROM space_categories WHERE id = $1",
                category,
            )
        ) == {"compute_credits": 50}


def test_a_task_without_guidance_keeps_reading_the_spaces_goals_and_private_guidance():
    """It read the 空间's guidance alone; once it has guidance of its own the
    goals and private guidance come from it, so they move with the pack."""
    with database_at(BEFORE) as db:
        space, category = _board(
            db,
            space_teaching={
                "mode": "guide",
                "learning_goals": ["能自己写出循环"],
                "private_prompt": "参考答案在讲义第 12 页。",
            },
        )
        bare = _task(db, 9104, space, category, None)

        db.upgrade(AFTER)

        assert _override(db, bare)["teaching"] == {
            "system_prompt": PACK_PROMPT,
            "skills": ["documents", "wolfram"],
            "learning_goals": ["能自己写出循环"],
            "private_prompt": "参考答案在讲义第 12 页。",
        }


def test_the_pack_prompt_leaves_the_default_teammates_persona():
    """The guidance says it every turn now: the copy that went into the
    persona when the project was built comes out, and an edited persona that no
    longer holds it is left alone."""
    with database_at(BEFORE) as db:
        space, category = _board(db, space_teaching={})
        task = _task(db, 9106, space, category, None)
        personas = {}
        for body in ("你是数据结构课的助教。\n\n" + PACK_PROMPT, "改过的人设"):
            project, _ = seed_room(db)
            agent = uuid.uuid4()
            db.execute(
                "INSERT INTO agent_instances (id, project_id, handle, display_name,"
                " configuration, created_at, updated_at)"
                " VALUES ($1, $2, 'cheese', 'cheese', $3::json, now(), now())",
                agent,
                project,
                json.dumps({"body": body, "skills": ["documents"]}),
            )
            db.execute(
                "UPDATE projects SET external_task_id = $1,"
                " default_agent_instance_id = $2 WHERE id = $3",
                task,
                agent,
                project,
            )
            personas[body] = agent

        db.upgrade(AFTER)

        def body_of(agent) -> str:
            return json.loads(
                db.fetchval(
                    "SELECT configuration::text FROM agent_instances WHERE id = $1",
                    agent,
                )
            )["body"]

        built, edited = personas.values()
        assert body_of(built) == "你是数据结构课的助教。"
        assert body_of(edited) == "改过的人设"
