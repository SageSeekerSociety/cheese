"""One personal team per user

Revision ID: 872d78113ce9
Revises: c1a7e4d29b58
Create Date: 2026-09-27
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "872d78113ce9"
down_revision: str | Sequence[str] | None = "c1a7e4d29b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Everything that names a team by id, and so has to follow a merged team to the
# one that is kept. The FK columns and the bare ones (no FK: device_team,
# knowledge, task, task_membership) alike.
_PLAIN_TEAM_REFERENCES = (
    "projects",
    "compute_grants",
    "knowledge",
    "task",
    "team_membership_application",
    "team_recruitment_post",
)


def upgrade() -> None:
    """Merge any user's extra personal teams into their oldest, then make a second
    one impossible.

    ``ensure_personal_team`` checked and then inserted, so two first requests at
    once could leave a user with two live personal teams. The kept team is the
    lowest id; whatever pointed at an extra team is moved onto it, and the extra
    team is soft-deleted, which takes it out of the unique index below.
    """
    op.execute(
        """
        CREATE TEMP TABLE personal_team_merge AS
        SELECT t.id AS extra_id, k.keep_id
        FROM team t
        JOIN (
            SELECT personal_owner_user_id, min(id) AS keep_id
            FROM team
            WHERE personal_owner_user_id IS NOT NULL AND deleted_at IS NULL
            GROUP BY personal_owner_user_id
            HAVING count(*) > 1
        ) k USING (personal_owner_user_id)
        WHERE t.deleted_at IS NULL AND t.id <> k.keep_id
        """
    )
    for table in _PLAIN_TEAM_REFERENCES:
        op.execute(
            f"""
            UPDATE {table} r SET team_id = m.keep_id
            FROM personal_team_merge m WHERE r.team_id = m.extra_id
            """
        )
    op.execute(
        """
        UPDATE task_membership r SET member_id = m.keep_id
        FROM personal_team_merge m WHERE r.is_team AND r.member_id = m.extra_id
        """
    )

    # One row per team: a device bound to a team (unique per device), a team's
    # machine limit (keyed by team). Where the kept team, or an earlier extra,
    # already has the row, the extra team's copy is dropped instead of moved.
    op.execute(
        """
        DELETE FROM device_team d USING personal_team_merge m
        WHERE d.team_id = m.extra_id AND EXISTS (
            SELECT 1 FROM device_team o
            LEFT JOIN personal_team_merge om ON om.extra_id = o.team_id
            WHERE o.device_id = d.device_id
              AND (o.team_id = m.keep_id
                   OR (om.keep_id = m.keep_id AND o.team_id < d.team_id))
        )
        """
    )
    op.execute(
        """
        UPDATE device_team d SET team_id = m.keep_id
        FROM personal_team_merge m WHERE d.team_id = m.extra_id
        """
    )
    op.execute(
        """
        DELETE FROM team_machine_limit l USING personal_team_merge m
        WHERE l.team_id = m.extra_id AND EXISTS (
            SELECT 1 FROM team_machine_limit o
            LEFT JOIN personal_team_merge om ON om.extra_id = o.team_id
            WHERE o.team_id = m.keep_id
               OR (om.keep_id = m.keep_id AND o.team_id < l.team_id)
        )
        """
    )
    op.execute(
        """
        UPDATE team_machine_limit l SET team_id = m.keep_id
        FROM personal_team_merge m WHERE l.team_id = m.extra_id
        """
    )

    # Members: whoever is already in the kept team (the owner always is) keeps
    # that one membership; the extra team's copy is ended with the team.
    op.execute(
        """
        UPDATE team_user_relation r SET deleted_at = now(), updated_at = now()
        FROM personal_team_merge m
        WHERE r.team_id = m.extra_id AND r.deleted_at IS NULL AND EXISTS (
            SELECT 1 FROM team_user_relation o
            LEFT JOIN personal_team_merge om ON om.extra_id = o.team_id
            WHERE o.user_id = r.user_id AND o.deleted_at IS NULL
              AND (o.team_id = m.keep_id
                   OR (om.keep_id = m.keep_id AND o.team_id < r.team_id))
        )
        """
    )
    op.execute(
        """
        UPDATE team_user_relation r SET team_id = m.keep_id
        FROM personal_team_merge m
        WHERE r.team_id = m.extra_id AND r.deleted_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE team t SET deleted_at = now(), updated_at = now()
        FROM personal_team_merge m WHERE t.id = m.extra_id
        """
    )
    op.execute("DROP TABLE personal_team_merge")

    op.create_index(
        "uq_team_personal_owner",
        "team",
        ["personal_owner_user_id"],
        unique=True,
        postgresql_where="personal_owner_user_id IS NOT NULL AND deleted_at IS NULL",
    )


def downgrade() -> None:
    """Drop the index. Merged teams stay merged: which rows used to point at
    which extra team is not kept anywhere."""
    op.drop_index("uq_team_personal_owner", table_name="team")
