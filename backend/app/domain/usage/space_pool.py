"""A Space's shared compute pool — funding it, watching it, warning about it.

The 2026-09-14 decision (商业化第一步) is that a 空间 buys ONE pool rather than
a per-head allowance: a course tops up once, every project under its 赛题
draws on that balance, and when it is gone everyone is refused. This module is
the teaching side of that decision — the pieces a 老师 or 三创中心 touches.
The spending side is `ComputeGrantRepository.list_for_project`.

Three properties of a shared pool are load-bearing and repeated here so no
reader has to rediscover them:

* **It can overspend, by design.** The gate is checked once on the way in and
  the charge lands when the turn settles, so the worst case is roughly
  (concurrently running sessions × one call's cost) past the cap. `consume`
  allows the overdraft on purpose — a true ledger beats a tidy one. Never
  promise a course that the pool stops exactly at zero.
* **"Unlimited" is unchanged.** A project with no grants at all is still
  unmetered (`summary()["unlimited"]`), so not buying a pool changes nothing.
  The blast radius of this feature is exactly the Spaces that get one.
* **A pool reaches only projects published under its Space**, one hop through
  the project's `external_task_id`. A project made outside any course holds
  nothing that names a pool and can never spend one.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.notification.models import NotificationType
from app.domain.notification.publisher import publish_notification_event
from app.domain.space.models import SpaceAdminRelation
from app.domain.usage.repositories import (
    SPACE_POOL_ALERT_RATIO,
    ComputeGrantRepository,
    UsageRepository,
)


class SpacePoolService:
    """Fund a Space's pool, read it back, and warn when it runs low."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Teaching side: fund
    # ------------------------------------------------------------------

    async def fund(
        self, *, space_id: int, credits_total: float, actor_user_id: int | None
    ) -> dict:
        """Top the Space's pool up by ``credits_total``.

        A top-up, not a reset: grants accumulate, so a teacher who adds 500
        twice has 1000. Replacing the balance instead would silently write off
        credits the class has already spent, and the ledger would stop
        reconciling against what was paid for.

        Spending someone else's budget is the whole thing an admin check is
        for, so it runs before the grant exists rather than after.
        """
        await self._ensure_admin(space_id, actor_user_id)
        await ComputeGrantRepository(self._session).grant_space(space_id, credits_total)
        return await self._overview(space_id)

    # ------------------------------------------------------------------
    # Teaching side: see
    # ------------------------------------------------------------------

    async def overview(self, *, space_id: int, actor_user_id: int | None) -> dict:
        """The pool's balance plus what each project under the Space spent.

        Both halves are needed to answer the question a teacher actually has.
        A balance alone cannot tell "nobody is using it" from "two teams are
        using all of it", and the second one is the conversation.

        Admin-only, unlike the Space's public pages: the per-project rows name
        which team is spending, across teams that cannot otherwise see each
        other's work.
        """
        await self._ensure_admin(space_id, actor_user_id)
        return await self._overview(space_id)

    async def _ensure_admin(self, space_id: int, actor_user_id: int | None) -> None:
        from app.domain.space.repositories import (
            SpaceAdminRelationRepository,
            SpaceCategoryRepository,
            SpaceRepository,
        )
        from app.domain.space.services import SpaceService

        service = SpaceService(
            SpaceRepository(session=self._session),
            SpaceCategoryRepository(session=self._session),
            SpaceAdminRelationRepository(session=self._session),
        )
        # Borrow the existing rule instead of restating it here: an
        # authorization check written twice is one that can disagree with
        # itself, and the permissive copy is the one that ships.
        await service.ensure_admin(space_id, actor_user_id, allow_admin=True)

    async def _overview(self, space_id: int) -> dict:
        from app.domain.project.models import Project
        from app.domain.project.repositories import ProjectRepository

        summary = await ComputeGrantRepository(self._session).space_summary(space_id)

        project_ids = await ProjectRepository(self._session).list_ids_for_space_tasks(
            space_id
        )
        spend = await UsageRepository(self._session).for_projects(project_ids)
        names = dict(
            (
                await self._session.execute(
                    select(Project.id, Project.name).where(Project.id.in_(project_ids))
                )
            ).all()
        )

        projects = [
            {
                "project_id": str(pid),
                "name": names.get(pid, ""),
                "cost_usd": spend.get(pid, {}).get("cost_usd", 0.0),
                "total_tokens": spend.get(pid, {}).get("total_tokens", 0),
                "turns": spend.get(pid, {}).get("turns", 0),
            }
            for pid in project_ids
        ]
        projects.sort(key=lambda row: row["cost_usd"], reverse=True)

        return {
            "space_id": space_id,
            "has_pool": summary["has_pool"],
            "credits_total": summary["credits_total"],
            "credits_used": summary["credits_used"],
            "credits_remaining": summary["credits_remaining"],
            "credits_ratio": summary["credits_ratio"],
            "alert_ratio": SPACE_POOL_ALERT_RATIO,
            "exhausted": summary["exhausted"],
            "needs_attention": summary["needs_attention"],
            "projects": projects,
        }

    # ------------------------------------------------------------------
    # Teaching side: warn
    # ------------------------------------------------------------------

    async def alert_if_needed(self, *, space_id: int) -> bool:
        """Tell the Space's admins when its pool passes the warn mark.

        Once per dip, not once per turn: `consume` runs on every settled turn,
        so a pool that sits at 85% for a week would otherwise post the same
        warning hundreds of times. `_already_alerted` suppresses while the
        standing warning is still the current truth — top the pool up and
        drain it again and the teacher is warned about the second dip too.

        Best-effort by contract: the caller is settling a turn, and a warning
        that cannot be delivered must not become a turn that cannot be closed.
        """
        summary = await ComputeGrantRepository(self._session).space_summary(space_id)
        if not summary["needs_attention"]:
            return False

        recipients = {
            int(r)
            for r in (
                await self._session.scalars(
                    select(SpaceAdminRelation.user_id).where(
                        SpaceAdminRelation.space_id == space_id,
                        SpaceAdminRelation.deleted_at.is_(None),
                    )
                )
            ).all()
            if r
        }
        if not recipients:
            return False

        if await self._already_alerted(space_id, recipients, summary["grants"]):
            return False

        await publish_notification_event(
            self._session,
            recipient_ids=recipients,
            type_=NotificationType.SPACE_COMPUTE_POOL_LOW,
            payload={
                "spaceId": str(space_id),
                "creditsTotal": summary["credits_total"],
                "creditsUsed": summary["credits_used"],
                "creditsRemaining": summary["credits_remaining"],
                "ratio": summary["credits_ratio"],
            },
            source="space-pool",
        )
        return True

    async def _already_alerted(
        self, space_id: int, recipients: set[int], grants: list
    ) -> bool:
        """Does a warning still describe the pool as it stands?

        A warning is stale once the pool has been topped up since it was sent:
        the balance the teacher was told about no longer exists, so the next
        dip is news and deserves its own warning. That is why the test is
        "alert newer than the newest grant" rather than "an alert exists" —
        the latter would warn exactly once, ever, and a class that burns
        through two budgets would only ever hear about the first.

        Matched on the payload's own `spaceId`, not on a message string: the
        copy is free to change between releases without either re-alerting or
        silently stopping.
        """
        from sqlalchemy import func

        from app.domain.notification.models import Notification

        newest_alert = await self._session.scalar(
            select(func.max(Notification.created_at)).where(
                Notification.receiver_id.in_(recipients),
                Notification.type == NotificationType.SPACE_COMPUTE_POOL_LOW,
                Notification.deleted_at.is_(None),
                Notification.metadata_payload["spaceId"].astext == str(space_id),
            )
        )
        if newest_alert is None:
            return False
        if not grants:
            return True
        newest_grant = max(g.created_at for g in grants)
        return newest_alert >= newest_grant
