"""一行 ``Task`` → 接口要的那个 dict。

从 ``routes/tasks.py`` 挪出来的 —— 那个文件早已越过体积帽（超帽只许缩），而「把任务
行摆成接口形状」本来就不碰路由、不碰会话，独立成篇也说得通。放在 ``app/api`` 而不是
``app/api/routes``：它不是路由，也不该吃「路由不许直接 import 领域的 models」那条约束
（``deps.py`` / ``doc_store.py`` 是同样的先例）。``#944`` 的题目级指导只是这张 dict
里的一格，读法在 ``task_teaching``。
"""

from datetime import datetime

from app.api.task_teaching import read_task_teaching
from app.domain.task.models import Task


def _ms(moment: datetime | None) -> int | None:
    """A moment as epoch milliseconds, the unit the task API speaks; None stays None."""
    return int(moment.timestamp() * 1000) if moment is not None else None


def _task_to_api_model(task: Task) -> dict:
    created_at_ms = _ms(task.created_at) or 0
    updated_at_ms = _ms(task.updated_at) or 0
    deadline_ms = _ms(task.deadline)
    # 审核痕迹是后加的两列：老题（以及还没审过的题）没有它，一律回 null ——
    # 界面上「没有审核人」与「不知道审核人」是同一件事，不做区分。
    reviewed_by = getattr(task, "reviewed_by", None)
    published_at_ms = _ms(getattr(task, "published_at", None))
    ended_at_ms = _ms(getattr(task, "ended_at", None))
    reviewed_at_ms = _ms(getattr(task, "reviewed_at", None))
    registration_start_ms = _ms(task.registration_start_at)
    approved_map = {0: "APPROVED", 1: "DISAPPROVED", 2: "NONE"}
    submitter_type_map = {0: "USER", 1: "TEAM"}
    return {
        "id": task.id,
        "name": task.name,
        "intro": task.intro,
        "description": task.description,
        "deadline": deadline_ms,
        "registrationStartAt": registration_start_ms,
        "defaultDeadline": task.default_deadline,
        "resubmittable": task.resubmittable,
        "editable": task.editable,
        "approved": approved_map.get(task.approved, "NONE"),
        "rank": task.rank,
        "submitterType": submitter_type_map.get(task.submitter_type, "USER"),
        "submissionSchema": [],
        "space": {"id": task.space_id},
        "category": {"id": task.category_id, "name": ""},
        "categoryId": task.category_id,
        "createdBy": task.creator_id,
        "creator": {"id": task.creator_id},
        "requireRealName": task.require_real_name,
        "participantLimit": task.participant_limit,
        "minTeamSize": task.min_team_size,
        "maxTeamSize": task.max_team_size,
        "teamLockingPolicy": task.team_locking_policy,
        "rejectReason": task.reject_reason,
        "videoUrl": task.video_url,
        "accessControlEnabled": task.access_control_enabled,
        # 这道题自己的「给 AI 队友的指导」(#944)：报的是**原始覆盖**，不是解析后的
        # 赢家 —— 发题页编的是这道题自己写了什么，空 = 继承，只有对着原始值才讲得通。
        "teaching": read_task_teaching(task),
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
        "publishedAt": published_at_ms,
        "endedAt": ended_at_ms,
        "reviewedBy": reviewed_by,
        "reviewedAt": reviewed_at_ms,
    }
