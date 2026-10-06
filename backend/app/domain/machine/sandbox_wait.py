"""What a tool call waiting on its session's cloud sandbox, or its whole cloud
VM, watches, and what it is told meanwhile (``session_work._attempt``)."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.domain.machine.models import (
    GONE,
    MAX_ENROLL_ATTEMPTS,
    CloudHost,
    CloudHostHome,
    MachineStatus,
)

# What a tool waiting on its cloud sandbox is told.
SANDBOX_PREPARING = "沙箱正在准备；对话和平台工具仍可用。"
SANDBOX_WAKING = "沙箱正在唤醒；对话和平台工具仍可用。"
SANDBOX_RESTORE_FAILED = "沙箱没能从归档恢复，稍后会再试；对话和平台工具仍可用。"
VM_PREPARING = "云虚拟机正在准备；对话和平台工具仍可用。"
VM_ERROR = "云虚拟机出错：供应方报告错误。对话和平台工具仍可用。"
# What a session whose sandbox's host the pool gave up on is told, once, with
# the first tool call that succeeds in its new sandbox (``HostPool._fail``
# records it under ``LOST_KEY`` in the session's ``execution_request``).
SANDBOX_LOST = (
    "原来的沙箱所在机器失联，已换成一个新沙箱：工作区从 git 重新取出，"
    "上次推送之后没推送的改动不在了。"
)
LOST_KEY = "sandbox_lost"


async def _home_settled(db, session_id) -> bool:
    """Whatever was moving the session's home has finished, or given up."""
    busy = await db.scalar(
        select(CloudHostHome.busy_until).where(
            CloudHostHome.session_id == session_id, CloudHostHome.left_at.is_(None)
        )
    )
    return busy is None or busy <= datetime.now(UTC)


async def _home_moved(db, session_id) -> bool:
    """The sleeping home has left the host that had no slot for it, or was
    woken there after all."""
    home = (
        await db.execute(
            select(CloudHostHome.host_id, CloudHostHome.stopped_at).where(
                CloudHostHome.session_id == session_id, CloudHostHome.left_at.is_(None)
            )
        )
    ).one_or_none()
    return home is None or home.host_id is None or home.stopped_at is None


async def _cloud_progress(db, hub, host_id) -> str | bool:
    """Where the host of the session's sandbox is on its way to being usable."""
    host = (
        await db.execute(
            select(
                CloudHost.status,
                CloudHost.device_id,
                CloudHost.enroll_attempts,
                CloudHost.released_at,
                CloudHost.whole_machine,
            ).where(CloudHost.id == host_id)
        )
    ).one_or_none()
    if host is None or host.released_at:
        # The pool let go of it; the next attempt places the session again.
        return True
    if host.device_id is None:
        if host.status == MachineStatus.error or (
            (host.enroll_attempts or 0) >= MAX_ENROLL_ATTEMPTS
        ):
            # Failed while being built: the next attempt places the session on
            # another host.
            return True
        return False
    if host.status == MachineStatus.error:
        if host.whole_machine:
            return VM_ERROR
        # The pool sweep gives it up and the next attempt places the session
        # in a new sandbox (``HostPool._lost``).
        return False
    if host.status in GONE:
        # Gone upstream: the next attempt forgets it and places the session.
        return True
    return hub.is_online(host.device_id)
