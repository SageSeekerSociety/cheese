"""``ad8816f5a621``：已经在库里的 ``team`` 记忆，改叫 ``project``，正文一个字没动。

一次 ``alembic upgrade head`` 跑在空库上什么都不证明——空库里没有一行 ``scope='team'``，
迁移里每一句都可以是错的而测试照绿。这里把**迁移前的形状**用字面量塞进真表，再跑迁移
自己的 ``upgrade()``，那是唯一能看见它对历史做了什么的方式。

迁移前的行是**字面量**塞的，不是走今天任何一条写入路径造出来的：``MemoryFileScope``
今天只有 ``project``/``private``，老的那一档它已经说不出、也写不出来了（这正是这次
改名的目的）。每段字面量都照着迁移前那个 writer 抄，名字写在旁边，好拿 ``git show``
去核对，而不是拿「迁移现在应该产出什么」去核对。

顺便钉住**没动的两处**：``report`` 和 ``summary`` 里面嵌着记忆正文，逐个替 ``team``
会连正文一起改坏，所以它们原样留着——这条用例就是那句「故意不动」的见证。
"""

import importlib.util
import json
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "ad8816f5a621_the_shared_memory_scope_is_called_project.py"
)

#: 迁移前的 ``memory_files.scope``。今天的 ``MemoryFileScope`` 已经没有这个成员了。
_TEAM = "team"
_PROJECT = "project"

#: 迁移前一份计划长什么样。``files``/``indexes`` 的元素带 ``scope``，``decisions``
#: 带 ``destination``/``scope``——``migration_service.apply`` 会照原样重放它们，留着
#: ``team`` 会让那一刻抛 ``migrationDestinationInvalid``。
_PRE_FILES = [
    {"scope": "team", "owner": "", "path": "a.md", "content": "正文", "is_new": True},
    {
        "scope": "private",
        "owner": "alice",
        "path": "b.md",
        "content": "x",
        "is_new": True,
    },
]
_PRE_INDEXES = [{"scope": "team", "owner": "", "content": "- [a](a.md)\n"}]
_PRE_DECISIONS = [
    {"destination": "team", "scope": "team", "reason": "归项目"},
    {"destination": "drop", "reason": "过期"},
]
_PRE_REPORT = "把这条留在 team 里，正文提到 team 这个词也不许被改。"
_PRE_SUMMARY = "整理完，动了 project/x.md 和 team/y.md"


