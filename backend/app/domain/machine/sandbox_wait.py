"""What a tool call waiting on its session's cloud sandbox, or its whole cloud
VM, watches, and what it is told meanwhile (``session_work._attempt``)."""

from sqlalchemy import select

from app.domain.machine.models import (
    GONE,
    MAX_ENROLL_ATTEMPTS,
    CloudHost,
    MachineStatus,
    SandboxHome,
)

# What a tool waiting on its cloud sandbox is told.
SANDBOX_PREPARING = "沙箱正在准备；对话和平台工具仍可用。"
EXECUTOR_SETUP_FAILED = "执行环境里的执行器没能装好：{reason}；对话和平台工具仍可用。"
# The reason above when the environment's disk is full: on a platform host the
# sandbox's own quota (`sandbox_host.py`), elsewhere the machine's disk. Its own
# error names a path and an errno, which tells a reader nothing to act on;
# inodes run out with space still free, so both are named.
DISK_FULL = "这个环境的磁盘满了，空间或文件数用完了，删掉不要的文件再试"
# What a tool call is told when its executor came up, or stayed up, in an
# environment whose disk is full: what it could not write is skipped
# (`bootstrap.spared`), an upgrade waits (`bootstrap.kept_on_full_disk`).
DISK_FULL_NOTICE = (
    "这个环境的磁盘满了（还剩 {mb} MB），平台有些文件没写下；删掉不要的文件腾出空间。"
)
VM_PREPARING = "虚拟机沙箱正在准备，要几分钟；对话和平台工具仍可用。"
VM_ERROR = "虚拟机沙箱出错：供应方报告错误。对话和平台工具仍可用。"
# What a session is told, once, with the first tool call that succeeds in a
# new sandbox: its old one was destroyed when idle (``lifecycle``) or given up
# with its host (``HostPool._fail``). Either records it under ``LOST_KEY`` in
# the session's ``execution_request``.
SANDBOX_LOST = (
    "沙箱环境已换成新的。只有推上去的提交还在，在任务分支上，用 "
    'cd "$(cheese worktree <任务 id>)" 重新打开任务目录。没提交的改动、没推的'
    "提交、依赖和缓存、/tmp、正在运行的进程都不在了，需要的重新做、重新安装、"
    "重新启动。"
)
# The same, for a sandbox in no project (``holder.Personal``): nothing of it
# was checkpointed, so nothing comes back.
SANDBOX_REPLACED = (
    "沙箱环境已换成新的。之前的文件、装过的软件和正在运行的进程都不在了，需要的重新"
    "生成、重新安装、重新启动。"
)


async def _home_removed(db, session_id) -> bool:
    """The sandbox being destroyed is gone, so the next attempt places the
    session in a new one."""
    stopped = await db.scalar(
        select(SandboxHome.stopped_at).where(SandboxHome.session_id == session_id)
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
