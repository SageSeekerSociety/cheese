"""管理后台「机器」（``GET /admin/stats/machines``）：平台的云端宿主机此刻是什么
样子，过去一天沙箱起得怎么样。

此刻的部分读台账：``cloud_hosts`` 每台一行（还在池里的，和已经放弃、provider 还没
确认删掉的），``sandbox_homes`` 每个沙箱一行，``warm_machines`` 每台温机一行。状态
是池子巡检（``HostPool.maintain`` / ``refresh_due``）上次从 MicroCloud 读到的，不是
这一刻去问的；连接器在不在线读的是本进程的 device hub。

宿主机和沙箱此刻用了多少读磁盘巡检留下的读数（``readings.py``）：每台宿主机最近一次
读数和之前几个小时的形状，每个沙箱最近一次的内存和磁盘。读数有多旧也给出来，过期的
读数一眼看得出。

过去一天的部分读运行记录：一个沙箱开始准备时记一条 ``cloud_startup``，就绪时记一条
``cloud_provisioning``（``progress.py``），两条都带 ``home``。按 ``home`` 配对，就
是一个会话等沙箱等了多久。会话被拒（``cloud_refused``）和宿主机被放弃
（``cloud_host_lost``）各有一条记录，写在 ``infra_records.py``。
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, Uuid, column, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent.environment import spec_of
from app.domain.machine import enrollment, infra_records, readings
from app.domain.machine.lifecycle import previewed_sessions
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError
from app.domain.machine.models import (
    MAX_PROVIDER_ERRORS,
    PROVIDER_ERROR_WINDOW,
    PROVIDER_PROBE_INTERVAL,
    CloudHost,
    MachineStatus,
    SandboxHome,
    WarmMachine,
    capacity,
    warm_pool_target,
)
from app.domain.machine.repositories import CloudHostRepository
from app.domain.run_record.models import RunRecord

#: 过去多久算「最近」：沙箱起得怎么样只看这一段。
HISTORY = timedelta(hours=24)
#: 最近一次工具调用在这之内，沙箱算「工作中」；``active_at`` 最多一分钟挪一次
#: （``services.ACTIVE_STEP``），所以至少两分钟。
WORKING_WITHIN = timedelta(minutes=2)

#: 沙箱用到上限的这一份，页面上标出来。
NEAR_CAP = 0.8

#: 项目叫什么、房间叫什么：只读这两列，不为它把项目、话题领域拉进来。
_projects = table("projects", column("id", Uuid), column("name", String))
_rooms = table("topics", column("id", Uuid), column("title", String))


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at is not None else None


async def _current_image_version() -> str | None:
    """What a host created right now would be made of, so the page can say
    which hosts are out of date. Asks the provider with a short timeout, not
    ``microcloud_timeout_s``: a page that only reports must not hang on a
    provider that is away, and an unreadable version is shown as unknown rather
    than as agreed."""
    if not settings.microcloud_base_url or not settings.microcloud_tenant_secret:
        return None
    try:
        return await enrollment.current_host_image_version(
            MicroCloudClient(timeout=5.0)
        )
    except (MicroCloudError, ValidationError):
        return None


def host_phase(host: CloudHost, *, homes: int, online: bool) -> str:
    """一台宿主机在它一生里走到哪一步了，一个词。

    依次是：放弃了（``failed``：provider 把它弄坏了；``releasing``：空闲到期、整台
    虚拟机用完了，或连接器失联太久，在等 provider 删掉）；还没有 provider 的机器号
    （``creating``）；provider 报错（``error``）；还没注册成设备（``enrolling``，
    注册报过错的是 ``enroll_failing``）；连接器不在（``offline``）；一个沙箱都没有
    （``idle``，空闲满 ``cloud_host_idle_hold_s`` 会被释放）；否则 ``ready``。"""
    if host.released_at is not None:
        return "failed" if host.failed_at is not None else "releasing"
    if host.machine_id is None:
        return "creating"
    if host.status == MachineStatus.error:
        return "error"
    if host.enrolled_at is None:
        return "enroll_failing" if host.enroll_error else "enrolling"
    if not online:
        return "offline"
    if not homes and not host.whole_machine:
        return "idle"
    return "ready"


def sandbox_state(home: SandboxHome, now: datetime) -> str:
    """销毁中、已冻结、在等宿主机（会话已被告知「正在准备」）、工作中、空闲。"""
    if home.stopped_at is not None:
        return "stopping"
    if home.frozen_at is not None:
        return "frozen"
    if home.waiting_since is not None:
        return "waiting"
    if now - home.active_at <= WORKING_WITHIN:
        return "working"
    return "idle"


def _idle_deadline(home: SandboxHome, previewed: bool = False) -> datetime | None:
    """最早什么时候因空闲被回收：最近一次工具调用加上规格的空闲时长。真正的判据还
    看房间最后一轮何时结束、后台命令还在不在跑（``SandboxLifecycle._idle_since``），
    所以这是下限，不是时刻表。"""
    if home.frozen_at is not None:
        # Frozen for good past its keep time: the sweep destroys it then. A
        # conversation that previews an app is kept the longer time.
        seconds = (
            settings.cloud_sandbox_frozen_keep_preview_s
            if previewed
            else settings.cloud_sandbox_frozen_keep_s
        )
        return home.frozen_at + timedelta(seconds=seconds)
    spec = spec_of(home.spec)
    if spec is None or home.stopped_at is not None or home.waiting_since is not None:
        return None
    return home.active_at + timedelta(seconds=spec.idle_s)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(pct) - 1]


async def _names(
    session: AsyncSession, homes: list[SandboxHome], hosts: list[CloudHost]
) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
    project_ids = {h.project_id for h in homes if h.project_id is not None} | {
        h.project_id for h in hosts if h.project_id is not None
    }
    room_ids = {h.topic_id for h in homes}
    projects: dict[uuid.UUID, str] = {}
    rooms: dict[uuid.UUID, str] = {}
    if project_ids:
        for row_id, name in await session.execute(
            select(_projects.c.id, _projects.c.name).where(
                _projects.c.id.in_(project_ids)
            )
        ):
            projects[row_id] = name
    if room_ids:
        for row_id, title in await session.execute(
            select(_rooms.c.id, _rooms.c.title).where(_rooms.c.id.in_(room_ids))
        ):
            rooms[row_id] = title
    return projects, rooms


async def _history(
    session: AsyncSession, now: datetime, waiting_now: set[str]
) -> dict[str, Any]:
    """过去 ``HISTORY`` 里沙箱的准备、就绪、替换和回收，会话被拒，宿主机被放弃。"""
    since = now - HISTORY
    rows = (
        await session.execute(
            select(
                RunRecord.kind,
                RunRecord.severity,
                RunRecord.meta,
                RunRecord.created_at,
            )
            .where(
                RunRecord.kind.in_(
                    [
                        "cloud_startup",
                        "cloud_provisioning",
                        "sandbox_asleep",
                        infra_records.REFUSED,
                        infra_records.HOST_LOST,
                    ]
                ),
                RunRecord.created_at >= since,
            )
            .order_by(RunRecord.created_at)
        )
    ).all()
    started: dict[str, datetime] = {}
    ready: dict[str, datetime] = {}
    replaced = released_idle = released_unpaid = sandboxes_lost = 0
    refused: dict[str, set[str]] = defaultdict(set)
    hosts_lost: dict[str, int] = defaultdict(int)
    for kind, severity, meta, at in rows:
        meta = meta or {}
        home = meta.get("home")
        said = ((meta.get("i18n") or {}).get("content") or {}).get("key")
        if kind == infra_records.REFUSED:
            refused[str(meta.get("reason"))].add(str(meta.get("session_id")))
        elif kind == infra_records.HOST_LOST:
            hosts_lost[str(meta.get("reason"))] += 1
            sandboxes_lost += int(meta.get("lost") or 0)
        elif kind == "sandbox_asleep":
            if severity == "warn":
                released_unpaid += 1
            else:
                released_idle += 1
        elif kind == "cloud_startup" and said in {"sandboxReplaced", "cloudVmReplaced"}:
            replaced += 1
        elif kind == "cloud_startup" and home and said not in {"cloudVmReleasedIdle"}:
            started.setdefault(home, at)
        elif kind == "cloud_provisioning" and home in started and home not in ready:
            ready[home] = at
    waits = sorted((ready[home] - started[home]).total_seconds() for home in ready)
    # 开始准备了、一直没就绪、此刻也不在等：中途被放弃（宿主机起不来、会话走了）。
    abandoned = sum(
        1 for home in started if home not in ready and home not in waiting_now
    )
    return {
        "since": since.isoformat(),
        "prepared": len(started),
        "ready": len(ready),
        "abandoned": abandoned,
        "replaced": replaced,
        "released_idle": released_idle,
        "released_unpaid": released_unpaid,
        # 数会话，不数记录：被拒的会话每十分钟记一条
        # （``infra_records.REFUSAL_REPEAT``）。
        "refused_sessions": len(set().union(*refused.values())),
        "refused_by_reason": {reason: len(ids) for reason, ids in refused.items()},
        "hosts_lost": sum(hosts_lost.values()),
        "hosts_lost_by_reason": dict(hosts_lost),
        "sandboxes_lost": sandboxes_lost,
        "wait_p50_s": _percentile(waits, 50),
        "wait_p95_s": _percentile(waits, 95),
        "wait_max_s": waits[-1] if waits else None,
    }


async def overview(session: AsyncSession, hub, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    hosts = list(
        await session.scalars(
            select(CloudHost).order_by(
                CloudHost.released_at.is_not(None), CloudHost.created_at
            )
        )
    )
    homes = list(
        await session.scalars(
            select(SandboxHome)
            .where(SandboxHome.host_id.is_not(None))
            .order_by(SandboxHome.created_at)
        )
    )
    warm = list(
        await session.scalars(
            select(WarmMachine)
            # 被领走的已经是一台宿主机，删掉的已经不在：两种行都留在表里不清。
            .where(WarmMachine.state.not_in(["claimed", "deleted"]))
            .order_by(WarmMachine.created_at)
        )
    )
    projects, rooms = await _names(session, homes, hosts)
    previewed = await previewed_sessions(
        session, [home.session_id for home in homes if home.session_id is not None]
    )
    by_host: dict[uuid.UUID, list[SandboxHome]] = defaultdict(list)
    for home in homes:
        assert home.host_id is not None
        by_host[home.host_id].append(home)

    usage = await readings.hosts_now(
        session, [h for h in hosts if h.released_at is None], now
    )
    sandbox_usage = await readings.sandboxes_now(session, [h.id for h in homes])
    # What a host built now would be made of; None when the provider cannot be
    # asked, in which case the page says the versions' age but not their state.
    current_image = await _current_image_version()

    def online(device_id: str | None) -> bool:
        return device_id is not None and hub.is_online(device_id)

    host_rows = []
    for host in hosts:
        on = by_host.get(host.id, [])
        host_rows.append(
            {
                "id": str(host.id),
                "hostname": host.hostname,
                "machine_id": host.machine_id,
                "whole_machine": host.whole_machine,
                "project": projects.get(host.project_id) if host.project_id else None,
                "phase": host_phase(host, homes=len(on), online=online(host.device_id)),
                "status": str(host.status),
                "online": online(host.device_id),
                "ip": host.ip,
                "cores": host.cores,
                "memory_mb": host.memory_mb,
                "disk_gb": host.disk_gb,
                "slots": capacity(host),
                "used": len(on),
                "created_at": _iso(host.created_at),
                "enrolled_at": _iso(host.enrolled_at),
                "last_seen_at": _iso(host.last_seen_at),
                "idle_since": _iso(host.idle_since),
                "offline_since": _iso(host.offline_since),
                "failed_at": _iso(host.failed_at),
                "released_at": _iso(host.released_at),
                "image_version": host.image_version,
                # None: the provider could not be asked, or this is a whole VM,
                # which is never rolled.
                "image_outdated": (
                    None
                    if current_image is None or host.whole_machine
                    else host.image_version != current_image
                ),
                "draining": host.draining_since is not None,
                "draining_since": _iso(host.draining_since),
                "enroll_attempts": host.enroll_attempts,
                "enroll_error": host.enroll_error or None,
                "usage": usage.get(host.id),
                "sandboxes": [
                    {
                        "id": str(home.id),
                        "spec": home.spec,
                        "state": sandbox_state(home, now),
                        # None for a sandbox of a person's own conversation.
                        "project_id": (
                            str(home.project_id) if home.project_id else None
                        ),
                        "project": (
                            projects.get(home.project_id) if home.project_id else None
                        ),
                        "room_id": str(home.topic_id),
                        "room": rooms.get(home.topic_id),
                        "placed_at": _iso(home.created_at),
                        "waiting_since": _iso(home.waiting_since),
                        "active_at": _iso(home.active_at),
                        "stopped_at": _iso(home.stopped_at),
                        "idle_deadline": _iso(
                            _idle_deadline(home, home.session_id in previewed)
                        ),
                        "usage": sandbox_usage.get(home.id),
                    }
                    for home in on
                ],
            }
        )

    live = [h for h in hosts if h.released_at is None]
    pool = [h for h in live if not h.whole_machine]
    failures, last_failure = await CloudHostRepository(session).failures_since(
        now - PROVIDER_ERROR_WINDOW
    )
    # 和 ``HostPool._provider_failing`` 同一个判据：失败够数、离上一次还不到探测间隔，
    # 新会话就会被拒（``CloudKeepsFailing``）。
    refusing = (
        failures >= MAX_PROVIDER_ERRORS
        and last_failure is not None
        and now - last_failure < PROVIDER_PROBE_INTERVAL
    )
    waiting = [h for h in homes if h.waiting_since is not None and h.stopped_at is None]
    oldest_wait = min(
        (h.waiting_since for h in waiting if h.waiting_since is not None),
        default=None,
    )
    used = sum(len(by_host.get(h.id, [])) for h in pool)
    slots = sum(capacity(h) for h in pool)
    return {
        "configured": bool(
            settings.microcloud_base_url and settings.microcloud_tenant_secret
        ),
        "now": now.isoformat(),
        "pool": {
            "hosts": len(pool),
            "vms": len(live) - len(pool),
            "max_hosts": settings.cloud_pool_max_hosts,
            "at_cap": len(live) >= settings.cloud_pool_max_hosts,
            "slots": slots,
            "used": used,
            "free": max(0, slots - used),
            "min_free": settings.cloud_pool_min_free_slots,
            "waiting": len(waiting),
            "oldest_wait_since": _iso(oldest_wait),
            "provider_failures": failures,
            "provider_failure_limit": MAX_PROVIDER_ERRORS,
            "provider_window_min": int(PROVIDER_ERROR_WINDOW.total_seconds() // 60),
            "provider_probe_min": int(PROVIDER_PROBE_INTERVAL.total_seconds() // 60),
            "last_provider_failure": _iso(last_failure),
            "refusing": refusing,
            "fixed_hosts": settings.cloud_pool_fixed_hosts,
            "warm_target": warm_pool_target(),
        },
        "config": {
            "slots_per_core": settings.cloud_host_slots_per_core,
            "sandbox_memory_mb": settings.cloud_sandbox_memory_mb,
            "sandbox_idle_s": settings.cloud_sandbox_idle_stop_s,
            "host_idle_hold_s": settings.cloud_host_idle_hold_s,
            "rebuild_after_days": settings.cloud_host_rebuild_after_days,
            "vm_idle_s": settings.cloud_vm_idle_release_s,
            "sandbox_disk_budget_gb": settings.cloud_sandbox_disk_budget_gb,
            "sandbox_inode_budget": settings.cloud_sandbox_inode_budget,
            "near_cap": NEAR_CAP,
            "check_s": settings.sandbox_disk_check_s,
            "alert_used": dict(readings.LINES),
            "history_step_s": int(readings.STEP.total_seconds()),
        },
        "hosts": host_rows,
        "warm": [
            {
                "id": str(w.id),
                "state": w.state,
                "machine_id": w.machine_id,
                "hostname": w.create_request.get("hostname"),
                "cores": w.create_request.get("cores"),
                "memory_mb": w.create_request.get("memoryMb"),
                "disk_gb": w.create_request.get("diskGb"),
                "ip": w.ip,
                "online": online(w.device_id),
                "created_at": _iso(w.created_at),
                "enrolled_at": _iso(w.enrolled_at),
                "attempts": w.attempts,
                "error": w.error or None,
            }
            for w in warm
        ],
        "history": await _history(session, now, {str(h.id) for h in waiting}),
    }
