"""Inviting friends during the beta: each person's own code and what it earns.

The rules are the team's 《内测激励与裂变机制设计方案》:

* Everyone has one code of their own. It admits one person to begin with, and
  three more once its owner has accepted a delivery themselves. Every friend
  who gets started (below) gives the seat they took back, so a code runs out
  only on friends who never accept anything.
* A friend who registers with it is that person's invitee for good.
* The invitee is paid 50 credits when they finish the new-user guide, and 100
  more when they accept their first delivery within seven days of registering.
  That acceptance pays the inviter 150 credits and 50 points.
* An inviter is paid at most 1,500 credits a month through invitations; past
  that the points are still recorded.

Every pack lasts as long as any reward pack (``REWARD_LIFETIME``) and is
spent after the month's plan credits. Each step
is a ``CreditReward`` keyed by the invitee, so it pays once whatever retries.
"""

import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.invite.models import InviteCode, InviteReferral
from app.domain.usage.incentives import CreditIncentives
from app.domain.usage.models import CreditReward
from app.domain.usage.windows import month_end, month_of
from app.domain.user.models import User
from app.domain.user.services import faces_by_handle, user_by_handle

FIRST_USES = 1
UNLOCKED_USES = 3
SEATS_BACK_PER_START = 1
JOIN_CREDITS = 50
ACTIVATED_CREDITS = 100
INVITER_CREDITS = 150
INVITER_POINTS = 50
INVITER_MONTHLY_CAP = 1500
ACTIVATION_WINDOW = timedelta(days=7)


async def personal_code(
    session: AsyncSession, user: User, *, has_accepted: Callable[[], Awaitable[bool]]
) -> InviteCode:
    """The person's own code, made the first time it is asked for. Someone who
    has already accepted a delivery (``has_accepted``, which the caller reads
    from review, and which is asked only when the code is made) gets it with
    the extra uses unlocked. Two first views at once make one code: the
    public shared page can be the first view, opened by many readers."""
    mine = select(InviteCode).where(InviteCode.owner_user_id == user.id)
    code = await session.scalar(mine)
    if code is not None:
        return code
    unlocked = await has_accepted()
    await session.execute(
        insert(InviteCode)
        .values(
            code=secrets.token_urlsafe(6),
            owner_user_id=user.id,
            created_by=user.id,
            max_uses=FIRST_USES + (UNLOCKED_USES if unlocked else 0),
            use_count=0,
            is_active=True,
            unlocked_at=datetime.now(UTC) if unlocked else None,
            note="personal",
        )
        .on_conflict_do_nothing(index_elements=["owner_user_id"])
    )
    found = await session.scalar(mine)
    assert found is not None  # inserted here, or by whoever won the race
    return found


async def record_referral(
    session: AsyncSession, code: InviteCode, invitee_id: int
) -> None:
    """Remember who brought the person who just registered with ``code``. An
    administrator's code belongs to nobody and invites nobody's friend."""
    if code.owner_user_id is None or code.owner_user_id == invitee_id:
        return
    session.add(
        InviteReferral(
            invitee_user_id=invitee_id,
            inviter_user_id=code.owner_user_id,
            code_id=code.id,
        )
    )
    await session.flush()


async def on_onboarded(session: AsyncSession, user_id: int) -> None:
    """An invitee's first project: their 50 credits."""
    now = datetime.now(UTC)
    marked = await session.scalar(
        update(InviteReferral)
        .where(
            InviteReferral.invitee_user_id == user_id,
            InviteReferral.onboarded_at.is_(None),
        )
        .values(onboarded_at=now)
        .returning(InviteReferral.invitee_user_id)
    )
    if marked is None:
        return
    await CreditIncentives(session).issue(
        key=f"invite:joined:{user_id}",
        user_id=user_id,
        credits=JOIN_CREDITS,
        reason="Invitation: first project",
        actor="system",
    )


