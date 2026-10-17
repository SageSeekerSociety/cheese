"""Analytics computation for the participant reports: pure functions.

The participant endpoints (``/analytics/participants`` and its CSV export)
all work the same way: drop the memberships the caller filtered out, then
measure counts, distributions and trends over what is left. That cluster
lives here, next to the overview/publisher aggregations in
``analytics_compute``, so both stay well under the file-size cap while the
view service keeps only session loading and orchestration.

Nothing here opens a session or reads a request, so a hand-built
``_Context`` is enough to exercise it.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from app.domain.space.analytics_compute import (
    APPROVED_MAP,
    APPROVED_REVERSE_MAP,
    SUCCESS_STATUS,
    _Context,
    bucketize,
    build_distribution,
    decode_identity,
)
from app.domain.task.models import TaskMembership


def filter_participant_memberships(
    memberships: list[TaskMembership],
    *,
    ctx: _Context,
    participation_approved: int | None,
    completion_status: str | None,
    real_name: str,
) -> list[TaskMembership]:
    result: list[TaskMembership] = []
    for m in memberships:
        if participation_approved is not None and m.approved != participation_approved:
            continue
        if completion_status is not None and m.completion_status != completion_status:
            continue
        if real_name != "all":
            has_rn = not m.is_team and m.member_id in ctx.identities_by_user_id
            if real_name == "with" and not has_rn:
                continue
            if real_name == "without" and has_rn:
                continue
        result.append(m)
    return result


def compute_participant_entity_metrics(
    memberships: list[TaskMembership],
    ctx: _Context,
) -> dict:
    participant_count = len(memberships)
    approved = sum(1 for m in memberships if m.approved == APPROVED_MAP["APPROVED"])
    pending = sum(1 for m in memberships if m.approved == APPROVED_MAP["NONE"])
    disapproved = sum(
        1 for m in memberships if m.approved == APPROVED_MAP["DISAPPROVED"]
    )
    submitted = sum(
        1 for m in memberships if ctx.submissions_by_membership_id.get(m.id)
    )
    successful = sum(1 for m in memberships if m.completion_status == SUCCESS_STATUS)
    return {
        "participantCount": participant_count,
        "approvedParticipantCount": approved,
        "pendingParticipantCount": pending,
        "disapprovedParticipantCount": disapproved,
        "submittedParticipantCount": submitted,
        "successfulParticipantCount": successful,
    }


def compute_participant_student_metrics(
    memberships: list[TaskMembership],
    ctx: _Context,
) -> dict:
    personal_member_ids = {m.member_id for m in memberships if not m.is_team}
    student_count = len(personal_member_ids)
    with_real_name = sum(
        1 for uid in personal_member_ids if uid in ctx.identities_by_user_id
    )
    return {
        "studentCount": student_count,
        "studentsWithRealNameCount": with_real_name,
    }


def compute_participant_distributions(
    memberships: list[TaskMembership],
    ctx: _Context,
) -> dict:
    approval_counter: Counter[str] = Counter(
        APPROVED_REVERSE_MAP.get(m.approved, "NONE") for m in memberships
    )
    completion_counter: Counter[str] = Counter(m.completion_status for m in memberships)

    grade_counter: Counter[str] = Counter()
    major_counter: Counter[str] = Counter()
    class_counter: Counter[str] = Counter()
    with_rn = 0
    without_rn = 0
    for m in memberships:
        if m.is_team:
            # PY team-member real-name linkage not yet modeled; treat as without.
            without_rn += 1
            continue
        identity = ctx.identities_by_user_id.get(m.member_id)
        if identity is None:
            without_rn += 1
            continue
        with_rn += 1
        decoded = decode_identity(identity)
        if decoded["grade"]:
            grade_counter[decoded["grade"]] += 1
        if decoded["major"]:
            major_counter[decoded["major"]] += 1
        if decoded["className"]:
            class_counter[decoded["className"]] += 1
    real_name_counter: Counter[str] = Counter(
        {"WITH_REAL_NAME": with_rn, "WITHOUT_REAL_NAME": without_rn}
    )

    return {
        "byApprovalStatus": build_distribution(
            "Participant Approval Status", approval_counter
        ),
        "byCompletionStatus": build_distribution(
            "Participant Completion Status", completion_counter
        ),
        "byGrade": build_distribution("Participant Grades", grade_counter),
        "byMajor": build_distribution("Participant Majors", major_counter),
        "byClassName": build_distribution("Participant Classes", class_counter),
        "byRealNameStatus": build_distribution(
            "Participant Real Name Status", real_name_counter
        ),
    }


def compute_participant_trends(
    memberships: list[TaskMembership],
    *,
    ctx: _Context,
    from_dt: datetime,
    to_dt: datetime,
    group_by: str,
) -> dict:
    membership_ids = {m.id for m in memberships}
    submissions = [s for s in ctx.submissions if s.membership_id in membership_ids]
    participants_joined = bucketize(
        (m.created_at for m in memberships),
        from_dt=from_dt,
        to_dt=to_dt,
        group_by=group_by,
    )
    submissions_created = bucketize(
        (s.created_at for s in submissions),
        from_dt=from_dt,
        to_dt=to_dt,
        group_by=group_by,
    )
    successes_achieved = bucketize(
        (m.updated_at for m in memberships if m.completion_status == SUCCESS_STATUS),
        from_dt=from_dt,
        to_dt=to_dt,
        group_by=group_by,
    )
    return {
        "participantsJoined": participants_joined,
        "submissionsCreated": submissions_created,
        "successesAchieved": successes_achieved,
    }
