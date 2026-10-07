"""Invitations sent before the fix reach the invitee once the migration has run.

Their notice was filed in the invited project's inbox, which the invitee cannot
open. The migration moves the notice of every invitation still pending into
the invitee's own mail, where it can be seen and answered; an invitation that
was already answered keeps the notice it had.
"""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

from app.domain.notification.models import Notification, NotificationType
from app.domain.project.models import InvitationStatus, ProjectInvitation
from tests.conftest import seed_user
from tests.integration.test_team_member_enters_team_project import _bearer
from tests.integration.test_who_may_read_a_project import _project

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic/versions/b6e2d94a1c37_project_invitations_reach_the_bell.py"
)

OWNER = "alice"
INVITEE = "invitee-1"


def _migration():
    spec = importlib.util.spec_from_file_location("invitations_to_bell", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_old_invitation(client, pid: str, status: InvitationStatus) -> str:
    """An invitation as the old code left it: the notice in the project inbox."""

    async def seed() -> str:
        async with client.test_factory() as session:
            invitation = ProjectInvitation(
                project_id=uuid.UUID(pid),
                invitee_handle=INVITEE,
                inviter_handle=OWNER,
                status=status,
            )
            session.add(invitation)
            await session.flush()
            now = datetime.now(UTC)
            session.add(
                Notification(
                    receiver_id=None,
                    recipient_handle=INVITEE,
                    type=NotificationType.DECISION_REQUEST,
                    project_id=uuid.UUID(pid),
                    title="invited",
                    body="",
                    metadata_payload={
                        "invitation_id": str(invitation.id),
                        "project_name": "P",
                        "options": ["yes", "no"],
                    },
                    read=False,
                    delivery_key=f"legacy:{invitation.id}",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
            return str(invitation.id)

    return asyncio.run(seed())


def _migrate(client) -> None:
    """把那次迁移的 `convert()` 重放一遍，跟 `alembic upgrade head` 同一条路。

    库现在是 head 的形状，而 `convert()` 清的是它当时的列名 `topic_id` —— 那一列
    后来换成 `conversation_id`（9f2b7c14a8e3，一条通知关于的那条对话）。重放前把
    那一列按库当时的样子补上，跑完再撤掉：`convert()` 只把它置空，没有数据要搬，
    之后这一行照样是「不属于哪条对话」，和它想写的结果一样。
    """
    convert = _migration().convert

    def _apply(conn) -> None:
        conn.execute(
            sa.text("ALTER TABLE notification ADD COLUMN IF NOT EXISTS topic_id uuid")
        )
        convert(conn)
        conn.execute(sa.text("ALTER TABLE notification DROP COLUMN topic_id"))

    async def run() -> None:
        async with client.test_factory() as session:
            await session.run_sync(_apply)
            await session.commit()

    asyncio.run(run())


def _bell(client) -> list[dict]:
    r = client.get(
        "/notifications",
        params={"type": "PROJECT_INVITE"},
        headers=_bearer(seed_user(client, INVITEE)),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["notifications"]


def test_a_pending_invitation_reaches_the_bell_and_can_be_answered(client):
    seed_user(client, INVITEE)
    pid, _ = _project(client, OWNER)
    invitation = _seed_old_invitation(client, pid, InvitationStatus.pending)
    assert _bell(client) == []

    _migrate(client)
    _migrate(client)  # a second run changes nothing

    [notice] = _bell(client)
    assert notice["read"] is False
    assert notice["contextMetadata"]["invitationId"] == invitation
    assert notice["entities"]["project"]["id"] == pid

    answered = client.post(
        f"/invitations/{invitation}/respond",
        json={"accept": True},
        headers=_bearer(seed_user(client, INVITEE)),
    )
    assert answered.status_code == 200, answered.text
    [notice] = _bell(client)
    assert notice["read"] is True
    assert notice["contextMetadata"]["status"] == "accepted"


def test_an_answered_invitation_is_left_where_it_was(client):
    seed_user(client, INVITEE)
    pid, _ = _project(client, OWNER)
    _seed_old_invitation(client, pid, InvitationStatus.declined)

    _migrate(client)

    assert _bell(client) == []
