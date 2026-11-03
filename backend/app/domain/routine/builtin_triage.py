"""The feedback triage rule the platform ships with, and whether it is working.

Feedback intake is on for everyone the moment the platform runs; working the
queue used to be a rule somebody had to create by hand, through an agent tool
or the API. Nobody did, and nothing said so: reports sat at 已收录 for days and
every page read healthy. So triage ships with the product too. One rule, with a
fixed id (`BUILTIN_ID`), is installed at startup in the room, for the owner and
with the teammate the deployment names (`FEEDBACK_TRIAGE_TOPIC_ID`,
`FEEDBACK_TRIAGE_OWNER_HANDLE`, `FEEDBACK_TRIAGE_AGENT_HANDLE`). Its title, goal,
schedule and batch size are the product's and are written back on every
start; the room, owner and teammate are the deployment's and follow its
settings. Its on/off switch is the people's: a rule somebody paused stays
paused across every redeploy, and the health report says it is paused.

`health()` is the `feedback_triage` entry of the health report
(`app.core.health`): on a deployment, a missing or invalid setting, a missing
or paused rule, or a last run that failed is `down` with the reason, so the
gap shows on the admin overview by name instead of as silence.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.agent_instance.adapters import project_team
from app.domain.delivery.agent import instance_for_seat
from app.domain.feedback import claims as feedback_claims
from app.domain.routine import schedule
from app.domain.routine.models import (
    Routine,
    RoutineRun,
    RoutineState,
    RoutineTrigger,
    RunStatus,
)
from app.domain.routine.service._shared import now
from app.domain.topic.models import Topic, TopicStatus
from app.domain.topic_membership.services import TopicMemberService

logger = logging.getLogger(__name__)

#: The built-in rule's identity, the same on every deployment and every start:
#: installing it twice finds the row the first install wrote.
BUILTIN_ID = uuid.uuid5(uuid.NAMESPACE_URL, "cheese:routine:feedback-triage")

TITLE = "反馈分诊"
INSTRUCTIONS = (
    "把反馈中心里还没人处理的反馈分诊一遍：逐条读、领取、调查，"
    "能修的问规则的主人要不要建任务，其余的写成建议交给他决定。"
)
#: Once a day, a working day's worth of reports: a run is one turn of one
#: teammate (`feedback.triage.MAX_BATCH` is the ceiling).
SPEC = {"freq": "daily", "time": "10:00", "feedback_batch": 5}
TIMEZONE = "Asia/Shanghai"
#: Who `created_by` names: nobody pressed a button, the platform installed it.
INSTALLED_BY = "system"

_SETTINGS = (
    ("FEEDBACK_TRIAGE_TOPIC_ID", "feedback_triage_topic_id"),
    ("FEEDBACK_TRIAGE_OWNER_HANDLE", "feedback_triage_owner_handle"),
    ("FEEDBACK_TRIAGE_AGENT_HANDLE", "feedback_triage_agent_handle"),
)


def _configured() -> dict[str, str]:
    return {env: getattr(settings, attr).strip() for env, attr in _SETTINGS}


async def _binding(session: AsyncSession) -> tuple[Topic | None, str | None]:
    """The room the deployment names, or why it cannot run the rule there."""
    named = _configured()
    missing = [env for env, value in named.items() if not value]
    if missing:
        return None, "未设置 " + "、".join(missing)
    try:
        topic_id = uuid.UUID(named["FEEDBACK_TRIAGE_TOPIC_ID"])
    except ValueError:
        return None, "FEEDBACK_TRIAGE_TOPIC_ID 不是房间 id"
    topic = await session.get(Topic, topic_id)
    if topic is None:
        return None, "FEEDBACK_TRIAGE_TOPIC_ID 指向的房间不存在"
    if topic.status == TopicStatus.archived:
        return topic, "FEEDBACK_TRIAGE_TOPIC_ID 指向的房间已归档"
    if not await feedback_claims.is_platform_project(session, topic.project_id):
        return topic, (
            "FEEDBACK_TRIAGE_TOPIC_ID 指向的房间不在做平台本身的项目里"
            "（DOCS_DEV_REPOSITORIES），那里的队友领不了反馈"
        )
    members = TopicMemberService(session)
    agent = named["FEEDBACK_TRIAGE_AGENT_HANDLE"]
    if agent not in await members.agent_handles(topic.id):
        return topic, f"FEEDBACK_TRIAGE_AGENT_HANDLE（{agent}）不是这个房间里的 AI 队友"
    instance = await instance_for_seat(session, topic.project_id, agent)
    if instance is None or not instance.is_active:
        return topic, f"FEEDBACK_TRIAGE_AGENT_HANDLE（{agent}）已停用"
    owner = named["FEEDBACK_TRIAGE_OWNER_HANDLE"]
    if owner not in settings.feedback_triage_handles:
        return topic, (
            f"FEEDBACK_TRIAGE_OWNER_HANDLE（{owner}）不在 FEEDBACK_TRIAGE_HANDLES 里："
            "分诊的建议要交给能改反馈状态的人"
        )
    if owner not in await members.people_in(topic, team=project_team):
        return topic, f"FEEDBACK_TRIAGE_OWNER_HANDLE（{owner}）不在这个房间里"
    return topic, None


async def install(session: AsyncSession) -> Routine | None:
    """Create the built-in rule, or bring it in line with this release and
    these settings. Nothing when the settings do not name a valid binding —
    `health()` says why.

    Idempotent: the fixed id makes a second install, or two processes
    installing at once during a rollout, find one row. `state` is never
    written on an existing row, so a pause survives every redeploy.
    """
    topic, problem = await _binding(session)
    if problem is not None:
        logger.warning("built-in feedback triage rule not installed: %s", problem)
        return None
    assert topic is not None
    named = _configured()
    wanted = {
        "project_id": topic.project_id,
        "topic_id": topic.id,
        "title": TITLE,
        "instructions": INSTRUCTIONS,
        "trigger": RoutineTrigger.schedule.value,
        "spec": SPEC,
        "timezone": TIMEZONE,
        "owner_handle": named["FEEDBACK_TRIAGE_OWNER_HANDLE"],
        "agent_handle": named["FEEDBACK_TRIAGE_AGENT_HANDLE"],
    }
    stamp = now()
    await session.execute(
        insert(Routine)
        .values(
            id=BUILTIN_ID,
            note="",
            state=RoutineState.active.value,
            created_by=INSTALLED_BY,
            next_run_at=schedule.next_after(SPEC, TIMEZONE, stamp),
            event_cursor=stamp,
            revision=1,
            **wanted,
        )
        .on_conflict_do_nothing(index_elements=[Routine.id])
    )
    row = await session.get(Routine, BUILTIN_ID, populate_existing=True)
    assert row is not None
    changed = {k: v for k, v in wanted.items() if getattr(row, k) != v}
    if changed:
        for key, value in changed.items():
            setattr(row, key, value)
        row.revision += 1
        if row.state == RoutineState.active.value:
            row.next_run_at = schedule.next_after(SPEC, TIMEZONE, stamp)
    await session.flush()
    return row


async def health(sessions: Callable[[], Any]) -> dict[str, Any]:
    """The `feedback_triage` entry of the health report.

    Off a deployment (a developer's machine, the test suite) with nothing
    configured it is `skipped`; everywhere else every gap is `down`, with the
    reason a person needs to close it.
    """
    if not settings.deployed_via_compose and not any(_configured().values()):
        return {"status": "skipped"}
    async with sessions() as session:
        _topic, problem = await _binding(session)
        if problem is not None:
            return {"status": "down", "error": problem}
        row = await session.get(Routine, BUILTIN_ID)
        if row is None:
            return {
                "status": "down",
                "error": "内置分诊规则不存在：启动时没装上，看后端日志",
            }
        if row.state != RoutineState.active.value:
            return {
                "status": "down",
                "error": "内置分诊规则已暂停：有人在规则页暂停了它，恢复后照常执行",
            }
        last = await session.scalar(
            select(RoutineRun)
            .where(
                RoutineRun.routine_id == BUILTIN_ID,
                RoutineRun.status.not_in(
                    (RunStatus.queued.value, RunStatus.running.value)
                ),
            )
            .order_by(RoutineRun.created_at.desc())
            .limit(1)
        )
        if last is not None and last.status == RunStatus.failed.value:
            return {
                "status": "down",
                "error": f"最近一次执行（{last.created_at:%Y-%m-%d %H:%M} UTC）失败："
                f"{last.error or '没有原因'}",
            }
        return {"status": "up"}
