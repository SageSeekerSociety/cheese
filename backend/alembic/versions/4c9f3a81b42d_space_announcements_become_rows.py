"""space announcements become rows

Revision ID: 4c9f3a81b42d
Revises: 30ee9b5002a9

A 题目版's announcements were one JSONB list on ``space``, written whole by the
space PATCH, so two managers editing at once overwrote each other and nothing
had an id to notify about. They become one ``space_announcement`` row each.

Every object element of the old list is copied: title, content, pinned, and its
``createdAt``/``updatedAt`` (epoch milliseconds; the space's own creation time
when missing). The old element named its publisher by nickname only, so the
author is the manager of that space whose nickname (or username) matches; an
element no manager matches keeps no author rather than being credited to
someone who did not write it. Elements that are not objects (a bare string)
carry no title or body and are dropped.

``deliveries.event_id`` gains an index: deleting an announcement now deletes
the notifications it sent, found by that event.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4c9f3a81b42d"
down_revision: str | Sequence[str] | None = "30ee9b5002a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "space_announcement",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_space_announcement_space", "space_announcement", ["space_id"])

    op.execute(
        """
        WITH element AS (
            SELECT s.id AS space_id,
                   s.created_at AS space_created_at,
                   e.value AS item,
                   e.ordinality AS position
            FROM space s
            CROSS JOIN LATERAL jsonb_array_elements(s.announcements)
                WITH ORDINALITY AS e(value, ordinality)
            WHERE jsonb_typeof(s.announcements) = 'array'
              AND jsonb_typeof(e.value) = 'object'
        ),
        timed AS (
            SELECT space_id,
                   item,
                   position,
                   CASE WHEN jsonb_typeof(item -> 'createdAt') = 'number'
                        THEN to_timestamp((item ->> 'createdAt')::numeric / 1000)
                        ELSE space_created_at END AS created_at,
                   CASE WHEN jsonb_typeof(item -> 'updatedAt') = 'number'
                        THEN to_timestamp((item ->> 'updatedAt')::numeric / 1000)
                   END AS updated_at
            FROM element
        )
        INSERT INTO space_announcement
            (space_id, author_id, title, content, pinned, expires_at,
             created_at, updated_at)
        SELECT t.space_id,
               (
                   SELECT sar.user_id
                   FROM space_admin_relation sar
                   JOIN "user" u ON u.id = sar.user_id
                   LEFT JOIN user_profile up
                     ON up.user_id = sar.user_id AND up.deleted_at IS NULL
                   WHERE sar.space_id = t.space_id
                     AND sar.deleted_at IS NULL
                     AND coalesce(t.item ->> 'publisher', '') <> ''
                     AND (up.nickname = t.item ->> 'publisher'
                          OR u.username = t.item ->> 'publisher')
                   ORDER BY sar.role, sar.id
                   LIMIT 1
               ),
               left(coalesce(t.item ->> 'title', ''), 255),
               coalesce(t.item ->> 'content', ''),
               coalesce(t.item ->> 'pinned', '') = 'true',
               NULL,
               t.created_at,
               greatest(t.created_at, coalesce(t.updated_at, t.created_at))
        FROM timed t
        ORDER BY t.space_id, t.position
        """
    )

    op.drop_column("space", "announcements")

    # Taking an announcement down retracts what it sent (`ledger.retract`),
    # found by the event it was sent as.
    op.create_index("idx_deliveries_event", "deliveries", ["event_id"])


def downgrade() -> None:
    op.drop_index("idx_deliveries_event", table_name="deliveries")
    op.add_column(
        "space",
        sa.Column(
            "announcements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        """
        UPDATE space s
        SET announcements = sub.items
        FROM (
            SELECT a.space_id,
                   jsonb_agg(
                       jsonb_build_object(
                           'title', a.title,
                           'content', a.content,
                           'pinned', a.pinned,
                           'publisher', coalesce(up.nickname, u.username, ''),
                           'createdAt',
                           (extract(epoch FROM a.created_at) * 1000)::bigint,
                           'updatedAt',
                           (extract(epoch FROM a.updated_at) * 1000)::bigint
                       )
                       ORDER BY a.created_at, a.id
                   ) AS items
            FROM space_announcement a
            LEFT JOIN "user" u ON u.id = a.author_id
            LEFT JOIN user_profile up
              ON up.user_id = a.author_id AND up.deleted_at IS NULL
            GROUP BY a.space_id
        ) sub
        WHERE s.id = sub.space_id
        """
    )
    op.drop_index("ix_space_announcement_space", table_name="space_announcement")
    op.drop_table("space_announcement")
