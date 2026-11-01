"""Project caps and member task caps over the existing credit ledger."""

import math
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import and_, delete, func, null, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.core.request_facts import recall, remember
from app.core.sentences import say
from app.domain.conversation.models import Conversation, ConversationKind
from app.domain.project.services import ProjectService
from app.domain.usage.balance import Refusal
from app.domain.usage.models import (
    MemberCreditBudget,
    ProjectCreditBudget,
    ResourceUsage,
    WorkCreditOwner,
)
from app.domain.usage.repositories import (
    gateway_spend_select,
    remember_gateway_spend,
)


class CreditBudgets:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def snapshot_owner(
        self,
        work_id: uuid.UUID,
        task_id: uuid.UUID,
        owner_handle: str,
        project_id: uuid.UUID,
        started_at: datetime,
    ) -> None:
        """First execution wins, including when a restart opens the same turn."""
        await self._session.execute(
            insert(WorkCreditOwner)
            .values(
                work_id=work_id,
                conversation_id=task_id,
                owner_handle=owner_handle,
                project_id=project_id,
                started_at=started_at,
            )
            .on_conflict_do_nothing(index_elements=[WorkCreditOwner.work_id])
        )

    async def owner_of(
        self, work_id: uuid.UUID | None, project_id: uuid.UUID | None
    ) -> str | None:
        execution = await self.execution(work_id, project_id)
        return execution.owner_handle if execution is not None else None

    async def execution(
        self, work_id: uuid.UUID | None, project_id: uuid.UUID | None
    ) -> WorkCreditOwner | None:
        if work_id is None or project_id is None:
            return None
        return await self._session.scalar(
            select(WorkCreditOwner).where(
                WorkCreditOwner.work_id == work_id,
                WorkCreditOwner.project_id == project_id,
            )
        )

    async def limits(self, project_id: uuid.UUID) -> dict[str, float]:
        rows = await self._session.execute(
            select(
                MemberCreditBudget.owner_handle, MemberCreditBudget.credits_limit
            ).where(MemberCreditBudget.project_id == project_id)
        )
        return {handle: limit for handle, limit in rows}

    async def save(
        self,
        project_id: uuid.UUID,
        *,
        credits_limit: float | None,
        set_project_limit: bool,
        member_limits: Mapping[str, float | None],
    ) -> dict:
        if any(
            amount is not None and (not math.isfinite(amount) or amount < 0)
            for amount in (credits_limit, *member_limits.values())
        ):
            raise BadRequestError(say("creditBudgetInvalidLimit"))
        project = await ProjectService(self._session).get(project_id)
        assert project is not None
        await self._session.execute(
            insert(ProjectCreditBudget)
            .values(
                project_id=project_id,
                credits_limit=credits_limit if set_project_limit else None,
                since=project.created_at,
            )
            .on_conflict_do_nothing(index_elements=[ProjectCreditBudget.project_id])
        )
        budget = await self._session.scalar(
            select(ProjectCreditBudget)
            .where(ProjectCreditBudget.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert budget is not None
        if set_project_limit:
            budget.credits_limit = credits_limit
        for handle, limit in member_limits.items():
            if limit is None:
                await self._session.execute(
                    delete(MemberCreditBudget).where(
                        MemberCreditBudget.project_id == project_id,
                        MemberCreditBudget.owner_handle == handle,
                    )
                )
            else:
                await self._session.execute(
                    insert(MemberCreditBudget)
                    .values(
                        project_id=project_id, owner_handle=handle, credits_limit=limit
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            MemberCreditBudget.project_id,
                            MemberCreditBudget.owner_handle,
                        ],
                        set_={"credits_limit": limit, "updated_at": datetime.now(UTC)},
                    )
                )
        await self._session.flush()
        return await self.report(project_id)

    async def _spending(self, project_id: uuid.UUID, since: datetime) -> list:
        rows = await self._session.execute(
            select(
                ResourceUsage.credit_owner_handle,
                Conversation.kind,
                func.coalesce(func.sum(ResourceUsage.credits), 0),
            )
            .outerjoin(Conversation, Conversation.id == ResourceUsage.conversation_id)
            .where(
                ResourceUsage.project_id == project_id,
                ResourceUsage.created_at >= since,
                ResourceUsage.created_at <= datetime.now(UTC),
            )
            .group_by(ResourceUsage.credit_owner_handle, Conversation.kind)
        )
        return list(rows.all())

    async def report(self, project_id: uuid.UUID) -> dict:
        project = await ProjectService(self._session).get(project_id)
        assert project is not None
        budget = await self._session.get(ProjectCreditBudget, project_id)
        limit = budget.credits_limit if budget is not None else None
        since = budget.since if budget is not None else project.created_at
        limits = await self.limits(project_id)
        members: dict[str, dict] = {
            handle: {"credits_limit": cap, "credits_used": 0.0}
            for handle, cap in limits.items()
        }
        total = public = unknown = 0.0
        for owner, kind, credits in await self._spending(project_id, since):
            total += credits
            if owner is not None:
                member = members.setdefault(
                    owner, {"credits_limit": None, "credits_used": 0.0}
                )
                member["credits_used"] += credits
            elif kind == ConversationKind.task or kind is None:
                unknown += credits
            else:
                public += credits
        return {
            "credits_limit": limit,
            "credits_used": total,
            "credits_remaining": max(0.0, limit - total) if limit is not None else None,
            "public_credits": public,
            "unattributed_credits": unknown,
            "members": members,
            "since": since.isoformat(),
        }

    async def project_refusal(self, project_id: uuid.UUID) -> Refusal | None:
        return (await self.project_refusals([project_id])).get(project_id)

    async def project_refusals(
        self, project_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Refusal]:
        """The projects among ``project_ids`` whose own credit budget is spent,
        in one read."""
        caps = await self.project_caps(project_ids)
        return {
            pid: Refusal(say("projectCreditBudgetSpent"), None)
            for pid, (limit, spent) in caps.items()
            if spent >= limit
        }

    async def project_caps(
        self, project_ids: list[uuid.UUID], *, with_gateway_spend: bool = False
    ) -> dict[uuid.UUID, tuple[float, float]]:
        """Project → (credit limit, credits used since the limit was set), for
        the projects among ``project_ids`` that have a limit.

        ``with_gateway_spend``: the same statement also reads what each
        project's gateway key has spent, and leaves it for
        `UsageRepository.gateway_spend` to answer from. Admission prices the
        key's budget from both; the other callers want the caps alone. Only a
        call that reads the caps brings the spend: when they were already read
        in this request, the spend is left to its own statement, as before, so
        a caller that wants it saved asks this first (`routes/llm_proxy.py`)."""
        if not project_ids:
            return {}
        # Admitting a model call asks this, then asks it again for the budget
        # its gateway key carries (`routes/llm_proxy.py`).
        can_cache = isinstance(self._session, AsyncSession)
        key = ("project_caps", frozenset(project_ids))
        if can_cache:
            if self._session.autoflush:
                await self._session.flush()
            found, cached = recall(self._session, key)
            if found:
                return cached
        capped = (
            select(
                ProjectCreditBudget.project_id,
                ProjectCreditBudget.credits_limit,
                func.coalesce(func.sum(ResourceUsage.credits), 0).label("used"),
            )
            .outerjoin(
                ResourceUsage,
                and_(
                    ResourceUsage.project_id == ProjectCreditBudget.project_id,
                    ResourceUsage.created_at >= ProjectCreditBudget.since,
                    ResourceUsage.created_at <= datetime.now(UTC),
                ),
            )
            .where(
                ProjectCreditBudget.project_id.in_(project_ids),
                ProjectCreditBudget.credits_limit.is_not(None),
            )
            .group_by(ProjectCreditBudget.project_id)
        )
        if not with_gateway_spend:
            rows = await self._session.execute(capped.add_columns(null()))
        else:
            # Each sum is its own subquery, so neither multiplies the other's
            # rows; the gateway one is `UsageRepository`'s own, with the
            # predicate of the index that answers it.
            caps_q = capped.subquery("capped")
            spent_q = gateway_spend_select(project_ids).subquery("gateway")
            rows = await self._session.execute(
                select(
                    func.coalesce(caps_q.c.project_id, spent_q.c.project_id),
                    caps_q.c.credits_limit,
                    caps_q.c.used,
                    spent_q.c.spent,
                ).select_from(
                    caps_q.join(
                        spent_q, spent_q.c.project_id == caps_q.c.project_id, full=True
                    )
                )
            )
        caps: dict[uuid.UUID, tuple[float, float]] = {}
        gateway_spend: dict[uuid.UUID, float] = {}
        for project_id, limit, used, gateway_usd in rows:
            if limit is not None:
                caps[project_id] = (limit, used)
            if gateway_usd is not None:
                gateway_spend[project_id] = float(gateway_usd)
        if can_cache:
            remember(
                self._session,
                key,
                caps,
                tables=frozenset({"project_credit_budgets", "resource_usage"}),
            )
            if with_gateway_spend:
                remember_gateway_spend(self._session, project_ids, gateway_spend)
        return caps

    async def member_refusal(
        self, project_id: uuid.UUID, owner_handle: str | None
    ) -> Refusal | None:
        remaining = await self.member_remaining(project_id, owner_handle)
        if remaining is not None and remaining <= 0:
            return Refusal(say("memberCreditBudgetSpent", owner=owner_handle), None)
        return None

    async def member_remaining(
        self, project_id: uuid.UUID, owner_handle: str | None
    ) -> float | None:
        if owner_handle is None:
            return None
        cap = await self._session.get(MemberCreditBudget, (project_id, owner_handle))
        if cap is None:
            return None
        budget = await self._session.get(ProjectCreditBudget, project_id)
        assert budget is not None
        spent = await self._session.scalar(
            select(func.coalesce(func.sum(ResourceUsage.credits), 0)).where(
                ResourceUsage.project_id == project_id,
                ResourceUsage.credit_owner_handle == owner_handle,
                ResourceUsage.created_at >= budget.since,
                ResourceUsage.created_at <= datetime.now(UTC),
            )
        )
        return cap.credits_limit - float(spent or 0)