async def on_accepted(session: AsyncSession, handle: str | None) -> None:
    """``handle`` just accepted a delivery. Their own code gains its extra uses
    the first time; an invitee doing so inside the window pays both sides."""
    user = await user_by_handle(session, handle) if handle else None
    if user is None:
        return
    now = datetime.now(UTC)
    await session.execute(
        update(InviteCode)
        .where(InviteCode.owner_user_id == user.id, InviteCode.unlocked_at.is_(None))
        .values(max_uses=InviteCode.max_uses + UNLOCKED_USES, unlocked_at=now)
    )
    inviter_id = await session.scalar(
        update(InviteReferral)
        .where(
            InviteReferral.invitee_user_id == user.id,
            InviteReferral.activated_at.is_(None),
            InviteReferral.registered_at >= now - ACTIVATION_WINDOW,
        )
        .values(activated_at=now)
        .returning(InviteReferral.inviter_user_id)
    )
    if inviter_id is None:
        return
    incentives = CreditIncentives(session)
    await incentives.issue(
        key=f"invite:activated:{user.id}",
        user_id=user.id,
        credits=ACTIVATED_CREDITS,
        reason="Invitation: first accepted delivery",
        actor="system",
    )
    # Two friends getting started at once must not both fit under the cap:
    # the inviter's own code row is the lock their payouts queue on.
    await session.execute(
        select(InviteCode.id)
        .where(InviteCode.owner_user_id == inviter_id)
        .with_for_update()
    )
    # The friend got started, so their seat comes back. Past the monthly cap
    # too: the cap limits credits, not how many friends can be brought in.
    # Once per friend, like the payouts: only the first activation gets here.
    await session.execute(
        update(InviteCode)
        .where(InviteCode.owner_user_id == inviter_id)
        .values(max_uses=InviteCode.max_uses + SEATS_BACK_PER_START)
    )
    paid = await _paid_this_month(session, inviter_id, now)
    await incentives.issue(
        key=f"invite:referrer:{user.id}",
        user_id=inviter_id,
        credits=max(0, min(INVITER_CREDITS, INVITER_MONTHLY_CAP - paid)),
        points=INVITER_POINTS,
        reason=f"Invitation: @{user.username} got started",
        actor="system",
    )


async def _paid_this_month(
    session: AsyncSession, inviter_id: int, now: datetime
) -> int:
    month = month_of(now)
    start = datetime(month.year, month.month, 1, tzinfo=month_end(month).tzinfo)
    invitees = select(
        func.concat("invite:referrer:", InviteReferral.invitee_user_id)
    ).where(InviteReferral.inviter_user_id == inviter_id)
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(CreditReward.credits), 0)).where(
                CreditReward.key.in_(invitees), CreditReward.created_at >= start
            )
        )
        or 0
    )


def _stage(row: InviteReferral, now: datetime) -> str:
    if row.activated_at is not None:
        return "activated"
    if row.registered_at < now - ACTIVATION_WINDOW:
        return "lapsed"
    return "onboarded" if row.onboarded_at is not None else "registered"


async def overview(
    session: AsyncSession, user: User, *, has_accepted: Callable[[], Awaitable[bool]]
) -> dict:
    """What the person's 邀请好友 page shows."""
    code = await personal_code(session, user, has_accepted=has_accepted)
    rows = list(
        await session.scalars(
            select(InviteReferral)
            .where(InviteReferral.inviter_user_id == user.id)
            .order_by(InviteReferral.registered_at.desc())
        )
    )
    invitees = {
        u.id: u
        for u in await session.scalars(
            select(User).where(User.id.in_([r.invitee_user_id for r in rows]))
        )
    }
    faces = await faces_by_handle(session, [u.username for u in invitees.values()])
    paid: dict[str, int] = {
        key: credits
        for key, credits in await session.execute(
            select(CreditReward.key, CreditReward.credits).where(
                CreditReward.key.in_(
                    [f"invite:referrer:{r.invitee_user_id}" for r in rows]
                )
            )
        )
    }
    rewards = await CreditIncentives(session).rewards(user.id)
    now = datetime.now(UTC)
    paid_this_month = await _paid_this_month(session, user.id, now)
    people = []
    for row in rows:
        invitee = invitees.get(row.invitee_user_id)
        if invitee is None:
            continue
        nickname, avatar_id = faces.get(invitee.username, (None, None))
        people.append(
            {
                "handle": invitee.username,
                "nickname": nickname,
                "avatar_id": avatar_id,
                "registered_at": row.registered_at,
                "stage": _stage(row, now),
                "credits": paid.get(f"invite:referrer:{row.invitee_user_id}", 0),
            }
        )
    return {
        "code": code.code,
        "max_uses": code.max_uses,
        "use_count": code.use_count,
        "unlocked": code.unlocked_at is not None,
        "unlock_uses": UNLOCKED_USES,
        "invite_credits": rewards["invite_credits"],
        "month_credits": paid_this_month,
        "month_cap": INVITER_MONTHLY_CAP,
        "invitees": people,
    }


async def guest_pass(
    session: AsyncSession, user: User, *, has_accepted: Callable[[], Awaitable[bool]]
) -> dict:
    """The sharer's code as a shared page's colophon offers it, with the seats
    left."""
    handle = user.username
    code = await personal_code(session, user, has_accepted=has_accepted)
    nickname, _ = (await faces_by_handle(session, [handle])).get(handle, (None, None))
    left = max(0, code.max_uses - code.use_count) if code.is_active else 0
    return {
        "handle": handle,
        "nickname": nickname,
        "code": code.code if left else None,
        "seats_left": left,
    }
