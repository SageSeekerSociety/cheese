"""e5f1a9c3d7b2: guidance keeps what the teacher wrote, in the new shape.

The week, the topics and the things to avoid are folded into the requirements;
a 项目集's guidance moves onto its 题目 that had none of their own.
"""

import json

from tests.integration.migration_replay import database_at

BEFORE = "1c646deda360"
AFTER = "e5f1a9c3d7b2"


def _space(db, teaching: dict) -> int:
    return db.fetchval(
        "INSERT INTO space (id, name, intro, description, avatar_id, enable_rank,"
        " task_templates, created_at, updated_at, teaching)"
        " VALUES (9001, '课', '', '', 1, false, '[]', now(), now(), $1::json)"
        " RETURNING id",
        json.dumps(teaching),
    )


def test_old_guidance_is_folded_into_requirements_and_moves_off_categories():
    with database_at(BEFORE) as db:
        space = _space(
            db,
            {
                "system_prompt": "第 {current_week} 周，只讲 {allowed_topics}。",
                "current_week": 3,
                "allowed_topics": ["循环"],
                "avoid_in_code": ["递归"],
                "material_ids": [5],
            },
        )
        category = db.fetchval(
            "INSERT INTO space_categories (id, space_id, name, display_order,"
            " created_at, updated_at, teaching) VALUES (9002, $1, '实验', 0, now(),"
            " now(), $2::json) RETURNING id",
            space,
            json.dumps({"system_prompt": "先写测试。", "current_week": 4}),
        )
        bare, own = (
            db.fetchval(
                "INSERT INTO task (id, name, submitter_type, resubmittable, editable,"
                " intro, description, space_id, category_id, creator_id,"
                " default_deadline, approved, require_real_name, reject_reason,"
                " access_control_enabled, team_locking_policy, created_at, updated_at,"
                " protocol_override) VALUES ($5, $1, 0, true, true, '', '', $2, $3,"
                " 1, 30, 0, false, '', false, 'NO_LOCK', now(), now(), $4::json)"
                " RETURNING id",
                name,
                space,
                category,
                json.dumps(override) if override is not None else None,
                task_id,
            )
            for task_id, name, override in (
                (9003, "没有自己的", None),
                (9004, "有自己的", {"teaching": {"system_prompt": "本题单独的。"}}),
            )
        )

        db.upgrade(AFTER)

        on_space = json.loads(
            db.fetchval("SELECT teaching::text FROM space WHERE id = $1", space)
        )
        assert on_space == {
            "system_prompt": "第 3 周，只讲 循环。\n暂时不要用到：递归。",
            "material_ids": [5],
        }
        assert (
            db.fetchval(
                "SELECT teaching::text FROM space_categories WHERE id = $1", category
            )
            == "{}"
        )
        moved = json.loads(
            db.fetchval("SELECT protocol_override::text FROM task WHERE id = $1", bare)
        )
        assert moved["teaching"] == {"system_prompt": "先写测试。\n当前是第 4 周。"}
        kept = json.loads(
            db.fetchval("SELECT protocol_override::text FROM task WHERE id = $1", own)
        )
        assert kept["teaching"] == {"system_prompt": "本题单独的。"}
