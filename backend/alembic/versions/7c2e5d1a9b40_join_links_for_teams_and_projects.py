"""join links: one shape for teams and projects

Teams get a ``handle``: a username's alphabet in the users' namespace, so a
handle names one user or one team. A personal team is named by its owner and
stores none. Every existing shared team is given ``team-<id>`` for its owner to
rename; the upgrade stops, changing nothing, if any of those is already a
username.

Teams and projects both get ``join_token`` (the link, permanent until reset)
and ``join_approval`` (whether joining through it waits for a manager, on by
default). Teams also get ``visibility``; every existing team stays public, which
is what they already were, so nothing drops out of search on deploy.

``project_join_links`` goes: its token moves onto the project, so a link that
has not expired yet keeps working at the same URL. Its expiry does not move —
reset replaces it — and an expired link is not carried over.

``project_join_requests`` holds the requests a project's link now produces
when approval is on.

Revision ID: 7c2e5d1a9b40
Revises: 2a88bca6e12e
Create Date: 2026-09-23 17:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7c2e5d1a9b40"
down_revision: str | Sequence[str] | None = "2a88bca6e12e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _join_columns(table: str) -> None:
    op.add_column(table, sa.Column("join_token", sa.String(64), nullable=True))
    op.add_column(
        table,
        sa.Column(
            "join_approval", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.create_unique_constraint(f"{table}_join_token_key", table, ["join_token"])


def upgrade() -> None:
    taken = (
        op.get_bind()
        .execute(
            sa.text(
                """
                SELECT u.username FROM "user" u JOIN team t
                  ON lower(u.username) = 'team-' || t.id
                WHERE t.personal_owner_user_id IS NULL
                """
            )
        )
        .scalars()
        .all()
    )
    if taken:
        raise RuntimeError(f"placeholder team handles already usernames: {taken}")
    op.add_column("team", sa.Column("handle", sa.String(32), nullable=True))
    op.execute(
        "UPDATE team SET handle = 'team-' || id WHERE personal_owner_user_id IS NULL"
    )
    op.create_index(
        "uq_team_handle_lower", "team", [sa.text("lower(handle)")], unique=True
    )
    op.create_check_constraint(
        "ck_team_handle_iff_shared",
        "team",
        "(personal_owner_user_id IS NULL) = (handle IS NOT NULL)",
    )
    op.add_column(
        "team",
        sa.Column("visibility", sa.String(16), nullable=False, server_default="public"),
    )
    _join_columns("team")
    _join_columns("projects")
    op.execute(
        """
        UPDATE projects p SET join_token = l.token
        FROM project_join_links l
        WHERE l.project_id = p.id AND l.expires_at > now()
        """
    )
    op.drop_table("project_join_links")

    op.create_table(
        "project_join_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requester_handle", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "requester_handle", "status", name="uq_project_join_request"
        ),
    )
    op.create_index(
        "ix_project_join_requests_project_id",
        "project_join_requests",
        ["project_id"],
    )
    op.create_index(
        "ix_project_join_requests_requester_handle",
        "project_join_requests",
        ["requester_handle"],
    )


def downgrade() -> None:
    op.drop_table("project_join_requests")
    op.create_table(
        "project_join_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for table in ("projects", "team"):
        op.drop_constraint(f"{table}_join_token_key", table, type_="unique")
        op.drop_column(table, "join_approval")
        op.drop_column(table, "join_token")
    op.drop_column("team", "visibility")
    op.drop_constraint("ck_team_handle_iff_shared", "team", type_="check")
    op.drop_index("uq_team_handle_lower", table_name="team")
    op.drop_column("team", "handle")
