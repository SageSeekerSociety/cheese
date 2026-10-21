"""Closing an account: what of a person goes, and what stays.

The privacy policy promises that a closed account's personal information is
deleted or anonymised. What goes is everything that says who the person is or
lets anyone act as them: their address, password, profile, real-name record,
every way of signing in (sessions, trusted browsers, passkeys, second factor,
third-party logins), their personal team's devices and model connections,
their personal team's agents' access,
their saved integration secrets, their private assistant conversations, the
notifications addressed to them, and their places in teams, projects and rooms.

An account that is the only owner of a team someone else is in, or could
still enter by a pending invitation or by joining that needs no approval, is
not closed until it hands that team on or shuts that way in: nothing else
can ever give the team an owner again, as GitHub also requires before
deleting an account. A team nobody else is in or can enter does not hold it.

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

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import ConflictError
from app.core.sentences import listing, say
from app.domain.admin.models import PlatformAdmin
from app.domain.agent_instance.models import (
    AccountConnection,
    AgentInstance,
    OwnAgent,
)
from app.domain.assistant.models import (
    AssistantChange,
    AssistantConversation,
    AssistantGatewayKey,
    AssistantProposal,
)
from app.domain.device.models import DeviceRow
from app.domain.integration.models import Integration
from app.domain.local_fs.models import LocalDirectoryGrantRow
from app.domain.notification.models import Notification
from app.domain.notification.push_models import PushSubscription
from app.domain.oauth.models import UserOAuthConnection
from app.domain.passkey.models import PasskeyCredential, PasskeyPrompt
from app.domain.project.models import Project, ProjectMember
from app.domain.team.models import (
    ApplicationType,
    Team,
    TeamMembershipApplication,
    TeamUserRelation,
)
from app.domain.team.protocol import (
    ApplicationStatus,
    TeamMemberRole,
    TeamVisibility,
)
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


async def teams_only_they_own(session: AsyncSession, user_id: int) -> list[str]:
    """Names of the teams ``user_id`` is the only owner of while someone else
    is in them or could still come in: a pending invitation, or joining with
    no approval by a public team's page or by its join link."""
    mine = aliased(TeamUserRelation)
    other = aliased(TeamUserRelation)
    someone_else = exists().where(
        other.team_id == Team.id,
        other.user_id != user_id,
        other.deleted_at.is_(None),
    )
    another_owner = someone_else.where(other.role == TeamMemberRole.OWNER)
    invited = exists().where(
        TeamMembershipApplication.team_id == Team.id,
        TeamMembershipApplication.type == ApplicationType.INVITATION.value,
        TeamMembershipApplication.status == ApplicationStatus.PENDING.value,
        TeamMembershipApplication.deleted_at.is_(None),
    )
    # With approval on, a join only files a request, and nobody is left to
    # grant it. Off, whoever can reach the team is in: a public shared team by
    # its page, any team by its link.
    open_to_join = Team.join_approval.is_(False) & (
        Team.join_token.is_not(None)
        | (
            Team.personal_owner_user_id.is_(None)
            & (Team.visibility == TeamVisibility.PUBLIC.value)
        )
    )
    rows = await session.scalars(
        select(Team.name)
        .join(mine, mine.team_id == Team.id)
        .where(
            mine.user_id == user_id,
            mine.role == TeamMemberRole.OWNER,
            mine.deleted_at.is_(None),
            Team.deleted_at.is_(None),
            someone_else | invited | open_to_join,
            ~another_owner,
        )
        .order_by(Team.name)
    )
    return list(rows)


async def close_account(session: AsyncSession, user_id: int) -> Closed | None:
    """Close the account in the caller's transaction; None when there is no
    live account by that id. Refused (409) while it is the only owner of a
    team someone else is in or could still enter (``teams_only_they_own``). The caller commits, then calls
    ``app.core.deleted_accounts.mark_deleted`` so access tokens already issued
    stop working before they expire."""
    user = await session.scalar(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    if user is None:
        return None
    owned = await teams_only_they_own(session, user_id)
    if owned:
        raise ConflictError(
            say("accountCloseOwnsTeams", teams=listing(owned, quoted=True))
        )
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
    await session.execute(
        delete(DeviceRow).where(
            DeviceRow.team_id.in_(
                select(Team.id).where(Team.personal_owner_user_id == user_id)
            )
        )
    )
    # Personal team connections go; shared team resources outlive the member.
    await session.execute(
        delete(AccountConnection).where(
            AccountConnection.team_id.in_(
                select(Team.id).where(Team.personal_owner_user_id == user_id)
            )
        )
    )
    # Retire personal-team agents; shared-team agents keep working.
    own = (
        select(OwnAgent.instance_id)
        .join(AgentInstance, AgentInstance.id == OwnAgent.instance_id)
        .join(Project, Project.id == AgentInstance.project_id)
        .join(Team, Team.id == Project.team_id)
        .where(Team.personal_owner_user_id == user_id)
    )
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
        delete(AssistantChange).where(AssistantChange.user_id == user_id)
    )
    await session.execute(
        delete(AssistantProposal).where(AssistantProposal.user_id == user_id)
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
