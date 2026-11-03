"""Assembling the payload each analytics endpoint answers with.

The service's public methods are these functions: one reads the request
parameters, loads the context, hands it to the computation, and shapes the
result the route returns. Each takes the service and reaches everything else
through it — ``service._load_context``, ``service._compute_entity_metrics`` and
the rest — so the patch points and the class's own methods stay what they were.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.space.analytics_compute import (
    participants_csv_text,
    publishers_csv_text,
    tasks_csv_text,
)
from app.domain.task.models import TaskMembership


async def get_overview(
    service,
    *,
    space_id: int,
    from_ts: int | None,
    to_ts: int | None,
    category_id: int | None,
    publisher_id: int | None,
    task_approved: str | None,
    group_by: str,
) -> dict:
    group_by_norm = service._normalize_group_by(group_by)
    from_dt, to_dt = service._resolve_window(from_ts, to_ts)
    approved_value = service._parse_approved(task_approved)

    await service._ensure_space_exists(space_id)
    ctx = await service._load_context(
        space_id=space_id,
        from_dt=from_dt,
        to_dt=to_dt,
        category_id=category_id,
        publisher_id=publisher_id,
        approved_value=approved_value,
    )

    entity_metrics = service._compute_entity_metrics(ctx)
    student_metrics = service._compute_overview_student_metrics(ctx)
    task_distributions = service._compute_task_distributions(ctx)
    trends = service._compute_overview_trends(
        ctx, from_dt=from_dt, to_dt=to_dt, group_by=group_by_norm
    )

    return {
        "summary": {
            "spaceId": space_id,
            "from": service._to_timestamp_ms(from_dt),
            "to": service._to_timestamp_ms(to_dt),
        },
        "entityMetrics": entity_metrics,
        "studentMetrics": student_metrics,
        "taskDistributions": task_distributions,
        "trends": trends,
    }


async def get_alerts(service, *, space_id: int) -> dict:
    await service._ensure_space_exists(space_id)
    # Alerts are evaluated against the *current* state of the space,
    # without any time window filtering (frontend passes no params).
    ctx = await service._load_context(
        space_id=space_id,
        from_dt=None,
        to_dt=None,
        category_id=None,
        publisher_id=None,
        approved_value=None,
    )

    now = datetime.now(UTC)

    pending_task_approval = service._count_pending_task_approvals(ctx)
    pending_participant_approval = service._count_pending_participant_approvals(ctx)
    pending_submission_review = service._count_pending_submission_reviews(ctx)

    stalled_task = service._count_stalled_tasks(ctx, now=now)
    overdue_unreviewed = service._count_overdue_unreviewed_submissions(ctx, now=now)
    inactive_publisher = service._count_inactive_publishers(ctx, now=now)

    return {
        "pendingTaskApprovalCount": pending_task_approval,
        "pendingParticipantApprovalCount": pending_participant_approval,
        "pendingSubmissionReviewCount": pending_submission_review,
        "stalledTaskCount": stalled_task,
        "overdueUnreviewedSubmissionCount": overdue_unreviewed,
        "inactivePublisherCount": inactive_publisher,
    }


async def get_publishers(
    service,
    *,
    space_id: int,
    from_ts: int | None,
    to_ts: int | None,
    category_id: int | None,
    task_approved: str | None,
    sort_by: str,
    sort_order: str,
) -> dict:
    from_dt, to_dt = service._resolve_window(from_ts, to_ts)
    approved_value = service._parse_approved(task_approved)
    sort_by_norm = service._normalize_publisher_sort_by(sort_by)
    sort_order_norm = service._normalize_sort_order(sort_order)

    await service._ensure_space_exists(space_id)
    ctx = await service._load_context(
        space_id=space_id,
        from_dt=from_dt,
        to_dt=to_dt,
        category_id=category_id,
        publisher_id=None,
        approved_value=approved_value,
    )

    rows = service._compute_publishers_rows(ctx)
    rows.sort(
        key=lambda row: row.get(sort_by_norm) or 0,
        reverse=(sort_order_norm == "desc"),
    )
    return {"publishers": rows}


async def get_tasks(
    service,
    *,
    space_id: int,
    from_ts: int | None,
    to_ts: int | None,
    category_id: int | None,
    publisher_id: int | None,
    task_approved: str | None,
    has_pending_review: bool | None,
    has_pending_approval: bool | None,
    sort_by: str,
    sort_order: str,
) -> dict:
    from_dt, to_dt = service._resolve_window(from_ts, to_ts)
    approved_value = service._parse_approved(task_approved)
    sort_by_norm = service._normalize_task_sort_by(sort_by)
    sort_order_norm = service._normalize_sort_order(sort_order)

    await service._ensure_space_exists(space_id)
    ctx = await service._load_context(
        space_id=space_id,
        from_dt=from_dt,
        to_dt=to_dt,
        category_id=category_id,
        publisher_id=publisher_id,
        approved_value=approved_value,
    )

    rows = [service._compute_task_row(task, ctx) for task in ctx.tasks]

    # Post-filter by hasPendingReview / hasPendingApproval
    if has_pending_review is True:
        rows = [r for r in rows if r["pendingReviewCount"] > 0]
    elif has_pending_review is False:
        rows = [r for r in rows if r["pendingReviewCount"] == 0]
    if has_pending_approval is True:
        rows = [r for r in rows if r["pendingParticipantApprovalCount"] > 0]
    elif has_pending_approval is False:
        rows = [r for r in rows if r["pendingParticipantApprovalCount"] == 0]

    rows.sort(
        key=lambda row: row.get(sort_by_norm) or 0,
        reverse=(sort_order_norm == "desc"),
    )
    return {"tasks": rows}


async def export_tasks_csv(
    service,
    *,
    space_id: int,
    from_ts: int | None,
    to_ts: int | None,
    category_id: int | None,
    publisher_id: int | None,
    task_approved: str | None,
    has_pending_review: bool | None,
    has_pending_approval: bool | None,
) -> str:
    data = await service.get_tasks(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=category_id,
        publisher_id=publisher_id,
        task_approved=task_approved,
        has_pending_review=has_pending_review,
        has_pending_approval=has_pending_approval,
        sort_by="createdAt",
        sort_order="desc",
    )
    return tasks_csv_text(data["tasks"])


async def export_publishers_csv(
    service,
    *,
    space_id: int,
    from_ts: int | None,
    to_ts: int | None,
    category_id: int | None,
    task_approved: str | None,
) -> str:
    data = await service.get_publishers(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=category_id,
        task_approved=task_approved,
        sort_by="taskCount",
        sort_order="desc",
    )
    return publishers_csv_text(data["publishers"])


async def get_participants(
    service,
    *,
    space_id: int,
    from_ts: int | None,
    to_ts: int | None,
    category_id: int | None,
    publisher_id: int | None,
    task_approved: str | None,
    participation_approved: str | None,
    completion_status: str | None,
    real_name: str,
    group_by: str,
) -> dict:
    group_by_norm = service._normalize_group_by(group_by)
    from_dt, to_dt = service._resolve_window(from_ts, to_ts)
    approved_value = service._parse_approved(task_approved)
    participation_approved_value = service._parse_approved_optional(
        participation_approved
    )
    completion_status_norm = service._normalize_completion_status(completion_status)
    real_name_norm = service._normalize_real_name_filter(real_name)

    await service._ensure_space_exists(space_id)
    ctx = await service._load_context(
        space_id=space_id,
        from_dt=from_dt,
        to_dt=to_dt,
        category_id=category_id,
        publisher_id=publisher_id,
        approved_value=approved_value,
        include_participant_details=True,
    )

    memberships = service._filter_participant_memberships(
        ctx.memberships,
        ctx=ctx,
        participation_approved=participation_approved_value,
        completion_status=completion_status_norm,
        real_name=real_name_norm,
    )

    entity_metrics = service._compute_participant_entity_metrics(memberships, ctx)
    student_metrics = service._compute_participant_student_metrics(memberships, ctx)
    distributions = service._compute_participant_distributions(memberships, ctx)
    trends = service._compute_participant_trends(
        memberships,
        ctx=ctx,
        from_dt=from_dt,
        to_dt=to_dt,
        group_by=group_by_norm,
    )

    return {
        "summary": {
            "spaceId": space_id,
            "from": service._to_timestamp_ms(from_dt),
            "to": service._to_timestamp_ms(to_dt),
        },
        "entityMetrics": entity_metrics,
        "studentMetrics": student_metrics,
        "distributions": distributions,
        "trends": trends,
    }


async def get_people(service, *, space_id: int) -> dict:
    """逐人一行，外加「开始做了没动」的逐条名单 —— 都只看这块板上未删除的题。

    两处口径，页面上也照说一遍：

    - **一条领取走到哪一步**：没有 live 提交＝在做；每个阶段都通过
      （``task_membership.completion_status`` 为 ``SUCCESS``，由
      ``submission_state`` 在每次提交与评审之后推出来）＝通过；有 live 提交但还
      没全部通过＝已交。
    - **开始做了没动**逐条给：开始做（项目建好，``claims.started_at``）早于
      14 天、而且一版 live 提交都没有；还没开始做的不算。14 天与 alerts 那一格
      的 ``STALLED_TASK_DAYS`` 同一个数，不是另定一个。

    团队报名按**队**算一行（``is_team`` 那半就是队号），和它在领取表里的
    粒度一致：一条领取是队领的，就没有「队里哪个人领的」这回事。

    两条查询各自聚合，都在 SQL 里数完 —— 逐人那一格不做 N+1（人数 × 领取
    数会跟着板的大小一起长）。
    """
    await service._ensure_space_exists(space_id)
    now = datetime.now(UTC)
    rows = await service._load_people_rows(space_id=space_id)
    stalled_rows = await service._load_stalled_claims(space_id=space_id, now=now)

    names = await service._load_member_names(
        [(bool(r.is_team), int(r.member_id)) for r in rows]
        + [(bool(r.is_team), int(r.member_id)) for r in stalled_rows]
    )

    people = [
        {
            "userId": int(row.member_id),
            "name": names[(bool(row.is_team), int(row.member_id))],
            "isTeam": bool(row.is_team),
            "claims": int(row.claims),
            "passed": int(row.passed),
            "submitted": int(row.submitted),
            # 在做 = 交了还没判的 + 一版都没交的。两条判据都在「有没有 live
            # 提交」这一层，所以它是减出来的，不需要第三条 SQL。
            "inProgress": int(row.claims) - int(row.passed) - int(row.submitted),
        }
        for row in rows
    ]
    stalled = [
        {
            "taskId": int(row.task_id),
            "taskTitle": row.task_name,
            "userId": int(row.member_id),
            "name": names[(bool(row.is_team), int(row.member_id))],
            "isTeam": bool(row.is_team),
            "startedAt": service._to_timestamp_ms(row.started_at),
        }
        for row in stalled_rows
    ]
    return {"people": people, "stalled": stalled}


async def export_participants_csv(
    service,
    *,
    space_id: int,
    from_ts: int | None,
    to_ts: int | None,
    category_id: int | None,
    publisher_id: int | None,
    task_approved: str | None,
    participation_approved: str | None,
    completion_status: str | None,
    real_name: str,
) -> tuple[str, list[TaskMembership]]:
    """Return (csv_text, memberships) so the route can write audit logs."""
    from_dt, to_dt = service._resolve_window(from_ts, to_ts)
    approved_value = service._parse_approved(task_approved)
    participation_approved_value = service._parse_approved_optional(
        participation_approved
    )
    completion_status_norm = service._normalize_completion_status(completion_status)
    real_name_norm = service._normalize_real_name_filter(real_name)

    await service._ensure_space_exists(space_id)
    ctx = await service._load_context(
        space_id=space_id,
        from_dt=from_dt,
        to_dt=to_dt,
        category_id=category_id,
        publisher_id=publisher_id,
        approved_value=approved_value,
        include_participant_details=True,
    )

    memberships = service._filter_participant_memberships(
        ctx.memberships,
        ctx=ctx,
        participation_approved=participation_approved_value,
        completion_status=completion_status_norm,
        real_name=real_name_norm,
    )

    return participants_csv_text(memberships, ctx), memberships
