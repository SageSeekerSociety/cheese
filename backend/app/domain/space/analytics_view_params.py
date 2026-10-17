"""Analytics view request parameters: what the query string may say.

Every analytics endpoint takes its filters as query strings, and every one of
them is read the same way: normalise the value, refuse one outside the set with
``BadRequestError``, hand back the form the service works in. The sets sit here
with that reading — they are the same fact from the endpoint's side.

Nothing here holds state, opens a session or reads a request.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.errors import BadRequestError
from app.domain.space.analytics_compute import APPROVED_MAP

# --- The values each parameter may take ---------------------------------------

COMPLETION_STATUSES = {
    "NOT_SUBMITTED",
    "PENDING_REVIEW",
    "REJECTED_RESUBMITTABLE",
    "FAILED",
    "SUCCESS",
}

GROUP_BY_VALUES = {"day", "week", "month"}

PUBLISHER_SORT_FIELDS = {
    "taskCount",
    "participantCount",
    "successRate",
    "lastTaskCreatedAt",
}

TASK_SORT_FIELDS = {
    "createdAt",
    "deadline",
    "participantCount",
    "submittedParticipantCount",
    "successfulParticipantCount",
    "submissionConversionRate",
    "successRate",
}

REAL_NAME_FILTERS = {"all", "with", "without"}

# Default analytics window
DEFAULT_WINDOW_DAYS = 180

# --- Parsing ------------------------------------------------------------------


def _resolve_window(
    from_ts: int | None, to_ts: int | None
) -> tuple[datetime, datetime]:
    now_utc = datetime.now(UTC)
    to_dt = (
        datetime.fromtimestamp(to_ts / 1000, tz=UTC) if to_ts is not None else now_utc
    )
    from_dt = (
        datetime.fromtimestamp(from_ts / 1000, tz=UTC)
        if from_ts is not None
        else to_dt - timedelta(days=DEFAULT_WINDOW_DAYS)
    )
    if from_dt > to_dt:
        raise BadRequestError("'from' must be <= 'to'")
    return from_dt, to_dt


def _parse_approved(value: str | None) -> int | None:
    if value is None:
        return None
    key = value.upper()
    if key not in APPROVED_MAP:
        raise BadRequestError(
            f"Invalid taskApproved: {value}. Must be APPROVED, DISAPPROVED, or NONE"
        )
    return APPROVED_MAP[key]


def _parse_approved_optional(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    key = value.upper()
    if key not in APPROVED_MAP:
        raise BadRequestError(
            f"Invalid participationApproved: {value}. Must be APPROVED, DISAPPROVED, or NONE"  # noqa: E501
        )
    return APPROVED_MAP[key]


def _normalize_completion_status(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    key = value.upper()
    if key not in COMPLETION_STATUSES:
        raise BadRequestError(
            f"Invalid completionStatus: {value}. Must be one of {sorted(COMPLETION_STATUSES)}"  # noqa: E501
        )
    return key


def _normalize_real_name_filter(value: str) -> str:
    key = (value or "all").lower()
    if key not in REAL_NAME_FILTERS:
        raise BadRequestError(
            f"Invalid realName: {value}. Must be one of {sorted(REAL_NAME_FILTERS)}"
        )
    return key


def _normalize_group_by(value: str) -> str:
    key = (value or "day").lower()
    if key not in GROUP_BY_VALUES:
        raise BadRequestError(f"Invalid groupBy: {value}. Must be day, week, or month")
    return key


def _normalize_publisher_sort_by(value: str) -> str:
    key = value or "taskCount"
    if key not in PUBLISHER_SORT_FIELDS:
        raise BadRequestError(
            f"Invalid sortBy: {value}. Must be one of {sorted(PUBLISHER_SORT_FIELDS)}"  # noqa: E501
        )
    return key


def _normalize_task_sort_by(value: str) -> str:
    key = value or "createdAt"
    if key not in TASK_SORT_FIELDS:
        raise BadRequestError(
            f"Invalid sortBy: {value}. Must be one of {sorted(TASK_SORT_FIELDS)}"
        )
    return key


def _normalize_sort_order(value: str) -> str:
    key = (value or "desc").lower()
    if key not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {value}. Must be asc or desc")
    return key
