"""the team is the way into a project

A project's people are its team's members, read from the team, plus external
members who accepted an invitation. So:

- Every project belongs to a team. A project without one moves to its owner's
  personal team (created if the owner has none yet). One with no owner who is a
  real user is deleted when nothing was ever said in it; any other such project
  stops the upgrade, changing nothing, and is named in the error.
- ``project_members`` keeps only external members and AI teammates' seats. Rows
  for the project's owner or for people on its team are deleted — the team
  already covers them, and a stored copy would outlive their leaving it.
- Project roles go (``project_members.role``, ``project_invitations.role``).
- An invitation is unique per person only while it waits for an answer: the old
  (project, invitee, status) key refused a second accepted invitation, so a
  person removed from a project could not be invited back.
- Project join links and join requests go; external members come by invitation.
- The older team-project tables go (``team_project``, ``team_project_membership``).

Irreversible: rows are deleted and tables dropped.

Revision ID: 4b8e1f6c2a93
Revises: e2ccbf34a7bd
Create Date: 2026-09-24 06:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "4b8e1f6c2a93"
down_revision: str | Sequence[str] | None = "e2ccbf34a7bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _give_every_project_a_team(conn) -> None:
    # Owners without a personal team get one first, exactly as the app makes it.
    conn.execute(
        sa.text(
            """
            WITH owners AS (
                SELECT DISTINCT u.id AS user_id
                FROM projects p JOIN "user" u ON u.username = p.owner_handle
                WHERE p.team_id IS NULL AND u.deleted_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM team t
                      WHERE t.personal_owner_user_id = u.id AND t.deleted_at IS NULL
                  )
            ), made AS (
                INSERT INTO team (id, name, intro, description, avatar_id,
                                  personal_owner_user_id, created_at, updated_at)
                SELECT nextval('team_seq'), '个人', '', '', 1, user_id, now(), now()
                FROM owners
                RETURNING id, personal_owner_user_id
            )
            INSERT INTO team_user_relation (id, team_id, user_id, role,
                                            created_at, updated_at)
            SELECT nextval('team_user_relation_seq'), id, personal_owner_user_id, 0,
                   now(), now()
            FROM made
            """
        )
    )
    conn.execute(
        sa.text(
            """
            UPDATE projects p SET team_id = t.id
            FROM "user" u JOIN team t
              ON t.personal_owner_user_id = u.id AND t.deleted_at IS NULL
            WHERE p.team_id IS NULL AND u.username = p.owner_handle
              AND u.deleted_at IS NULL
            """
        )
    )
    orphans = conn.execute(
        sa.text(
            """
            SELECT p.id, p.name,
                   EXISTS (SELECT 1 FROM blocks b WHERE b.project_id = p.id) AS used
            FROM projects p WHERE p.team_id IS NULL
            """
        )
    ).all()
    used = [(str(pid), name) for pid, name, was_used in orphans if was_used]
    if used:
        raise RuntimeError(
            "projects with no team and no owner who is a user, but with "
            f"conversation in them — give each a team first: {used}"
        )
    if orphans:
        conn.execute(
            sa.text("DELETE FROM projects WHERE id = ANY(:ids)"),
            {"ids": [pid for pid, _, _ in orphans]},
        )


def _team_fk_name(conn) -> str:
    fks = sa.inspect(conn).get_foreign_keys("projects")
    return next(fk["name"] for fk in fks if fk["constrained_columns"] == ["team_id"])


def upgrade() -> None:
    conn = op.get_bind()
    _give_every_project_a_team(conn)
    op.drop_constraint(_team_fk_name(conn), "projects", type_="foreignkey")
    op.alter_column("projects", "team_id", nullable=False)
    op.create_foreign_key(
        "projects_team_id_fkey",
        "projects",
        "team",
        ["team_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    conn.execute(
        sa.text(
            """
            DELETE FROM project_members m
            USING projects p
            WHERE m.project_id = p.id
              AND (
                m.user_handle = p.owner_handle
                OR EXISTS (
                  SELECT 1 FROM team_user_relation r JOIN "user" u ON u.id = r.user_id
                  WHERE r.team_id = p.team_id AND r.deleted_at IS NULL
                    AND u.username = m.user_handle
                )
              )
            """
        )
    )
    op.drop_column("project_members", "role")
    op.drop_column("project_invitations", "role")
    op.drop_constraint("uq_project_invitation", "project_invitations", type_="unique")
    op.create_index(
        "uq_project_invitation_pending",
        "project_invitations",
        ["project_id", "invitee_handle"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.drop_table("project_join_requests")
    op.drop_constraint("projects_join_token_key", "projects", type_="unique")
    op.drop_column("projects", "join_token")
    op.drop_column("projects", "join_approval")

    op.drop_table("team_project_membership")
    op.drop_table("team_project")


def downgrade() -> None:
    raise RuntimeError(
        "irreversible: roster rows the team already covered were deleted, empty "
        "team-less projects were deleted, and the team-project tables were "
        "dropped. Restore from a backup taken before 4b8e1f6c2a93 if needed."
    )
