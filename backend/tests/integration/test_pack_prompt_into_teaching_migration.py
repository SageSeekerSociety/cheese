"""b7d4e1a9c2f3: a 资源包's prompt and skills become the 题目's AI guidance.

Each 题目 gets the prompt and skills of the 资源包 it was reading — its own
when it overrides the 资源包, its 项目集's otherwise — and every 资源包 keeps
only its compute.
"""

import json

from tests.integration.migration_replay import database_at

BEFORE = "e6b2d9f4a173"
AFTER = "b7d4e1a9c2f3"


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


def test_a_pack_prompt_and_skills_move_into_the_guidance_of_the_task_reading_it():
    with database_at(BEFORE) as db:
        space = db.fetchval(
            "INSERT INTO space (id, name, intro, description, avatar_id, enable_rank,"
            " task_templates, created_at, updated_at)"
            " VALUES (9101, '空间', '', '', 1, false, '[]', now(), now())"
            " RETURNING id"
        )
        category = db.fetchval(
            "INSERT INTO space_categories (id, space_id, name, display_order,"
            " created_at, updated_at, resource_pack) VALUES (9102, $1, '分组', 0,"
            " now(), now(), $2::json) RETURNING id",
            space,
            json.dumps(
                {
                    "compute_credits": 50,
                    "prompt": "先写测试再写实现。",
                    "skills": ["documents", "wolfram"],
                }
            ),
        )
        inherits = _task(
            db,
            9103,
            space,
            category,
            {"teaching": {"system_prompt": "只用标准库。", "skills": ["documents"]}},
        )
        bare = _task(db, 9104, space, category, None)
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
        assert _override(db, bare)["teaching"] == {
            "system_prompt": "先写测试再写实现。",
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
