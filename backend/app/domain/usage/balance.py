"""What a payer may still spend, and what it is told when it may spend nothing.

A charge drains the packs that apply in this order:

1. what a task earmarked for this project;
2. the paying team's plan pack for the month;
3. credits bought or granted by an administrator, what lapses first first.

A windowed plan drains a task's earmark first, which never fills a window; the
rest fills every window. Once any window is full, the rest drains bought or
granted credits instead, and with none the call waits for the window to reset.
The last call may overdraw; it was admitted with credits left, and what it spent
is spent either way.

Everything here is a pure function of the packs, the payers and the window rows
the ledger read (``ledger_queries``): which packs are spendable, in what order,
how full each window is, and the sentence a refusal says. The reading and the
writing are the ledger's.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.sentences import NoticeText, say
from app.domain.usage.models import ComputeGrant, GrantSource, PlanWindowUse
from app.domain.usage.payers import Payer
from app.domain.usage.windows import _TZ, WindowUse, _window_use, month_end, month_of


def _applies(pack: ComputeGrant, payer: Payer) -> bool:
    if pack.project_id is not None:
        return pack.project_id == payer.project_id
    return pack.team_id == payer.team_id


def _live(pack: ComputeGrant, now: datetime) -> bool:
    return pack.expires_at is None or pack.expires_at > now


def _rank(pack: ComputeGrant) -> int:
    if pack.project_id is not None:
        return 0
    if pack.source == GrantSource.PLAN_PERIOD:
        return 1
    return 2


_FAR = datetime.max.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Refusal:
    """Why a call may not run, and when it may."""

    message: NoticeText
    reopens_at: datetime | None

    def retry_after_s(self) -> int:
        """Seconds a refused caller should wait, an hour when nothing will
        change on its own."""
        if self.reopens_at is None:
            return 3600
        return max(60, int((self.reopens_at - datetime.now(UTC)).total_seconds()))


def _hours(hours: float) -> str:
    return f"{hours:g}"


def _day(at: datetime) -> dict:
    local = at.astimezone(_TZ)
    return {"month": local.month, "day": local.day, "time": local.strftime("%H:%M")}


def _order(pack: ComputeGrant) -> tuple:
    # Within a rank, what lapses first is spent first.
    return (_rank(pack), pack.expires_at or _FAR, pack.created_at, pack.id)


def _window_refusal(use: WindowUse) -> Refusal:
    at = use.resets_at
    assert at is not None
    if use.window.calendar == "week":
        return Refusal(say("creditsWeekFull", **_day(at)), at)
    if use.window.calendar == "month":
        local = at.astimezone(_TZ)
        return Refusal(say("creditsMonthFull", month=local.month, day=local.day), at)
    assert use.window.hours is not None
    return Refusal(
        say("creditsWindowFull", hours=_hours(use.window.hours), **_day(at)), at
    )


@dataclass(frozen=True)
class Balance:
    """What a payer may still spend. For a windowed plan, what its fullest
    window still allows counts alongside the packs, as if it were one."""

    unlimited: bool
    credits_total: float
    credits_used: float
    # When the payer's plan next issues a pack; None when it issues none.
    resets_at: datetime | None
    packs: tuple[ComputeGrant, ...] = ()
    # A windowed plan's windows, each as full as it is now.
    windows: tuple[WindowUse, ...] = ()

    @property
    def credits_remaining(self) -> float:
        return self.credits_total - self.credits_used

    @property
    def exhausted(self) -> bool:
        return not self.unlimited and self.credits_remaining <= 0

    def packs_remaining(self) -> float:
        """What is left of every pack besides the plan's own."""
        return sum(
            p.credits_total - p.credits_used
            for p in self.packs
            if p.source != GrantSource.PLAN_PERIOD
        )

    def refusal(self) -> Refusal:
        full = [w for w in self.windows if w.full]
        if full:
            return _window_refusal(max(full, key=lambda w: w.resets_at or _FAR))
        if self.resets_at is None:
            return Refusal(say("creditsSpent"), None)
        at = self.resets_at.astimezone(_TZ)
        return Refusal(
            say("creditsMonthSpent", month=at.month, day=at.day), self.resets_at
        )

    def summary(self) -> dict:
        """The shape the credit endpoints have always answered with."""
        return {
            "unlimited": self.unlimited,
            "credits_total": self.credits_total,
            "credits_used": self.credits_used,
            "credits_remaining": self.credits_remaining,
            "grants": list(self.packs),
        }


def _spendable(candidates: Iterable[ComputeGrant], payer: Payer) -> list:
    """Of ``candidates``, what ``payer`` may spend now, in the order a charge
    drains them. A windowed plan spends no plan pack, even one issued for this
    month before the plan stopped issuing them."""
    now = datetime.now(UTC)
    return sorted(
        (
            p
            for p in candidates
            if _applies(p, payer)
            and _live(p, now)
            and not (payer.terms.windowed and p.source == GrantSource.PLAN_PERIOD)
        ),
        key=_order,
    )


def _issued_this_month(candidates: Iterable[ComputeGrant], payer: Payer) -> bool:
    month = month_of(datetime.now(UTC))
    return any(
        p.source == GrantSource.PLAN_PERIOD
        and p.team_id == payer.team_id
        and p.period_start == month
        for p in candidates
    )


def _balance(
    candidates: list[ComputeGrant],
    payer: Payer,
    rows: Mapping[tuple[int, str], PlanWindowUse],
) -> Balance:
    packs = _spendable(candidates, payer)
    # The month's plan pack counts before its first charge writes it.
    unissued = 0.0
    if payer.terms.issues and not _issued_this_month(candidates, payer):
        unissued = payer.terms.issues
    resets_at = None
    if payer.terms.issues:
        resets_at = month_end(month_of(datetime.now(UTC)))
    windows: tuple[WindowUse, ...] = ()
    room = 0.0
    if payer.terms.windowed:
        now = datetime.now(UTC)
        windows = tuple(
            _window_use(w, rows.get((payer.team_id, w.key)), now)
            for w in payer.terms.windows
        )
        room = min(w.room for w in windows)
    return Balance(
        unlimited=payer.terms.unlimited,
        credits_total=sum(p.credits_total for p in packs) + unissued + room,
        credits_used=sum(p.credits_used for p in packs),
        resets_at=resets_at,
        packs=tuple(packs),
        windows=windows,
    )
