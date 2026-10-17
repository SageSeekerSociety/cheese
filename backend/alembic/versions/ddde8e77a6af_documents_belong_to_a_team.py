"""Documents belong to a team; the team knowledge base becomes documents

Revision ID: ddde8e77a6af
Revises: 1478f65e1004
Create Date: 2026-10-18

A document belonged to a project. It now belongs to a team — a person's own to
their personal team — and to at most one project of it (#3774): a project's
documents take their project's team, and a document with no project is the
team's own, read and written by its members.

The team knowledge base (``knowledge``) is replaced by those documents. Each of
its live entries becomes a team document: a text entry's text, a link entry's
link, a code entry's code, and for an uploaded file its name (the file is not
carried over), each after the entry's description. A course's teaching settings
that named knowledge entries (``knowledge_ids``, in spaces, 项目集 and a 赛题's
override) name those documents instead (``document_ids``).

The release before this one is still serving while it runs, and it creates
documents without a team: a trigger fills the team from the project on insert,
so its writes keep passing the new NOT NULL. Its knowledge pages keep reading
their tables too. The trigger and the knowledge tables go in the next release's
migration, once nothing that predates this one is serving.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from migration_helpers import with_lock_retries

from alembic import op

revision: str = "ddde8e77a6af"
down_revision: str | Sequence[str] | None = "1478f65e1004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INDEX = "ix_documents_team_id"


def upgrade() -> None:
    with_lock_retries("documents, team")
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("documents")}
    if "team_id" not in columns:
        op.add_column("documents", sa.Column("team_id", sa.BigInteger(), nullable=True))
    # The release still serving inserts a project's document without its team.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION documents_team_from_project() RETURNS trigger AS $$
        BEGIN
            IF NEW.team_id IS NULL AND NEW.project_id IS NOT NULL THEN
                SELECT team_id INTO NEW.team_id FROM projects WHERE id = NEW.project_id;
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER IF EXISTS documents_team_from_project ON documents")
    op.execute(
        "CREATE TRIGGER documents_team_from_project BEFORE INSERT ON documents "
        "FOR EACH ROW EXECUTE FUNCTION documents_team_from_project()"
    )
    op.execute(
        "UPDATE documents d SET team_id = p.team_id "
        "FROM projects p WHERE p.id = d.project_id AND d.team_id IS NULL"
    )
    # migration-safety: allow alter-column-existing — a few thousand rows, scanned once under the lock this migration already holds; the trigger above keeps the old release's inserts valid
    op.alter_column("documents", "team_id", nullable=False)
    op.alter_column("documents", "project_id", nullable=True)
    if not bind.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'fk_documents_team_id_team'"
        )
    ).scalar():
        op.create_foreign_key(
            "fk_documents_team_id_team",
            "documents",
            "team",
            ["team_id"],
            ["id"],
            ondelete="CASCADE",
            postgresql_not_valid=True,
        )
    if "knowledge" in sa.inspect(bind).get_table_names() and not _converted(bind):
        _knowledge_into_documents(bind)
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TABLE documents VALIDATE CONSTRAINT fk_documents_team_id_team"
        )
        if _invalid(INDEX):
            op.drop_index(INDEX, table_name="documents", postgresql_concurrently=True)
        op.create_index(
            INDEX,
            "documents",
            ["team_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def _invalid(name: str) -> bool:
    return bool(
        op.get_bind()
        .execute(
            sa.text(
                "SELECT NOT i.indisvalid FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = :name"
            ),
            {"name": name},
        )
        .scalar()
    )


def _converted(bind) -> bool:
    """A rerun after a failed index build: the entries are documents already.
    Before this migration every document had a project."""
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM documents WHERE project_id IS NULL LIMIT 1")
        ).scalar()
    )


def _markdown(kind: str, name: str, description: str, content: dict) -> str:
    # The title is the document's own; the page draws it above the text.
    parts: list[str] = []
    if description.strip():
        parts.append(description.strip())
    if kind == "TEXT" and content.get("text"):
        parts.append(str(content["text"]))
    elif kind == "LINK" and content.get("url"):
        parts.append(f"[{name}]({content['url']})")
    elif kind == "CODE" and content.get("code"):
        parts.append(f"```{content.get('language') or ''}\n{content['code']}\n```")
    elif kind == "MATERIAL":
        parts.append(f"原知识库附件：{content.get('name') or name}（文件没有迁移过来）")
    return "\n\n".join(parts) + "\n"


def _knowledge_into_documents(bind) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT k.id, k.name, k.description, k.type, k.content, k.team_id, "
            "k.created_at, k.updated_at, u.username "
            "FROM knowledge k JOIN team t ON t.id = k.team_id "
            'LEFT JOIN "user" u ON u.id = k.created_by_id '
            "WHERE k.deleted_at IS NULL ORDER BY k.id"
        )
    ).all()
    made: dict[int, str] = {}
    for (
        kid,
        name,
        description,
        kind,
        content,
        team_id,
        created,
        updated,
        author,
    ) in rows:
        content = content if isinstance(content, dict) else json.loads(content or "{}")
        made[kid] = str(
            bind.execute(
                sa.text(
                    "INSERT INTO documents "
                    "(id, team_id, project_id, kind, title, content, version, "
                    "author, created_at, updated_at) VALUES "
                    "(gen_random_uuid(), :team, NULL, 'doc', :title, :content, 0, "
                    ":author, :created, :updated) RETURNING id"
                ),
                {
                    "team": team_id,
                    "title": (name or "")[:200],
                    "content": _markdown(kind, name or "", description or "", content),
                    "author": author or "system",
                    "created": created,
                    "updated": updated,
                },
            ).scalar_one()
        )
    _repoint_teaching(bind, made)


def _repoint(teaching: object, made: dict[int, str]) -> object:
    if not isinstance(teaching, dict) or "knowledge_ids" not in teaching:
        return teaching
    out = {k: v for k, v in teaching.items() if k != "knowledge_ids"}
    ids = teaching.get("knowledge_ids") or []
    out["document_ids"] = [made[i] for i in ids if isinstance(i, int) and i in made]
    return out


def _repoint_teaching(bind, made: dict[int, str]) -> None:
    for table in ("space", "space_categories"):
        for row_id, teaching in bind.execute(
            sa.text(
                f"SELECT id, teaching FROM {table} WHERE teaching::text LIKE '%knowledge_ids%'"
            )
        ).all():
            value = teaching if isinstance(teaching, dict) else json.loads(teaching)
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET teaching = CAST(:v AS JSON) WHERE id = :id"
                ),
                {
                    "v": json.dumps(_repoint(value, made), ensure_ascii=False),
                    "id": row_id,
                },
            )
    for row_id, override in bind.execute(
        sa.text(
            "SELECT id, protocol_override FROM task "
            "WHERE protocol_override::text LIKE '%knowledge_ids%'"
        )
    ).all():
        value = override if isinstance(override, dict) else json.loads(override)
        value = {**value, "teaching": _repoint(value.get("teaching"), made)}
        bind.execute(
            sa.text(
                "UPDATE task SET protocol_override = CAST(:v AS JSON) WHERE id = :id"
            ),
            {"v": json.dumps(value, ensure_ascii=False), "id": row_id},
        )


def downgrade() -> None:
    raise NotImplementedError("The knowledge base is gone; restore from a backup")
