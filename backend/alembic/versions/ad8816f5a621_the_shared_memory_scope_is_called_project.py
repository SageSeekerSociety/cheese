"""The shared memory scope is called project, not team

Revision ID: ad8816f5a621
Revises: be894366c0a5
Create Date: 2026-10-04
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ad8816f5a621"
down_revision: str | Sequence[str] | None = "4383bf20b465"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: 共享记忆那一档作用域的名字。文件树里它是目录前缀（`project/x.md`），
#: `memory_files.scope` 是这一列的值。
_OLD = "team"
_NEW = "project"


def _loaded(content) -> list:
    """`json` 列在某些驱动上读回来是字符串。"""
    return json.loads(content) if isinstance(content, str) else list(content or [])


def _flip(value) -> str:
    return _NEW if str(value) == _OLD else str(value)


def _rewrite_scopes(rows, column: str, bind) -> None:
    """把 `files` / `indexes` 里每个元素的 `scope` 从 team 改成 project。

    这两列会被 `apply` 重新解析回 `MemoryFileScope`（`migration_service`），
    而 `MemoryFileScope` 只认 `project`/`private`——留着 `team` 的行会让那次
    落笔直接抛异常，所以这里必须跟着改。
    """
    for id_, raw in rows:
        items = _loaded(raw)
        for item in items:
            if isinstance(item, dict) and str(item.get("scope")) == _OLD:
                item["scope"] = _NEW
        bind.execute(
            sa.text(
                f"UPDATE memory_migration_plans SET {column} = CAST(:c AS jsonb)"
                " WHERE id = :id"
            ),
            {"id": id_, "c": json.dumps(items, ensure_ascii=False)},
        )


def _rewrite_decisions(rows, bind) -> None:
    """`decisions` 是模型原样的那次回答，`apply` 重放它：`destination` 也走
    同一个枚举（`Destination`），`scope` 一旦是 `team` 同样要改，否则重放时
    那一刻抛 `migrationDestinationInvalid`。"""
    for id_, raw in rows:
        items = _loaded(raw)
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("destination") or "").strip().lower() == _OLD:
                item["destination"] = _NEW
            if str(item.get("scope") or "").strip().lower() == _OLD:
                item["scope"] = _NEW
        bind.execute(
            sa.text(
                "UPDATE memory_migration_plans SET decisions = CAST(:c AS jsonb)"
                " WHERE id = :id"
            ),
            {"id": id_, "c": json.dumps(items, ensure_ascii=False)},
        )


def _rewrite_files_changed(rows, bind) -> None:
    """`memory_dream_runs.files_changed` 是给总览房间看的一串路径
    （`project/x.md`），只是展示，但留着 `team/` 就和别处对不上了。"""
    prefix = f"{_OLD}/"
    for id_, raw in rows:
        items = _loaded(raw)
        flipped = [
            f"{_NEW}/{str(item)[len(prefix) :]}"
            if str(item).startswith(prefix)
            else item
            for item in items
        ]
        bind.execute(
            sa.text(
                "UPDATE memory_dream_runs SET files_changed = CAST(:c AS jsonb)"
                " WHERE id = :id"
            ),
            {"id": id_, "c": json.dumps(flipped, ensure_ascii=False)},
        )


def upgrade() -> None:
    """共享记忆那一档从 `team` 改叫 `project`。

    改名只动存储里的两个地方：`memory_files.scope` 这一列的值，和已经存下来的
    迁移计划里跟着它走的那几个字段（`files`/`indexes` 的 `scope`、`decisions`
    的 `destination`/`scope`）。`memory_files.scope` 是 `varchar(8)`，没有 CHECK
    约束，`project`（7 个字符）放得下。

    `report` 那份正文和 `memory_dream_runs.summary` **不动**：它们里面嵌了记忆
    正文，逐个替 "team" 会连正文一起改坏；它们是当时那一版的样子，留着。

    会话机上的 `~/.cheese/memory/team/` 不在这里处理，由 runner 落一轮
    `_migrate_legacy_scope` 就地搬成 `project/`（见 `claude_code/runner.py`）。
    """
    bind = op.get_bind()
    op.execute(f"UPDATE memory_files SET scope = '{_NEW}' WHERE scope = '{_OLD}'")
    _rewrite_scopes(
        bind.execute(
            sa.text("SELECT id, files FROM memory_migration_plans")
        ).fetchall(),
        "files",
        bind,
    )
    _rewrite_scopes(
        bind.execute(
            sa.text("SELECT id, indexes FROM memory_migration_plans")
        ).fetchall(),
        "indexes",
        bind,
    )
    _rewrite_decisions(
        bind.execute(
            sa.text("SELECT id, decisions FROM memory_migration_plans")
        ).fetchall(),
        bind,
    )
    _rewrite_files_changed(
        bind.execute(
            sa.text("SELECT id, files_changed FROM memory_dream_runs")
        ).fetchall(),
        bind,
    )


def downgrade() -> None:
    """改回 `team`，把升级动过的两个地方一起还原。"""
    bind = op.get_bind()
    op.execute(f"UPDATE memory_files SET scope = '{_OLD}' WHERE scope = '{_NEW}'")

    def back(items: list) -> list:
        for item in items:
            if isinstance(item, dict):
                if str(item.get("scope")) == _NEW:
                    item["scope"] = _OLD
                if str(item.get("destination") or "").strip().lower() == _NEW:
                    item["destination"] = _OLD
        return items

    for column in ("files", "indexes", "decisions"):
        for id_, raw in bind.execute(
            sa.text(f"SELECT id, {column} FROM memory_migration_plans")
        ).fetchall():
            bind.execute(
                sa.text(
                    f"UPDATE memory_migration_plans SET {column} = CAST(:c AS jsonb)"
                    " WHERE id = :id"
                ),
                {"id": id_, "c": json.dumps(back(_loaded(raw)), ensure_ascii=False)},
            )
    prefix = f"{_NEW}/"
    for id_, raw in bind.execute(
        sa.text("SELECT id, files_changed FROM memory_dream_runs")
    ).fetchall():
        items = [
            f"{_OLD}/{str(i)[len(prefix) :]}" if str(i).startswith(prefix) else i
            for i in _loaded(raw)
        ]
        bind.execute(
            sa.text(
                "UPDATE memory_dream_runs SET files_changed = CAST(:c AS jsonb)"
                " WHERE id = :id"
            ),
            {"id": id_, "c": json.dumps(items, ensure_ascii=False)},
        )
