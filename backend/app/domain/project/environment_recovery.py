"""One overview-assisted repair per failed room, recorded on its timeline."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.domain.agent.models import AgentTurn
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.project.models import Project
from app.domain.topic.models import Topic


async def latest_recovery(db, topic_id):
    return await db.scalar(
        select(Block)
        .where(
            Block.topic_id == topic_id,
            Block.meta["event_type"].as_string() == "environment_recovery",
        )
        .order_by(Block.created_at.desc())
        .limit(1)
    )


async def report_failure(
    chat, project_id: uuid.UUID, topic_id: uuid.UUID, status: dict
):
    from app.api.deps import get_work_runner

    async with chat.session_factory() as db:
        topic = await db.scalar(
            select(Topic).where(Topic.id == topic_id).with_for_update()
        )
        project = await db.get(Project, project_id)
        if topic is None or project is None or topic.is_private:
            return
        previous = await latest_recovery(db, topic_id)
        if previous is not None and (previous.meta or {}).get("state") != "closed":
            if (previous.meta or {}).get("attempt") != status.get("attempt"):
                previous.meta = {**previous.meta, "state": "needs_help"}
            await db.commit()
            return
        root = project.root_topic_id
        available = root is not None and root != topic_id
        dispatch_turn = uuid.uuid4()
        event = await BlockRepository(db).add(
            project_id=project_id,
            topic_id=topic_id,
            author="system",
            author_type=AuthorType.system,
            kind=BlockKind.event,
            content=(
                "环境准备失败，已交给总览芝士检查。"
                if available
                else "环境准备失败，总览芝士暂不可用，请查看安装日志。"
            ),
            meta={
                "event_type": "environment_recovery",
                "state": "requested" if available else "needs_help",
                "attempt": status.get("attempt"),
                "revision": status.get("revision"),
                "stage": status.get("stage"),
                "exit_code": status.get("exit_code"),
                "dispatch_turn": str(dispatch_turn),
                "dispatched_at": datetime.now(UTC).isoformat(),
            },
        )
        await db.commit()
        if not available or root is None:
            return
        # Logs stay in the affected room; overview fetches them through scoped APIs.
        path = f"/projects/{project_id}/environment/recovery/rooms/{topic_id}"
        await chat.post_system_event(
            root,
            "房间环境准备失败，已交给总览芝士检查",
            meta={
                "event_type": "environment_recovery_request",
                "room_id": str(topic_id),
            },
        )
        get_work_runner().submit_kickoff(
            chat,
            root,
            turn_id=dispatch_turn,
            prompt=(
                f"房间 {topic_id} 的环境准备失败，任务消息尚未送达。"
                f"修复记录：{event.id}。使用 cheese api GET {path} 读取失败步骤、"
                "日志和房间配置。诊断原因后，如能修复，使用 cheese api POST "
                f"{path}，提交 JSON：incident_id、expected_revision、config"
                "（setup_script、startup_script、variables）。接口只修改这个房间，"
                "并实际启动芝士继续待处理消息。每条记录仅允许一次自动重启。"
                "不要通过忽略失败或删除必要安装步骤绕过问题；不要在回复中复述密钥。"
                "如果需要凭据、机器权限或无法确定修复方式，请向同一接口提交"
                "incident_id、expected_revision、config=null 和 reason，"
                "记录需要的协助。"
                "总览使用基础工具环境，不执行项目脚本。"
            ),
        )


async def close_recovery(db, topic_id):
    await db.scalar(select(Topic.id).where(Topic.id == topic_id).with_for_update())
    event = await latest_recovery(db, topic_id)
    if event is not None:
        event.meta = {**event.meta, "state": "closed"}


async def reconcile_recovery(db, topic_id):
    from app.api.deps import get_work_runner

    # Serialize status changes with apply/repair so a read cannot overwrite
    # a repair that started while it was checking the previous dispatch.
    await db.scalar(select(Topic.id).where(Topic.id == topic_id).with_for_update())
    event = await latest_recovery(db, topic_id)
    if event is None or (event.meta or {}).get("state") not in (
        "requested",
        "retrying",
    ):
        return event
    meta = event.meta or {}
    turn_id = uuid.UUID(meta["dispatch_turn"])
    if get_work_runner().kickoff_pending(turn_id):
        return event
    turn = await db.get(AgentTurn, turn_id)
    if turn is not None and turn.stopped_at is None:
        return event
    # The incident commits before dispatch. Allow the immediate handoff window;
    # a lost process or refused/completed turn then becomes a visible dead end.
    if datetime.now(UTC) - datetime.fromisoformat(meta["dispatched_at"]) < timedelta(
        seconds=10
    ):
        return event
    event.meta = {
        **meta,
        "state": "needs_help",
        "reason": "总览未能完成自动处理，请查看总览中的说明",
    }
    await db.flush()
    return event
