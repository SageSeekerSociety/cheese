"""Credit plans and the platform administrator's hand on them (#2397).

A plan is a record, not code: an administrator edits what it issues or its time
windows, the model tiers it allows and its rank, puts a team on one, deletes one
no team is on, and issues a team extra credits. Every one of those writes is
recorded in ``credit_admin_audit`` with who did it and what changed; a change to
a plan takes effect from the next period, so a pack already issued keeps its
size.
"""

import math
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.team.models import DEFAULT_PLAN_KEY
from app.domain.team.services import team_service
from app.domain.usage.ledger import Ledger, month_of
from app.domain.usage.models import (
    ComputeGrant,
    CreditAdminAudit,
    GrantSource,
    Plan,
)
from app.domain.user.services import (
    faces_by_handle,
    search_accounts,
    usernames_by_ids,
    users_by_handle,
)

#: The fields of a plan an administrator may change.
EDITABLE = (
    "name",
    "audience",
    "credits_per_period",
    "windows",
    "model_tiers",
    "rank",
)
CALENDARS = frozenset({"week", "month"})
MODEL_TIERS = frozenset({"included", "premium", "frontier"})
AUDIENCES = frozenset({"personal", "team", "both"})


def plan_out(plan: Plan) -> dict:
    return {
        "key": plan.key,
        "name": plan.name,
        "audience": plan.audience,
        "credits_per_period": plan.credits_per_period,
        "period": plan.period,
        "windows": list(plan.windows or []),
        "model_tiers": None if plan.model_tiers is None else list(plan.model_tiers),
        "unlimited": plan.unlimited,
        "admin_only": plan.admin_only,
        "rank": plan.rank,
    }


def pack_out(pack: ComputeGrant) -> dict:
    return {
        "id": str(pack.id),
        "source": pack.source,
        "project_id": str(pack.project_id) if pack.project_id else None,
        "task_id": pack.source_task_id,
        "credits_total": pack.credits_total,
        "credits_used": pack.credits_used,
        "period_start": pack.period_start.isoformat() if pack.period_start else None,
        "expires_at": pack.expires_at.isoformat() if pack.expires_at else None,
        "reason": pack.reason,
        "created_at": pack.created_at.isoformat(),
    }


def _plan_fields(data: dict) -> dict:
    """The editable fields of ``data``, each window holding only the keys it
    uses."""
    data = {k: v for k, v in data.items() if k in EDITABLE}
    if data.get("rank", 0) is None:
        del data["rank"]
    if data.get("windows") is not None:
        data["windows"] = [
            {k: v for k, v in w.items() if v is not None} for w in data["windows"]
        ]
    return data


def _check_plan_fields(data: dict) -> None:
    credits = data.get("credits_per_period")
    if credits is not None and (not math.isfinite(credits) or credits < 0):
        raise BadRequestError(say("planMonthlyNonNegative"))
    keys = set()
    for window in data.get("windows") or []:
        hours, calendar = window.get("hours"), window.get("calendar")
        cap = window.get("credits")
        if (hours is None) == (calendar is None):
            raise BadRequestError(say("planWindowUnit"))
        if calendar is not None and calendar not in CALENDARS:
            raise BadRequestError(say("planWindowReset"))
        if hours is not None and (not isinstance(hours, int | float) or hours <= 0):
            raise BadRequestError(say("planWindowHoursPositive"))
        if not isinstance(cap, int | float) or not math.isfinite(cap) or cap <= 0:
            raise BadRequestError(say("planWindowLimitPositive"))
        key = calendar or f"{hours:g}h"
        if key in keys:
            raise BadRequestError(say("planWindowDuplicate"))
        keys.add(key)
    audience = data.get("audience")
    if audience is not None and audience not in AUDIENCES:
        raise BadRequestError(
            say("planAudienceInvalid", options=", ".join(sorted(AUDIENCES)))
        )
    tiers = data.get("model_tiers")
    if tiers is not None and not set(tiers) <= MODEL_TIERS:
        raise BadRequestError(
            say("planModelTierInvalid", options=", ".join(sorted(MODEL_TIERS)))
        )


def _check_billing(plan: Plan) -> None:
    """A plan bills one way: a monthly pack or time windows, never both. Only
    an unlimited plan has neither."""
    if plan.unlimited:
        return
    monthly = plan.credits_per_period is not None
    windowed = bool(plan.windows)
    if monthly and windowed:
        raise BadRequestError(say("planEitherMonthlyOrWindows"))
    if not monthly and not windowed:
        raise BadRequestError(say("planNeedsMonthlyOrWindow"))


