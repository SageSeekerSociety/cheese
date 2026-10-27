"""Tell people before a paying team's credits run out.

Three caps stop work, and each stops a different set of people: a team's plan
credits stop the whole team, a project's own budget stops that project, and a
member's task cap stops the tasks attributed to that member. Each is read here
on its own terms — a monthly plan counts this month's pack (counted as issued
before its first charge, as the ledger does), a windowed plan counts its
fullest window, and the two project caps never reset, so their figure is the
limit somebody set.

"Running low" is one threshold, ``THRESHOLD``: the same 80% the credit page
already paints its remaining figure with (it turns at 20% left) and the model
spend caps alert at. A crossing is said once and stays said until what was
crossed changes — the month, the window round, or the limit itself. Those are
part of the event id, so a sweep that runs again finds nothing new to say, and
a new month or a raised limit is news again. The delivery ledger dedupes on
that id, so even two sweeps racing each other tell a person once.

The sentence always says what the reader can do: when it comes back, or that
it does not come back on its own and an administrator has to raise it. It
stops just below 100% — there the ledger's own refusal already speaks, and the
same fact is not said twice.

This module only reads. Who a team's and a project's people are, and how a
notification is delivered, are handed in by ``app.periodic_jobs``, because
those live above this package.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import NoticeText, notice_keys, notice_message, say
from app.domain.team.models import Team
from app.domain.usage.budgets import CreditBudgets
from app.domain.usage.ledger import Ledger
from app.domain.usage.models import (
    ComputeGrant,
    GrantSource,
    MemberCreditBudget,
    ProjectCreditBudget,
    ResourceUsage,
)
from app.domain.usage.payers import Payer, Terms, terms_of
from app.domain.usage.windows import _TZ, WindowUse, month_end, month_of
from app.domain.user.services import usernames_by_ids

#: How full is "nearly full". One value for all three caps, and the same one the
#: credit page's remaining figure turns warning-coloured at (20% left) and the
#: model spend caps alert at.
THRESHOLD = 0.8

#: Where a warning's identity is minted. A crossing is one event: the id is
#: derived from what was crossed, so recomputing it never says it twice.
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "cheese.credit-warning")


@dataclass(frozen=True)
class Warning:
    """One cap nearly spent, and who is stopped when it is spent."""

    #: ``team``, ``project`` or ``member`` — the layer that is running out.
    layer: str
    #: What was crossed, stably: a month, a window round, a pair of limits.
    #: It is part of the event id; a new month or a raised limit is a new
    #: crossing worth saying.
    marker: str
    used: float
    total: float
    resets_at: datetime | None
    sentence: NoticeText

    @property
    def remaining(self) -> float:
        return max(0.0, self.total - self.used)


#: What the sweep is handed by its wiring: who a team's and a project's people
#: are, and how to deliver one event to one handle.
TeamRoster = Callable[[AsyncSession, int], Awaitable[Sequence[str]]]
ProjectRoster = Callable[[AsyncSession, uuid.UUID], Awaitable[Sequence[str]]]
Notify = Callable[[AsyncSession, uuid.UUID, dict, Sequence[str]], Awaitable[None]]


def _ratio(used: float, total: float) -> float:
    return 0.0 if total <= 0 else used / total


def _crossed(used: float, total: float) -> bool:
    """Nearly spent, but not spent: at 100% the ledger's refusal speaks."""
    return total > 0 and THRESHOLD <= _ratio(used, total) < 1.0


