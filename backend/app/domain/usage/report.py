"""What a team's or a person's credits went to this month, as members see it
(#2397, #2233).

Everything here is a ratio: how much of this month's plan pack is used, or how
full each of a windowed plan's time windows is, and what share of the month's
spend each day, project or product line took. No credit amounts and no tokens
leave this module, and nothing is split by person: spend inside a team's
projects belongs to the team (#394). A person's own page reads only their
personal team's spend.
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.usage.ledger import (
    _TZ,
    Ledger,
    Payer,
    Terms,
    WindowUse,
    month_end,
    month_of,
    terms_of,
)
from app.domain.usage.models import ComputeGrant, GrantSource, Plan
from app.domain.usage.repositories import UsageRepository

#: The product line a call outside any project belongs to, by usage kind. A
#: call inside a project is 协作 whatever its kind.
LINE_COLLAB = "collab"
LINE_ASK = "ask"
LINE_WRITE = "write"
LINES = (LINE_COLLAB, LINE_ASK, LINE_WRITE)
KIND_LINES: dict[str, str] = {
    "assistant": LINE_ASK,
    "docs_ask": LINE_ASK,
    "task_pdf_draft": LINE_WRITE,
}


def line_of(kind: str, project_id: uuid.UUID | None) -> str | None:
    """The product line a usage row counts towards; None when it is none of
    them."""
    if project_id is not None:
        return LINE_COLLAB
    return KIND_LINES.get(kind.split(":", 1)[0])


def _ratio(part: float, whole: float) -> float:
    return 0.0 if whole <= 0 else min(1.0, max(0.0, part / whole))


def _used(use: WindowUse) -> float:
    return (
        _ratio(use.credits_used, use.window.credits) if use.window.credits > 0 else 1.0
    )


def _shares(parts: dict, total: float) -> dict:
    return {key: (value / total if total > 0 else 0.0) for key, value in parts.items()}


class UsageReport:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _plan(self, key: str) -> Plan | None:
        return await self._session.get(Plan, key)

    def _period(
        self, terms: Terms, packs: list[ComputeGrant], month: date
    ) -> dict | None:
        """This month's plan pack: used and remaining as ratios, and when it
        resets. Before the month's first charge writes the pack it counts as
        issued and unused, as it does in the ledger's balance. None for a
        windowed plan, which issues no pack."""
        if terms.windowed:
            return None
        if terms.unlimited:
            return {
                "unlimited": True,
                "used_ratio": None,
                "remaining_ratio": None,
                "resets_at": None,
            }
        current = next(
            (
                p
                for p in packs
                if p.source == GrantSource.PLAN_PERIOD and p.period_start == month
            ),
            None,
        )
        total = current.credits_total if current else terms.issues
        used = current.credits_used if current else 0.0
        if total <= 0:
            return {
                "unlimited": False,
                "used_ratio": None,
                "remaining_ratio": None,
                "resets_at": None,
            }
        used_ratio = _ratio(used, total)
        return {
            "unlimited": False,
            "used_ratio": used_ratio,
            "remaining_ratio": 1.0 - used_ratio,
            "resets_at": month_end(month).isoformat(),
        }

    @staticmethod
    def _windows(uses: tuple[WindowUse, ...]) -> list[dict]:
        """Each window: how full it is and when it resets; an hours window no
        call has started has no reset yet."""
        return [
            {
                "hours": u.window.hours,
                "calendar": u.window.calendar,
                "used_ratio": _used(u),
                "resets_at": u.resets_at.isoformat() if u.resets_at else None,
            }
            for u in uses
        ]

    @staticmethod
    def _packs(packs: list[ComputeGrant]) -> list[dict]:
        """Every live pack besides the plan's, by what is left of it."""
        return [
            {
                "id": str(p.id),
                "source": p.source,
                "project_id": str(p.project_id) if p.project_id else None,
                "task_id": p.source_task_id,
                "remaining_ratio": 1.0 - _ratio(p.credits_used, p.credits_total),
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            }
            for p in packs
            if p.source != GrantSource.PLAN_PERIOD
        ]

    async def _month(
        self, team_id: int, month: date, *, lines: bool
    ) -> tuple[list[dict], dict[uuid.UUID, float], dict[str, float]]:
        """The month's spend: per day, per project and per product line, each
        as a share of the month's total."""
        start = datetime(month.year, month.month, 1, tzinfo=_TZ)
        end = month_end(month)
        rows = await UsageRepository(self._session).team_spend_by_day(
            team_id, since=start, until=end, tz=str(_TZ)
        )
        total = sum(credits for _, _, _, credits in rows)
        by_day: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        by_project: dict[uuid.UUID, float] = defaultdict(float)
        by_line: dict[str, float] = {line: 0.0 for line in LINES}
        for day, project_id, kind, credits in rows:
            if project_id is not None:
                by_project[project_id] += credits
            line = line_of(kind, project_id) if lines else None
            if line is not None:
                by_line[line] += credits
            by_day[day][line or "all"] += credits
        today = datetime.now(UTC).astimezone(_TZ).date()
        days = []
        day = month
        while day < end.date():
            spent = by_day.get(day, {})
            entry: dict = {"date": day.isoformat()}
            if day > today:
                entry["share"] = None
            else:
                entry["share"] = _ratio(sum(spent.values()), total)
                if lines:
                    entry["lines"] = {
                        line: _ratio(spent.get(line, 0.0), total) for line in LINES
                    }
            days.append(entry)
            day += timedelta(days=1)
        return days, _shares(dict(by_project), total), _shares(by_line, total)

    async def team(self, team_id: int, plan_key: str, *, lines: bool = False) -> dict:
        """A team's month: its plan, this period, windows, other packs, each
        day's and each project's share of the month's spend. ``lines`` splits
        it by product line too, for a person's own page (their personal team)."""
        month = month_of(datetime.now(UTC))
        terms = (await terms_of(self._session, [plan_key]))[plan_key]
        plan = await self._plan(plan_key)
        ledger = Ledger(self._session)
        packs = await ledger.team_packs(team_id)
        uses = await ledger.window_uses([Payer(team_id=team_id, terms=terms)])
        days, projects, by_line = await self._month(team_id, month, lines=lines)
        out = {
            "plan": {"key": plan_key, "name": plan.name if plan else plan_key},
            "period": self._period(terms, packs, month),
            "windows": self._windows(uses.get(team_id, ())),
            "packs": self._packs(packs),
            "days": days,
            "projects": [
                {"id": str(pid), "share": share}
                for pid, share in sorted(projects.items(), key=lambda kv: -kv[1])
            ],
        }
        if lines:
            out["lines"] = by_line
        return out

    async def teams_left(self, teams: list) -> list[dict]:
        """For each of ``teams``: its plan's name and what is left of this
        month's plan pack."""
        if not teams:
            return []
        month = month_of(datetime.now(UTC))
        terms = await terms_of(self._session, [t.plan_key for t in teams])
        plans = {
            p.key: p
            for p in (
                await self._session.execute(
                    select(Plan).where(Plan.key.in_({t.plan_key for t in teams}))
                )
            ).scalars()
        }
        ledger = Ledger(self._session)
        packs = await ledger.live_packs([t.id for t in teams])
        uses = await ledger.window_uses(
            Payer(team_id=t.id, terms=terms[t.plan_key]) for t in teams
        )
        out = []
        for team in teams:
            period = self._period(terms[team.plan_key], packs.get(team.id, []), month)
            if period is None:
                # A windowed plan: what its fullest window has left.
                left = 1.0 - max((_used(u) for u in uses.get(team.id, ())), default=0.0)
                period = {"unlimited": False, "remaining_ratio": left}
            plan = plans.get(team.plan_key)
            out.append(
                {
                    "id": team.id,
                    "name": team.name,
                    "handle": team.handle,
                    "plan": {
                        "key": team.plan_key,
                        "name": plan.name if plan else team.plan_key,
                    },
                    "unlimited": period["unlimited"],
                    "remaining_ratio": period["remaining_ratio"],
                }
            )
        return out