def _period(held: list[ComputeGrant]) -> dict:
    """This month's plan pack: ``credits_total`` is None until the month's
    first call has issued it."""
    month = month_of(datetime.now(UTC))
    current = next(
        (
            p
            for p in held
            if p.source == GrantSource.PLAN_PERIOD and p.period_start == month
        ),
        None,
    )
    return {
        "start": month.isoformat(),
        "credits_total": current.credits_total if current else None,
        "credits_used": current.credits_used if current else 0.0,
    }


class PlanService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def plans(self) -> list[dict]:
        """Every plan, with how many teams are on it and whether new teams start
        on it."""

        rows = await self._session.execute(select(Plan).order_by(Plan.rank, Plan.key))
        counts = await team_service(self._session).teams_per_plan()
        return [
            {
                **plan_out(p),
                "team_count": counts.get(p.key, 0),
                "is_default": p.key == DEFAULT_PLAN_KEY,
            }
            for p in rows.scalars()
        ]

    async def _plan(self, key: str) -> Plan:
        plan = await self._session.get(Plan, key)
        if plan is None:
            raise NotFoundError(say("planNotFound", plan=key))
        return plan

    async def create_plan(self, *, handle: str, data: dict) -> dict:
        """A new plan, issuing ``credits_per_period`` every month to the teams an
        administrator puts on it."""
        key = data.get("key") or f"plan-{uuid.uuid4().hex[:8]}"
        data = _plan_fields(data)
        _check_plan_fields(data)
        if await self._session.get(Plan, key) is not None:
            raise BadRequestError(say("planExists", plan=key))
        plan = Plan(
            key=key,
            name=data.get("name") or key,
            audience=data.get("audience") or "both",
            credits_per_period=data.get("credits_per_period"),
            period="month",
            windows=data.get("windows") or [],
            model_tiers=data.get("model_tiers", ["included"]),
            unlimited=False,
            admin_only=False,
            rank=data.get("rank") or 0,
        )
        _check_billing(plan)
        self._session.add(plan)
        await self._session.flush()
        after = plan_out(plan)
        await self._audit(handle, "plan.create", key, None, after)
        return after

    async def update_plan(self, *, handle: str, key: str, data: dict) -> dict:
        """Change what a plan issues and allows, from the next period on."""
        plan = await self._plan(key)
        data = _plan_fields(data)
        _check_plan_fields(data)
        before = plan_out(plan)
        for field, value in data.items():
            setattr(plan, field, value)
        _check_billing(plan)
        await self._session.flush()
        after = plan_out(plan)
        await self._audit(handle, "plan.update", key, before, after)
        return after

    async def delete_plan(self, *, handle: str, key: str) -> None:
        """Delete a plan no team is on. The plan new teams start on stays."""

        plan = await self._plan(key)
        if key == DEFAULT_PLAN_KEY:
            raise BadRequestError(say("planDefaultCannotDelete", plan=plan.name))
        teams = (await team_service(self._session).teams_per_plan()).get(key, 0)
        if teams:
            raise BadRequestError(say("planInUse", count=teams, plan=plan.name))
        before = plan_out(plan)
        await self._session.delete(plan)
        await self._session.flush()
        await self._audit(handle, "plan.delete", key, before, None)

    async def set_team_plan(self, *, handle: str, team_id: int, key: str) -> dict:

        team = await team_service(self._session).get_team(team_id)
        if team is None:
            raise NotFoundError(say("teamNotFoundById", team_id=team_id))
        plan = await self._plan(key)
        personal = team.personal_owner_user_id is not None
        if plan.audience == "personal" and not personal:
            raise BadRequestError(say("planPersonalOnly", name=plan.name))
        if plan.audience == "team" and personal:
            raise BadRequestError(say("planTeamOnly", name=plan.name))
        before = {"plan_key": team.plan_key}
        team.plan_key = key
        await self._session.flush()
        await self._audit(handle, "team.plan", str(team_id), before, {"plan_key": key})
        return {"team_id": team_id, "plan_key": key}

    async def grant(
        self,
        *,
        handle: str,
        team_id: int,
        credits: float,
        expires_at: datetime | None,
        reason: str | None = None,
    ) -> dict:
        """Issue a team credits by hand, as an administrator does once a
        purchase has been paid for outside the platform."""

        if await team_service(self._session).get_team(team_id) is None:
            raise NotFoundError(say("teamNotFoundById", team_id=team_id))
        if expires_at is not None and expires_at <= datetime.now(UTC):
            raise BadRequestError(say("grantExpiryPast"))
        try:
            pack = await Ledger(self._session).grant(
                team_id,
                credits,
                source=GrantSource.ADMIN_GRANT,
                expires_at=expires_at,
                reason=reason,
            )
        except ValueError as exc:
            raise BadRequestError(say("grantAmountPositive")) from exc
        out = pack_out(pack)
        await self._audit(handle, "team.grant", str(team_id), None, out)
        return out

    async def teams(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
        plan_key: str | None = None,
        personal: bool | None = None,
    ) -> dict:
        """The console's list: every team with its plan, this period's plan
        pack, and the packs it may still spend. ``plan_key`` and ``personal``
        narrow it before it is paged."""

        owner_ids: list[int] = []
        if query and query.strip():
            handles = [h for h, _ in await search_accounts(self._session, query, 50)]
            owners = await users_by_handle(self._session, handles)
            owner_ids = [user.id for user in owners.values()]
        teams_svc = team_service(self._session)
        teams, total = await teams_svc.list_all_teams(
            query=query,
            owner_ids=owner_ids,
            limit=page_size,
            offset=(page - 1) * page_size,
            plan_key=plan_key,
            personal=personal,
        )
        packs = await Ledger(self._session).live_packs([t.id for t in teams])
        owners_by_id = await usernames_by_ids(
            self._session,
            [t.personal_owner_user_id for t in teams if t.personal_owner_user_id],
        )
        faces = await faces_by_handle(self._session, owners_by_id.values())
        members = await teams_svc.member_counts(
            [t.id for t in teams if not t.personal_owner_user_id]
        )
        items = []
        for team in teams:
            held = packs.get(team.id, [])
            owner = team.personal_owner_user_id
            owner_handle = owners_by_id.get(owner) if owner else None
            items.append(
                {
                    "id": team.id,
                    "name": team.name,
                    "handle": team.handle,
                    "personal_owner": owner_handle,
                    "personal_owner_nickname": (
                        faces.get(owner_handle, (None, None))[0]
                        if owner_handle
                        else None
                    ),
                    "member_count": None if owner else members.get(team.id, 0),
                    "plan_key": team.plan_key,
                    "period": _period(held),
                    "packs": [pack_out(p) for p in held],
                }
            )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def team(self, team_id: int) -> dict:
        """One team as the console opens it: its plan, this period, and every
        pack it may still spend."""

        teams_svc = team_service(self._session)
        team = await teams_svc.get_team(team_id)
        if team is None:
            raise NotFoundError(say("teamNotFoundById", team_id=team_id))
        plan = await self._plan(team.plan_key)
        held = (await Ledger(self._session).live_packs([team.id])).get(team.id, [])
        owner = team.personal_owner_user_id
        owners = await usernames_by_ids(self._session, [owner] if owner else [])
        owner_handle = owners.get(owner) if owner else None
        faces = await faces_by_handle(
            self._session, [owner_handle] if owner_handle else []
        )
        members = await teams_svc.member_counts([] if owner else [team.id])
        return {
            "id": team.id,
            "name": team.name,
            "handle": team.handle,
            "personal_owner": owner_handle,
            "personal_owner_nickname": (
                faces.get(owner_handle, (None, None))[0] if owner_handle else None
            ),
            "member_count": None if owner else members.get(team.id, 0),
            "plan": plan_out(plan),
            "period": _period(held),
            "packs": [pack_out(p) for p in held],
        }

    async def team_history(self, team_id: int, *, limit: int) -> list[dict]:
        """What administrators did to one team, newest first."""
        return await self.audit(limit=limit, target=str(team_id))

    async def audit(self, *, limit: int, target: str | None = None) -> list[dict]:
        stmt = select(CreditAdminAudit)
        if target is not None:
            stmt = stmt.where(
                CreditAdminAudit.target == target,
                CreditAdminAudit.action.like("team.%"),
            )

        rows = (
            (
                await self._session.execute(
                    stmt.order_by(CreditAdminAudit.created_at.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        faces = await faces_by_handle(self._session, (r.actor_handle for r in rows))
        return [
            {
                "created_at": row.created_at.isoformat(),
                "actor_handle": row.actor_handle,
                # Resolved now, not stored: a nickname changes after the fact.
                "actor_name": faces.get(row.actor_handle, (None, None))[0],
                "action": row.action,
                "target": row.target,
                "before": row.before,
                "after": row.after,
            }
            for row in rows
        ]

    async def _audit(
        self,
        handle: str,
        action: str,
        target: str,
        before: dict | None,
        after: dict | None,
    ) -> None:
        self._session.add(
            CreditAdminAudit(
                actor_handle=handle[:64],
                action=action,
                target=target,
                before=before,
                after=after,
            )
        )
        await self._session.flush()
