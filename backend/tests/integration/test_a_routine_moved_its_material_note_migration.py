"""3e8b1d5a72c4 folds two columns into the rule's one free-text field.

The form now asks for a goal and, optionally, a note: 「使用哪些资料」 and
「结果放在频道的哪个目录」 are questions it no longer asks — each run decides
them. A rule that was told, back then, keeps what it was told: both texts move
into 「补充说明」, which is the column that used to hold the first of them.

The result line is the part worth asserting: a folder was stored as a path
(``周报``), and the teammate reads the note as prose, so the migration has to
spell out where that path went.

Replaying the migration must not fold the line twice — the folder column stays
where it is, so the second run has to recognize its own work. The row that
already reads like a folded one is how that is asserted: it is seeded folded,
and the migration runs once, over all of them.
"""

import uuid

from tests.integration.migration_replay import ReplayDatabase, database_at, seed_room

BEFORE = "0d7f3a9c41b6"
AFTER = "3e8b1d5a72c4"


def _rule(
    db: ReplayDatabase,
    project: uuid.UUID,
    room: uuid.UUID,
    *,
    scope: str,
    folder: str,
) -> uuid.UUID:
    rule = uuid.uuid4()
    db.execute(
        "INSERT INTO routines (id, project_id, topic_id, title, instructions,"
        " context_scope, output_dir, trigger, spec, timezone, state, agent_handle,"
        " owner_handle, proposed_by, next_run_at, revision, created_at, updated_at)"
        " VALUES ($1, $2, $3, '每周进展', '整理本周各房间的进展', $4, $5, 'schedule',"
        ' \'{"freq": "weekly", "weekdays": [0], "time": "09:00"}\'::jsonb,'
        " 'Asia/Shanghai', 'active', 'cheese-abc', 'alice', 'alice', NULL, 1,"
        " now(), now())",
        rule,
        project,
        room,
        scope,
        folder,
    )
    return rule


def test_two_columns_become_the_one_note_and_folding_it_twice_changes_nothing():
    with database_at(BEFORE) as db:
        project, room = seed_room(db)
        both = _rule(db, project, room, scope="本项目所有房间", folder="周报")
        folder_only = _rule(db, project, room, scope="", folder="日报")
        scope_only = _rule(db, project, room, scope="频道全部内容", folder="")
        neither = _rule(db, project, room, scope="", folder="")
        # 已经折过一遍的那条：迁移的 WHERE 认得出自己写下的话，不再折第二遍。
        already = _rule(
            db,
            project,
            room,
            scope="本项目所有房间\n结果保存到：频道 周报",
            folder="周报",
        )

        db.upgrade(AFTER)

        def note(rule: uuid.UUID) -> str:
            return db.fetchval("SELECT context_scope FROM routines WHERE id = $1", rule)

        assert note(both) == "本项目所有房间\n结果保存到：频道 周报"
        assert note(folder_only) == "结果保存到：频道 日报", "只有目录的那条也要写上"
        assert note(scope_only) == "频道全部内容", "没有目录就不添那一行"
        assert note(neither) == ""
        assert note(already) == "本项目所有房间\n结果保存到：频道 周报"

        # 目标那一格不是这次迁移的事，一个字没动。
        assert (
            db.fetchval("SELECT instructions FROM routines WHERE id = $1", both)
            == "整理本周各房间的进展"
        )