def _load():
    spec = importlib.util.spec_from_file_location("_scope_rename", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_team_memory_becomes_a_project_one_and_history_is_not_rewritten(
    db_session, _portal
):
    migration = _load()
    file_id, private_id, plan_id, run_id = (uuid.uuid4() for _ in range(4))

    async def run():
        from app.domain.project.services import ProjectService
        from tests.integration.conftest import registered

        await registered(db_session, "alice")
        # github_app 建的 project 不去 provision 真仓库（forgejo 才会），
        # 这条用例只要一个能挂记忆的 project 行。
        project = await ProjectService(db_session).create(
            name="记忆改名", owner_handle="alice", forge_kind="github_app"
        )
        await db_session.flush()
        pid = project.id

        connection = await db_session.connection()

        def check(conn):
            conn.execute(
                sa.text(
                    "INSERT INTO memory_files (id, project_id, scope, owner_handle,"
                    " path, content, version, updated_by, created_at, updated_at)"
                    " VALUES (:id, :pid, :scope, '', 'a.md', '正文', 1, '',"
                    " now(), now())"
                ),
                {"id": file_id, "pid": pid, "scope": _TEAM},
            )
            # 同一张表里的 private 那一档不该被动。
            conn.execute(
                sa.text(
                    "INSERT INTO memory_files (id, project_id, scope, owner_handle,"
                    " path, content, version, updated_by, created_at, updated_at)"
                    " VALUES (:id, :pid, 'private', 'alice', 'b.md', '私', 1, '',"
                    " now(), now())"
                ),
                {"id": private_id, "pid": pid},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO memory_migration_plans (id, project_id, status,"
                    " sources_digest, report, sources, source_ids, decisions, files,"
                    " indexes, suggestions, created_by, approved_by, summary,"
                    " created_at, updated_at)"
                    " VALUES (:id, :pid, 'draft', '', :report, '[]', '[]',"
                    " CAST(:decisions AS jsonb), CAST(:files AS jsonb),"
                    " CAST(:indexes AS jsonb), '[]', '', '', '', now(), now())"
                ),
                {
                    "id": plan_id,
                    "pid": pid,
                    "report": _PRE_REPORT,
                    "decisions": json.dumps(_PRE_DECISIONS, ensure_ascii=False),
                    "files": json.dumps(_PRE_FILES, ensure_ascii=False),
                    "indexes": json.dumps(_PRE_INDEXES, ensure_ascii=False),
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO memory_dream_runs (id, project_id, status,"
                    " tokens_at_start, started_at, finished_at, summary,"
                    " files_changed, created_at, updated_at)"
                    " VALUES (:id, :pid, 'completed', 0, now(), now(), :summary,"
                    " CAST(:files AS jsonb), now(), now())"
                ),
                {
                    "id": run_id,
                    "pid": pid,
                    "summary": _PRE_SUMMARY,
                    "files": json.dumps(
                        ["project/x.md", "team/y.md", "private/alice/z.md"],
                        ensure_ascii=False,
                    ),
                },
            )

            # 迁移真正跑到库里——不是照抄一份 SQL。
            migration.op = Operations(MigrationContext.configure(conn))
            migration.upgrade()

            assert (
                conn.execute(
                    sa.text("SELECT scope FROM memory_files WHERE id = :id"),
                    {"id": file_id},
                ).scalar_one()
                == _PROJECT
            )
            # private 那一档一个字节都没动。
            assert (
                conn.execute(
                    sa.text("SELECT scope FROM memory_files WHERE id = :id"),
                    {"id": private_id},
                ).scalar_one()
                == "private"
            )

            plan = (
                conn.execute(
                    sa.text(
                        "SELECT report, decisions, files, indexes"
                        " FROM memory_migration_plans WHERE id = :id"
                    ),
                    {"id": plan_id},
                )
                .mappings()
                .one()
            )
            # report 正文原样留着——「故意不动」的那一处。
            assert plan["report"] == _PRE_REPORT
            files = _as_list(plan["files"])
            assert files[0]["scope"] == _PROJECT
            assert files[1]["scope"] == "private"
            assert _as_list(plan["indexes"])[0]["scope"] == _PROJECT
            decisions = _as_list(plan["decisions"])
            assert decisions[0]["destination"] == _PROJECT
            assert decisions[0]["scope"] == _PROJECT
            assert decisions[1]["destination"] == "drop"
            assert decisions[0]["reason"] == "归项目"

            run = (
                conn.execute(
                    sa.text(
                        "SELECT summary, files_changed FROM memory_dream_runs"
                        " WHERE id = :id"
                    ),
                    {"id": run_id},
                )
                .mappings()
                .one()
            )
            # summary 是给总览房间看的那段文字，嵌着路径，同样不许被替。
            assert run["summary"] == _PRE_SUMMARY
            assert _as_list(run["files_changed"]) == [
                "project/x.md",
                "project/y.md",
                "private/alice/z.md",
            ]

            # 反向也要能跑：downgrade 把动过的几处一起还原。
            migration.downgrade()
            assert (
                conn.execute(
                    sa.text("SELECT scope FROM memory_files WHERE id = :id"),
                    {"id": file_id},
                ).scalar_one()
                == _TEAM
            )
            plan = (
                conn.execute(
                    sa.text(
                        "SELECT decisions, files, indexes"
                        " FROM memory_migration_plans WHERE id = :id"
                    ),
                    {"id": plan_id},
                )
                .mappings()
                .one()
            )
            assert _as_list(plan["files"])[0]["scope"] == _TEAM
            assert _as_list(plan["indexes"])[0]["scope"] == _TEAM
            assert _as_list(plan["decisions"])[0]["destination"] == _TEAM
            assert (
                _as_list(
                    conn.execute(
                        sa.text(
                            "SELECT files_changed FROM memory_dream_runs WHERE id = :id"
                        ),
                        {"id": run_id},
                    ).scalar_one()
                )[1]
                == "team/y.md"
            )

        await connection.run_sync(check)

    _portal.call(run)


def _as_list(value) -> list:
    """json 列在某些驱动上读回来是字符串。"""
    return json.loads(value) if isinstance(value, str) else list(value or [])
