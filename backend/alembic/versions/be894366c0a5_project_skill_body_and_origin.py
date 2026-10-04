"""A project skill is a name, a use, one body and its files; and where it came from

The five fields (用途, inputs, steps, outputs, files) become 用途, one markdown
``body`` and files, the shape an imported SKILL.md already has. A saved skill's
inputs, steps and outputs are joined into its body under the headings the page
used to show them under, and every revision's content is rewritten the same way,
so restoring an old version still works.

``origin`` says who wrote a skill first: an AI teammate (``cheese``), a person
(``person``) or an import (``import``). Existing rows are told apart by whether
the handle that proposed them carries an agent-binding.

Revision ID: be894366c0a5
Revises: a7d3e91c5b20
"""

import json

import sqlalchemy as sa

from alembic import op

revision = "be894366c0a5"
down_revision = "a7d3e91c5b20"
branch_labels = None
depends_on = None


def _body(inputs: str, steps: str, outputs: str) -> str:
    inputs, steps, outputs = (
        (inputs or "").strip(),
        steps or "",
        (outputs or "").strip(),
    )
    if not inputs and not outputs:
        return steps
    parts = []
    if inputs:
        parts.append(f"## 需要的输入\n\n{inputs}")
    parts.append(f"## 步骤与规则\n\n{steps}")
    if outputs:
        parts.append(f"## 输出要求\n\n{outputs}")
    return "\n\n".join(parts)


def _loaded(content) -> dict:
    # A jsonb column reads back as text through some drivers.
    return json.loads(content) if isinstance(content, str) else dict(content)


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column(
        "project_skills",
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "project_skills",
        sa.Column("origin", sa.String(16), nullable=False, server_default="person"),
    )
    for id_, inputs, steps, outputs in bind.execute(
        sa.text("SELECT id, inputs, steps, outputs FROM project_skills")
    ).fetchall():
        bind.execute(
            sa.text("UPDATE project_skills SET body = :body WHERE id = :id"),
            {"id": id_, "body": _body(inputs, steps, outputs)},
        )
    bind.execute(
        sa.text(
            """
            UPDATE project_skills s SET origin = 'cheese'
            WHERE EXISTS (
                SELECT 1 FROM "user" u JOIN agent_bindings b ON b.user_id = u.id
                WHERE u.username = s.proposed_by
            )
            """
        )
    )
    for id_, content in bind.execute(
        sa.text("SELECT id, content FROM project_skill_revisions")
    ).fetchall():
        content = _loaded(content)
        content["body"] = _body(
            content.pop("inputs", ""),
            content.pop("steps", ""),
            content.pop("outputs", ""),
        )
        bind.execute(
            sa.text(
                "UPDATE project_skill_revisions SET content = CAST(:c AS jsonb)"
                " WHERE id = :id"
            ),
            {"id": id_, "c": json.dumps(content, ensure_ascii=False)},
        )
    op.drop_column("project_skills", "inputs")
    op.drop_column("project_skills", "steps")
    op.drop_column("project_skills", "outputs")


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("inputs", "steps", "outputs"):
        op.add_column(
            "project_skills",
            sa.Column(name, sa.Text(), nullable=False, server_default=""),
        )
    bind.execute(sa.text("UPDATE project_skills SET steps = body"))
    for id_, content in bind.execute(
        sa.text("SELECT id, content FROM project_skill_revisions")
    ).fetchall():
        content = _loaded(content)
        content["steps"] = content.pop("body", "")
        content["inputs"] = content["outputs"] = ""
        bind.execute(
            sa.text(
                "UPDATE project_skill_revisions SET content = CAST(:c AS jsonb)"
                " WHERE id = :id"
            ),
            {"id": id_, "c": json.dumps(content, ensure_ascii=False)},
        )
    op.drop_column("project_skills", "origin")
    op.drop_column("project_skills", "body")