def _event_id(layer: str, scope: str, marker: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{layer}:{scope}:{marker}")


def _points(value: float) -> float:
    """Credits as the page shows them: whole 点 above ten, one decimal below."""
    return int(value) if abs(value) >= 10 else round(value, 1)


def _when(at: datetime | None) -> dict:
    if at is None:
        return {}
    local = at.astimezone(_TZ)
    return {"month": local.month, "day": local.day, "time": local.strftime("%H:%M")}


def _how_full(use: WindowUse) -> float:
    """How much of the window is used, as a share — a window with more credits
    can have spent more without being closer to stopping the call."""
    return use.credits_used / use.window.credits if use.window.credits > 0 else 0.0


def _window_sentence(calendar: str | None) -> str:
    """A window is said by its name — 本周, 本月, 本小时 — because 「这个窗口」 tells
    the reader nothing about when it comes back."""
    if calendar == "week":
        return "creditWarningTeamWeek"
    if calendar == "month":
        return "creditWarningTeamMonth"
    return "creditWarningTeamHours"


def _team_warning(
    terms: Terms,
    pack: ComputeGrant | None,
    month: date,
    uses: Sequence[WindowUse],
) -> Warning | None:
    """A team's plan as one warning, or None while it has room.

    A monthly plan is read from this month's pack; a windowed plan from its
    fullest window — the one that stops the call first."""
    if terms.unlimited:
        return None
    if terms.windowed:
        fullest = max(uses, key=_how_full, default=None)
        if fullest is None or fullest.resets_at is None:
            return None
        used, total = fullest.credits_used, fullest.window.credits
        if not _crossed(used, total):
            return None
        return Warning(
            layer="team",
            # The limit is part of what was crossed: raising a window's credits
            # is a new crossing, and the same round of the same window must not
            # stay silent because it was crossed once at the old limit.
            marker=f"{fullest.window.key}:{total:g}:{fullest.resets_at.isoformat()}",
            used=used,
            total=total,
            resets_at=fullest.resets_at,
            sentence=say(
                _window_sentence(fullest.window.calendar),
                remaining=_points(total - used),
                hours=(
                    f"{fullest.window.hours:g}"
                    if fullest.window.hours is not None
                    else ""
                ),
                **_when(fullest.resets_at),
            ),
        )
    total = pack.credits_total if pack is not None else terms.issues
    used = pack.credits_used if pack is not None else 0.0
    if not _crossed(used, total):
        return None
    reset = month_end(month)
    return Warning(
        layer="team",
        # The limit belongs in the marker the same way it does for a window:
        # a plan that issues more this month is a new crossing.
        marker=f"{month.isoformat()}:{total:g}",
        used=used,
        total=total,
        resets_at=reset,
        sentence=say(
            "creditWarningTeam", remaining=_points(total - used), **_when(reset)
        ),
    )


@dataclass(frozen=True)
class _Scoped:
    warning: Warning
    #: Where a delivery key puts it apart from another scope's same sentence.
    scope: str
    #: Who hears it.
    handles_for: str | None = None
    #: The team's public name, for the link a warning leads to.
    team_handle: str | None = None


async def _team_warnings(session: AsyncSession) -> list[_Scoped]:
    teams = (
        await session.execute(
            select(
                Team.id, Team.plan_key, Team.handle, Team.personal_owner_user_id
            ).where(Team.deleted_at.is_(None))
        )
    ).all()
    if not teams:
        return []
    # A personal team's address is its owner's username, not a handle it does
    # not have (`TeamService.labels_of` says the same for links).
    owners = await usernames_by_ids(
        session,
        [owner for *_, owner in teams if owner is not None],
    )
    terms = await terms_of(session, [key for _, key, _, _ in teams])
    month = month_of(datetime.now(UTC))
    ledger = Ledger(session)
    packs = {
        team_id: pack
        for team_id, held in (
            await ledger.live_packs([team_id for team_id, _, _, _ in teams])
        ).items()
        for pack in held
        if pack.source == GrantSource.PLAN_PERIOD and pack.period_start == month
    }
    payers = [Payer(team_id=team_id, terms=terms[key]) for team_id, key, _, _ in teams]
    uses = await ledger.window_uses(payers)
    out: list[_Scoped] = []
    for team_id, key, handle, owner in teams:
        warning = _team_warning(
            terms[key], packs.get(team_id), month, uses.get(team_id, ())
        )
        if warning is not None:
            out.append(
                _Scoped(
                    warning,
                    f"team:{team_id}",
                    team_handle=handle or owners.get(owner or 0),
                )
            )
    return out


async def _project_warnings(session: AsyncSession) -> list[_Scoped]:
    rows = (
        await session.execute(
            select(
                ProjectCreditBudget.project_id,
                ProjectCreditBudget.credits_limit,
                func.coalesce(func.sum(ResourceUsage.credits), 0.0),
            )
            .outerjoin(
                ResourceUsage,
                and_(
                    ResourceUsage.project_id == ProjectCreditBudget.project_id,
                    ResourceUsage.created_at >= ProjectCreditBudget.since,
                ),
            )
            .where(ProjectCreditBudget.credits_limit.is_not(None))
            .group_by(ProjectCreditBudget.project_id, ProjectCreditBudget.credits_limit)
        )
    ).all()
    out: list[_Scoped] = []
    for project_id, limit, spent in rows:
        if not _crossed(float(spent), float(limit)):
            continue
        out.append(
            _Scoped(
                Warning(
                    layer="project",
                    marker=f"{float(limit):g}",
                    used=float(spent),
                    total=float(limit),
                    resets_at=None,
                    sentence=say(
                        "creditWarningProject",
                        remaining=_points(float(limit) - float(spent)),
                    ),
                ),
                f"project:{project_id}",
            )
        )
    return out


async def _member_warnings(session: AsyncSession) -> list[_Scoped]:
    """A member cap is read against the same window its own check uses: the
    project's budget, from the day the project began."""
    rows = (
        await session.execute(
            select(
                MemberCreditBudget.project_id,
                MemberCreditBudget.owner_handle,
                MemberCreditBudget.credits_limit,
                func.coalesce(func.sum(ResourceUsage.credits), 0.0),
            )
            .join(
                ProjectCreditBudget,
                ProjectCreditBudget.project_id == MemberCreditBudget.project_id,
            )
            .outerjoin(
                ResourceUsage,
                and_(
                    ResourceUsage.project_id == MemberCreditBudget.project_id,
                    ResourceUsage.credit_owner_handle
                    == MemberCreditBudget.owner_handle,
                    ResourceUsage.created_at >= ProjectCreditBudget.since,
                ),
            )
            .where(MemberCreditBudget.credits_limit.is_not(None))
            .group_by(
                MemberCreditBudget.project_id,
                MemberCreditBudget.owner_handle,
                MemberCreditBudget.credits_limit,
            )
        )
    ).all()
    out: list[_Scoped] = []
    for project_id, handle, limit, spent in rows:
        if not _crossed(float(spent), float(limit)):
            continue
        out.append(
            _Scoped(
                Warning(
                    layer="member",
                    marker=f"{handle}:{float(limit):g}",
                    used=float(spent),
                    total=float(limit),
                    resets_at=None,
                    sentence=say(
                        "creditWarningMember",
                        remaining=_points(float(limit) - float(spent)),
                    ),
                ),
                f"member:{project_id}:{handle}",
                handle,
            )
        )
    return out


def payload(
    warning: Warning,
    *,
    team_handle: str | None = None,
    project_id: uuid.UUID | None = None,
) -> dict:
    """What a screen and a mail need: the stored sentence, its key, the figures
    behind it, and where the reader goes to do something about it — a team's
    address for a team-wide one, the project for the other two. The two
    addresses are camelCase keys because every screen reads `contextMetadata`,
    where the rest of the platform's keys are named that way."""
    content = warning.sentence
    return {
        "content": str(content),
        **notice_message(notice_keys(content=content)),
        "event": "credit_warning",
        "layer": warning.layer,
        "used": warning.used,
        "total": warning.total,
        "remaining": warning.remaining,
        "resets_at": warning.resets_at.isoformat() if warning.resets_at else None,
        **({"teamHandle": team_handle} if team_handle else {}),
        **({"projectId": str(project_id)} if project_id else {}),
    }


async def collect(session: AsyncSession) -> list[Warning]:
    """Every cap nearly spent right now, without sending anything."""
    scoped: list[_Scoped] = []
    scoped += await _team_warnings(session)
    scoped += await _project_warnings(session)
    scoped += await _member_warnings(session)
    return [s.warning for s in scoped]


async def agent_note(session: AsyncSession, project_id: uuid.UUID) -> str | None:
    """What 芝士 is told on the way into a turn, or None while there is room.

    The figures are read with the same functions the inbox uses, so a crossing
    says the same sentence in both places. Only the fact and its consequence —
    the platform does not explain the project's settings to it.

    Both layers this project pays from are read — the team's plan and the
    project's own budget — and the fuller of the two is said, because that is
    the one that stops the call first. Each keeps its own sentence: the project
    budget does not come back on its own. A member's task cap is not read: it
    stops that person's tasks, not the calls this turn starts.
    """
    from app.domain.usage.ledger import payer_for_project

    payer = await payer_for_project(session, project_id)
    said: list[tuple[float, str]] = []
    if not payer.terms.unlimited:
        month = month_of(datetime.now(UTC))
        ledger = Ledger(session)
        packs = await ledger.team_packs(payer.team_id)
        pack = next(
            (
                p
                for p in packs
                if p.source == GrantSource.PLAN_PERIOD and p.period_start == month
            ),
            None,
        )
        uses = await ledger.window_uses([payer])
        team = _team_warning(payer.terms, pack, month, uses.get(payer.team_id, ()))
        if team is not None:
            said.append(
                (
                    _ratio(team.used, team.total),
                    str(say("creditWarningAgent", remaining=_points(team.remaining))),
                )
            )
    caps = await CreditBudgets(session).project_caps([project_id])
    cap, spent = caps.get(project_id, (None, 0.0))
    if cap is not None and _crossed(spent, cap):
        said.append(
            (
                _ratio(spent, cap),
                str(say("creditWarningAgentBudget", remaining=_points(cap - spent))),
            )
        )
    return max(said, key=lambda pair: pair[0])[1] if said else None


async def sweep(
    sessions,
    *,
    team_people: TeamRoster,
    project_people: ProjectRoster,
    notify: Notify,
) -> int:
    """One pass: say every crossing that has not been said yet. Returns how
    many were said."""
    said = 0
    async with sessions() as session:
        team_warnings = await _team_warnings(session)
        project_warnings = await _project_warnings(session)
        member_warnings = await _member_warnings(session)
        for scoped in team_warnings:
            handles = await team_people(session, int(scoped.scope.split(":")[1]))
            if not handles:
                continue
            await notify(
                session,
                _event_id(scoped.warning.layer, scoped.scope, scoped.warning.marker),
                payload(scoped.warning, team_handle=scoped.team_handle),
                handles,
            )
            said += 1
        for scoped in project_warnings:
            project_id = uuid.UUID(scoped.scope.split(":")[1])
            handles = await project_people(session, project_id)
            if not handles:
                continue
            await notify(
                session,
                _event_id(scoped.warning.layer, scoped.scope, scoped.warning.marker),
                payload(scoped.warning, project_id=project_id),
                handles,
            )
            said += 1
        for scoped in member_warnings:
            await notify(
                session,
                _event_id(scoped.warning.layer, scoped.scope, scoped.warning.marker),
                payload(
                    scoped.warning,
                    project_id=uuid.UUID(scoped.scope.split(":")[1]),
                ),
                (scoped.handles_for,) if scoped.handles_for else (),
            )
            said += 1
        await session.commit()
    return said
