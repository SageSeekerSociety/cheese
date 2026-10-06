"""Accept-card / Review business logic — the 验收 state machine.

Spec §4.4 (AI 不能验收自己做的东西), §6.3 (采纳即归档/merge, 且可撤销).
This is deterministic platform code, not AI.
"""

from __future__ import annotations

# 兼容门面：`app.domain.review.services` 仍是既有调用点与测试的导入路径。
# 每个公开名与被 monkeypatch 的名字都逐个再导出（`X as X`），类对象与
# 方法集合一格没动。
from app.core.db import async_session_factory as async_session_factory
from app.domain.review.nudges import _NUDGE_TAIL_LIMIT as _NUDGE_TAIL_LIMIT
from app.domain.review.nudges import (
    _ci_nudge as _ci_nudge,
)
from app.domain.review.nudges import (
    _conflict_nudge as _conflict_nudge,
)
from app.domain.review.nudges import (
    _review_nudge as _review_nudge,
)
from app.domain.review.room_notices import (
    _announce_filed as _announce_filed,
)
from app.domain.review.room_notices import (
    _announce_new_artifact as _announce_new_artifact,
)
from app.domain.review.room_notices import (
    _note_needs_human as _note_needs_human,
)
from app.domain.review.room_notices import (
    _note_outside_accept_txn as _note_outside_accept_txn,
)
from app.domain.review.room_notices import (
    _note_poll_failed as _note_poll_failed,
)
from app.domain.review.room_notices import (
    _notify_merge_result as _notify_merge_result,
)
from app.domain.review.room_notices import (
    _notify_ready as _notify_ready,
)
from app.domain.review.room_notices import (
    _record_task_nudge as _record_task_nudge,
)
from app.domain.review.room_notices import (
    _tell_the_reviewer as _tell_the_reviewer,
)
from app.domain.review.services._shared import (
    _ACCEPT_NO_BRANCH_PREFIX as _ACCEPT_NO_BRANCH_PREFIX,
)
from app.domain.review.services._shared import (
    _ACCEPT_PR_OPEN_FAILED_PREFIX as _ACCEPT_PR_OPEN_FAILED_PREFIX,
)
from app.domain.review.services._shared import (
    _ACCEPT_PR_STALLED_PREFIX as _ACCEPT_PR_STALLED_PREFIX,
)
from app.domain.review.services._shared import (
    _ALEMBIC_VERSIONS_DIR as _ALEMBIC_VERSIONS_DIR,
)
from app.domain.review.services._shared import (
    _ARTIFACT_ACTION_BOTH as _ARTIFACT_ACTION_BOTH,
)
from app.domain.review.services._shared import (
    _ARTIFACT_ACTION_MISSING as _ARTIFACT_ACTION_MISSING,
)
from app.domain.review.services._shared import (
    _ARTIFACT_ACTION_UNWANTED as _ARTIFACT_ACTION_UNWANTED,
)
from app.domain.review.services._shared import (
    _BLOCKED_BY_CARD_MESSAGES as _BLOCKED_BY_CARD_MESSAGES,
)
from app.domain.review.services._shared import (
    _CARD_BLOCKS_NEW_CARD as _CARD_BLOCKS_NEW_CARD,
)
from app.domain.review.services._shared import (
    _DELIVERABLE_BOTH as _DELIVERABLE_BOTH,
)
from app.domain.review.services._shared import (
    _DELIVERABLE_MAX_BYTES as _DELIVERABLE_MAX_BYTES,
)
from app.domain.review.services._shared import (
    _FORCE_MERGE_VERDICTS as _FORCE_MERGE_VERDICTS,
)
from app.domain.review.services._shared import (
    _GITHUB_ENFORCES_TTL_S as _GITHUB_ENFORCES_TTL_S,
)
from app.domain.review.services._shared import (
    _MERGE_FAILED_MESSAGE as _MERGE_FAILED_MESSAGE,
)
from app.domain.review.services._shared import (
    _MISSING_SUBJECT as _MISSING_SUBJECT,
)
from app.domain.review.services._shared import (
    _NOT_THIS_ROOMS_WORK as _NOT_THIS_ROOMS_WORK,
)
from app.domain.review.services._shared import (
    _REQUIRED_CHECK_GRACE_MINUTES as _REQUIRED_CHECK_GRACE_MINUTES,
)
from app.domain.review.services._shared import (
    EVENT_ACCEPT_DISMISSED as EVENT_ACCEPT_DISMISSED,
)
from app.domain.review.services._shared import (
    EVENT_ACCEPT_DONE as EVENT_ACCEPT_DONE,
)
from app.domain.review.services._shared import (
    EVENT_ACCEPT_STOPPED as EVENT_ACCEPT_STOPPED,
)
from app.domain.review.services._shared import (
    EVENT_CARD_REDESCRIBED as EVENT_CARD_REDESCRIBED,
)
from app.domain.review.services._shared import (
    EVENT_CARD_VOIDED as EVENT_CARD_VOIDED,
)
from app.domain.review.services._shared import (
    EVENT_FORCE_MERGED as EVENT_FORCE_MERGED,
)
from app.domain.review.services._shared import (
    EVENT_MERGE_REFUSED as EVENT_MERGE_REFUSED,
)
from app.domain.review.services._shared import (
    EVENT_MIGRATION_COLLISION as EVENT_MIGRATION_COLLISION,
)
from app.domain.review.services._shared import (
    EVENT_PR_CLOSED as EVENT_PR_CLOSED,
)
from app.domain.review.services._shared import (
    FORCE_MERGED_PREFIX as FORCE_MERGED_PREFIX,
)
from app.domain.review.services._shared import (
    GATE_ABANDONED_PREFIX as GATE_ABANDONED_PREFIX,
)
from app.domain.review.services._shared import (
    SEVERITY_ERROR as SEVERITY_ERROR,
)
from app.domain.review.services._shared import (
    SEVERITY_INFO as SEVERITY_INFO,
)
from app.domain.review.services._shared import (
    SEVERITY_WARN as SEVERITY_WARN,
)
from app.domain.review.services._shared import (
    VOIDED_PREFIX as VOIDED_PREFIX,
)
from app.domain.review.services._shared import (
    WHO_CHEESE as WHO_CHEESE,
)
from app.domain.review.services._shared import (
    WHO_HUMAN as WHO_HUMAN,
)
from app.domain.review.services._shared import (
    WHO_PLATFORM as WHO_PLATFORM,
)
from app.domain.review.services._shared import (
    ReviewerAdmission as ReviewerAdmission,
)
from app.domain.review.services._shared import (
    _capped as _capped,
)
from app.domain.review.services._shared import (
    _force_merge_verdict as _force_merge_verdict,
)
from app.domain.review.services._shared import (
    _github_enforces as _github_enforces,
)
from app.domain.review.services._shared import (
    _github_enforces_cache as _github_enforces_cache,
)
from app.domain.review.services._shared import (
    _GitHubCredentials as _GitHubCredentials,
)
from app.domain.review.services._shared import (
    _never_shown_message as _never_shown_message,
)
from app.domain.review.services._shared import (
    _no_artifact_action as _no_artifact_action,
)
from app.domain.review.services._shared import (
    _one_artifact_action as _one_artifact_action,
)
from app.domain.review.services._shared import (
    _one_deliverable as _one_deliverable,
)
from app.domain.review.services._shared import (
    _read_deliverable as _read_deliverable,
)
from app.domain.review.services._shared import (
    _stale_view_message as _stale_view_message,
)
from app.domain.review.services._shared import (
    approvals_required_of as approvals_required_of,
)
from app.domain.review.services._shared import (
    logger as logger,
)
from app.domain.review.services._shared import (
    notice as notice,
)
from app.domain.review.services.service import AcceptService as AcceptService
from app.domain.room_task.services import TaskService as TaskService
