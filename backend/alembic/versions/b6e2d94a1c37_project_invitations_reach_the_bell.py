"""Move each pending project invitation's notice into the invitee's own mail

Revision ID: b6e2d94a1c37
Revises: f7985445d2bf
Create Date: 2026-10-04

An invitation used to be filed as a ``decision_request`` in the invited
project's inbox. That inbox is read only by people on the project's roster,
and the invitee is not on it until they accept, so nobody could ever see it.
Invitations now go to the invitee's site mail as ``PROJECT_INVITE``
(``InvitationService._notify_invitee``), the same shape the delivery ledger
writes.

This converts the notice of every invitation still pending into that shape, in
place: the row keeps its id, its time and its read state, and gets the ledger
row that settling the invitation later looks it up by. The ledger row is
recorded as already sent, so nothing is emailed for an old invitation.

Only notices tied to a pending invitation are touched; answered ones were
already resolved where they were. A notice whose invitee has no account is left
alone: there is no mail to move it to. Running it again finds nothing left to
convert, because a converted row is no longer a ``decision_request``.
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b6e2d94a1c37"
down_revision: str | Sequence[str] | None = "f7985445d2bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ``delivery.ledger._EVENT_NAMESPACE`` and ``event_id_for``, copied rather than
# imported so this migration keeps meaning what it meant when it was written.
_EVENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "notification.cheese")


def _event_id(invitation_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(_EVENT_NAMESPACE, f"PROJECT_INVITE:{invitation_id}")


PENDING_NOTICES = sa.text(
    """
    SELECT n.id AS notification_id,
           i.id AS invitation_id,
           i.project_id,
           i.invitee_handle,
           i.created_at,
           p.name AS project_name,
           invitee.id AS receiver_id,
           inviter.id AS inviter_id
      FROM project_invitations i
      JOIN notification n
        ON n.type = 'decision_request'
       AND n.project_id = i.project_id
       AND n.recipient_handle = i.invitee_handle
       AND n.metadata ->> 'invitation_id' = i.id::text
       AND n.deleted_at IS NULL
      JOIN projects p ON p.id = i.project_id
      JOIN "user" invitee
        ON invitee.username = i.invitee_handle AND invitee.deleted_at IS NULL
      LEFT JOIN "user" inviter
        ON inviter.username = i.inviter_handle AND inviter.deleted_at IS NULL
     WHERE i.status = 'pending'
    """
)


def upgrade() -> None:
    convert(op.get_bind())


def convert(bind: sa.Connection) -> None:
    for row in bind.execute(PENDING_NOTICES).mappings().all():
        event_id = _event_id(row["invitation_id"])
        key = f"{event_id}:{row['invitee_handle']}"
        payload: dict = {
            "project": {"type": "project", "id": str(row["project_id"])},
            "projectName": row["project_name"],
            "invitationId": str(row["invitation_id"]),
        }
        if row["inviter_id"] is not None:
            payload["inviter"] = {"type": "user", "id": str(row["inviter_id"])}
        bind.execute(
            sa.text(
                """
                INSERT INTO deliveries
                    (id, event_id, recipient_handle, receiver_id, external_channels,
                     state, dedup_key, type, payload, event_at, recorded_at,
                     sent_at, attempts)
                VALUES
                    (:id, :event_id, :handle, :receiver_id, false,
                     'received', :key, 'PROJECT_INVITE', CAST(:payload AS jsonb),
                     :event_at, now(), now(), 0)
                ON CONFLICT (dedup_key) DO NOTHING
                """
            ),
            {
                "id": uuid.uuid4(),
                "event_id": event_id,
                "handle": row["invitee_handle"],
                "receiver_id": row["receiver_id"],
                "key": key,
                "payload": json.dumps(payload),
                "event_at": row["created_at"],
            },
        )
        bind.execute(
            sa.text(
                """
                UPDATE notification
                   SET type = 'PROJECT_INVITE',
                       receiver_id = :receiver_id,
                       recipient_handle = NULL,
                       project_id = NULL,
                       topic_id = NULL,
                       level = NULL,
                       title = NULL,
                       body = NULL,
                       resolved_at = NULL,
                       metadata = CAST(:payload AS jsonb),
                       delivery_key = :key
                 WHERE id = :id
                """
            ),
            {
                "id": row["notification_id"],
                "receiver_id": row["receiver_id"],
                "payload": json.dumps(payload),
                "key": key,
            },
        )


def downgrade() -> None:
    """Downgrade puts nothing back: the old row was unreadable by the one person it
    was for."""
    pass
