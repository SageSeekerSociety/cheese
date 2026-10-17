"""0d7f3a9c41b6 moves a rule that was waiting for a person into 已暂停.

「待确认」 is gone: a rule is saved and running, whoever made it. A row still
sitting in `draft` names a state nothing renders any more, and it is the one
state that has to be moved rather than left — nobody ever approved that rule to
run, and it has no `next_run_at` to fire at either. The migration parks it, so
whoever wants it turns it on with 「恢复」.

The three columns the confirm step added stay where they are (``proposed_by``
is the one ``created_by`` now maps onto); this asserts the data, not the
schema, which is why it reads the old names.
"""

import uuid

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "49a2e86fa493"
AFTER = "0d7f3a9c41b6"


def _rule(
    db: ReplayDatabase, project: uuid.UUID, room: uuid.UUID, state: str
) -> uuid.UUID:
    rule = uuid.uuid4()
    db.execute(
        "INSERT INTO routines (id, project_id, topic_id, title, instructions,"
        " trigger, spec, timezone, state, agent_handle, owner_handle,"
        " proposed_by, next_run_at, revision, created_at, updated_at)"
        " VALUES ($1, $2, $3, '每周进展', '整理本周各房间的进展', 'schedule',"
        ' \'{"freq": "weekly", "weekdays": [0], "time": "09:00"}\'::jsonb,'
        " 'Asia/Shanghai', $4, 'cheese-abc', 'alice', 'cheese-abc', NULL, 1,"
        " now(), now())",
        rule,
        project,
        room,
        state,
    )
    return rule


def test_a_rule_that_waited_for_a_person_is_parked_not_started():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        waited = _rule(db, project, room, "draft")
        running = _rule(db, project, room, "active")
        parked = _rule(db, project, room, "paused")

        db.upgrade(AFTER)

        state = lambda rule: db.fetchval(  # noqa: E731 — 三行都读同一列
            "SELECT state FROM routines WHERE id = $1", rule
        )
        assert state(waited) == "paused"
        assert state(running) == "active", "在跑的规则被迁移碰了"
        assert state(parked) == "paused"

        # 别的列一点没动：名字、目标、谁建的、谁执行都还在原位。
        row = db.fetchrow(
            "SELECT title, instructions, proposed_by, agent_handle, owner_handle"
            " FROM routines WHERE id = $1",
            waited,
        )
        assert row["title"] == "每周进展"
        assert row["instructions"] == "整理本周各房间的进展"
        assert row["proposed_by"] == "cheese-abc"
        assert row["agent_handle"] == "cheese-abc"
        assert row["owner_handle"] == "alice"

        # 反向走一遍再上来，数据还是这样：这一步没有可逆的旧值，重放安全。
        db.downgrade(BEFORE)
        db.upgrade(AFTER)
        assert state(waited) == "paused"
        assert state(running) == "active"
