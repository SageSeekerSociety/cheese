"""Closing an account: what of a person goes, and what stays.

The privacy policy promises that a closed account's personal information is
deleted or anonymised. What goes is everything that says who the person is or
lets anyone act as them: their address, password, profile, real-name record,
every way of signing in (sessions, trusted browsers, passkeys, second factor,
third-party logins), their connected machines, the model keys and services
they connected, their own agents' access,
their saved integration secrets, their private assistant conversations, the
notifications addressed to them, and their places in teams, projects and rooms.

What stays is what other people still need: messages, documents and tasks
they wrote in shared rooms (shown as 「已注销用户」), records of who read
their real-name data, usage and billing records, and their consent records,
which the privacy policy says outlive the account.

The handle stays on the closed row and can never be taken again. Rooms,
rosters, reviews and routines refer to people by handle; a new account with
the same handle would inherit authority and authorship it never had.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.admin.models import PlatformAdmin
from app.domain.agent_instance.models import (
    AccountConnection,
    AgentInstance,
    OwnAgent,
)
from app.domain.assistant.models import AssistantConversation, AssistantGatewayKey
from app.domain.device.models import DeviceRow
from app.domain.integration.models import Integration
from app.domain.local_fs.models import LocalDirectoryGrantRow
from app.domain.notification.models import Notification
from app.domain.notification.push_models import PushSubscription
from app.domain.oauth.models import UserOAuthConnection
from app.domain.passkey.models import PasskeyCredential, PasskeyPrompt
from app.domain.project.models import ProjectMember
from app.domain.team.models import TeamUserRelation
from app.domain.topic.models import TopicMembership, TopicReadState
from app.domain.user.models import (
    User,
    UserBackupCode,
    UserProfile,
    UserRealNameIdentity,
    UserSession,
    UserTrustedDevice,
    UserTwoFactor,
)

#: What a closed account is called wherever a name is shown for it.
DELETED_NICKNAME = "已注销用户"


def tombstone_email(user_id: int) -> str:
    """An address nobody reads (``app.core.email.PLACEHOLDER_EMAIL_DOMAINS``),
    unique per account, so the person's real address is free to register
    again."""
    return f"deleted-{user_id}@placeholder.internal"


@dataclass(frozen=True)
class Closed:
    user_id: int
    handle: str


async def close_account(session: AsyncSession, user_id: int) -> Closed | None:
    """Close the account in the caller's transaction; None when there is no
    live account by that id. The caller commits, then calls
    ``app.core.deleted_accounts.mark_deleted`` so access tokens already issued
    stop working before they expire."""
    user = await session.scalar(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    if user is None:
        return None
    now = datetime.now(UTC)
    handle = user.username

    # Who they are.
    user.email = tombstone_email(user_id)
    user.email_domain = None
    user.hashed_password = None
    user.language = None
    user.timezone = None
    user.updated_at = now
    user.deleted_at = now
    await session.execute(
        update(UserProfile)
        .where(UserProfile.user_id == user_id, UserProfile.deleted_at.is_(None))
        .values(
            nickname=DELETED_NICKNAME,
            intro="",
            avatar_id=1,
            updated_at=now,
            deleted_at=now,
        )
    )
    await session.execute(
        update(UserRealNameIdentity)
        .where(UserRealNameIdentity.user_id == user_id)
        .values(
            real_name="",
            student_id="",
            grade="",
            major="",
            class_name="",
            updated_at=now,
            deleted_at=now,
        )
    )

    # Every way of signing in or acting as them.
    for model in (
        UserSession,
        UserTrustedDevice,
        UserTwoFactor,
        UserBackupCode,
        UserOAuthConnection,
        PasskeyCredential,
        PasskeyPrompt,
        PushSubscription,
        AssistantGatewayKey,
    ):
        await session.execute(delete(model).where(model.user_id == user_id))
    # Their machines: the device token stops working, and everything bound to
    # the machine (its login for their own Claude Code, its rooms, its health)
    # goes with the row by cascade.
    await session.execute(
        delete(LocalDirectoryGrantRow).where(
            LocalDirectoryGrantRow.owner_user_id == user_id
        )
    )
    await session.execute(delete(DeviceRow).where(DeviceRow.owner_user_id == user_id))
    # The model connections they hold: an API key, or a model service and its key.
    await session.execute(
        delete(AccountConnection).where(AccountConnection.owner_user_id == user_id)
    )
    # Their own agents stay on the rosters where their work is, switched off.
    own = select(OwnAgent.instance_id).where(OwnAgent.owner_user_id == user_id)
    await session.execute(
        update(AgentInstance).where(AgentInstance.id.in_(own)).values(is_active=False)
    )
    await session.execute(
        delete(Integration).where(Integration.owner_user_id == user_id)
    )
    await session.execute(
        delete(AssistantConversation).where(AssistantConversation.user_id == user_id)
    )
    await session.execute(
        delete(Notification).where(
            or_(
                Notification.receiver_id == user_id,
                Notification.recipient_handle == handle,
            )
        )
    )

    # Their places: what they wrote stays, their seat does not.
    await session.execute(
        update(TeamUserRelation)
        .where(
            TeamUserRelation.user_id == user_id, TeamUserRelation.deleted_at.is_(None)
        )
        .values(deleted_at=now, updated_at=now)
    )
    await session.execute(
        delete(ProjectMember).where(ProjectMember.user_handle == handle)
    )
    await session.execute(
        delete(TopicMembership).where(TopicMembership.member_handle == handle)
    )
    await session.execute(
        delete(TopicReadState).where(TopicReadState.user_handle == handle)
    )
    await session.execute(delete(PlatformAdmin).where(PlatformAdmin.handle == handle))
    await session.flush()
    return Closed(user_id=user_id, handle=handle)


async def closed_handles(session: AsyncSession, handles: list[str]) -> list[str]:
    """Which of ``handles`` belong to closed accounts."""
    wanted = sorted({h for h in handles if h})
    if not wanted:
        return []
    rows = await session.scalars(
        select(User.username).where(
            User.username.in_(wanted), User.deleted_at.is_not(None)
        )
    )
    return sorted(set(rows))
