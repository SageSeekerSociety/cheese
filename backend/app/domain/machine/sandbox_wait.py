"""What a tool call waiting on its session's cloud sandbox, or its whole cloud
VM, watches, and what it is told meanwhile (``session_work._attempt``)."""

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
EXECUTOR_SETUP_FAILED = "工作电脑上的执行器没能装好：{reason}；对话和平台工具仍可用。"
VM_PREPARING = "云虚拟机正在准备；对话和平台工具仍可用。"
VM_ERROR = "云虚拟机出错：供应方报告错误。对话和平台工具仍可用。"
# What a session is told, once, with the first tool call that succeeds in a
# new sandbox: its old one was destroyed when idle (``lifecycle``) or given up
# with its host (``HostPool._fail``). Either records it under ``LOST_KEY`` in
# the session's ``execution_request``.
SANDBOX_LOST = (
    "沙箱环境已换成新的。每轮结束时的检查点存下的东西都还在：已推送的提交在任务"
    "分支上，当时没提交的改动和未跟踪文件在平台快照里，用 "
    'cd "$(cheese worktree <任务 id>)" 重新打开任务目录时自动放回，并说明放回了'
    "什么。检查点之后才做的改动、依赖和缓存、生成目录、/tmp、正在运行的进程不在"
    "了，需要的重新做、重新安装、重新启动。"
)
LOST_KEY = "sandbox_lost"


async def _home_removed(db, session_id) -> bool:
    """The sandbox being destroyed is gone, so the next attempt places the
    session in a new one."""
    stopped = await db.scalar(
        select(CloudHostHome.stopped_at).where(
            CloudHostHome.session_id == session_id, CloudHostHome.left_at.is_(None)
        )
    )
    return stopped is None


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
