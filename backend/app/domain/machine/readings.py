"""What each platform host and each sandbox on it uses, kept for the admin
machines page, and the alert when a host runs short.

The disk guard reads every platform host once a minute (``disk_guard``), and
with the same reading the host reports its memory and swap, its memory
pressure, whether zswap is loaded, the CPU time its processes have taken, its
load and what each sandbox's cgroup holds — memory now and at most, anonymous
and file pages, swap, OOM kills (``sandbox_home.disk``). This keeps them:

- one ``HostReading`` per host per reading, for ``KEEP``, so the page shows the
  last one, how old it is, and the hours before it;
- one ``SandboxUsage`` per sandbox: its memory from every reading, its home's
  bytes and inodes from every walk.

A host that has used more than ``LINES`` of its disk, inodes or
memory on two readings running is reported to the alert group. Two, so a
memory spike that is gone a minute later says nothing. It is reported on every
such reading, and ``alerting`` decides what goes out: the first, then once an
hour, four hours, a day while it lasts. The alert is sent here rather than
logged as an error, because the error-log path (``obs.AlertOnError``) keys an
alert by its message with ids masked, and hostnames are hex: one host's alert
would silence every other host's for the hour.
"""

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import alerting
from app.core.config import settings
from app.domain.machine import holder as holders
from app.domain.machine.models import (
    CloudHost,
    HostReading,
    SandboxHome,
    SandboxUsage,
)

logger = logging.getLogger("cheese.machine.readings")

#: How long a host's readings are kept.
KEEP = timedelta(hours=6)
#: What a host runs short of, as the alert names it.
SHORT_OF = {"disk": "磁盘", "inodes": "inode", "memory": "内存"}


def _share(total: int | None, free: int | None) -> float | None:
    if not total or free is None:
        return None
    return max(0.0, min(1.0, 1 - free / total))


def used(reading: HostReading) -> dict[str, float | None]:
    """The share of its disk, inodes and memory a reading found used."""
    return {
        "disk": _share(reading.disk_bytes, reading.disk_free_bytes),
        "inodes": _share(reading.inodes, reading.inodes_free),
        "memory": _share(reading.memory_bytes, reading.memory_available_bytes),
    }


#: Past which share used a host is reported, of each: early enough to act
#: before the disk guard's floors (``sandbox_disk_free_floor``, 10% free, and
#: ``sandbox_inodes_free_floor``, 5%) start refusing and evicting sandboxes,
#: and, for memory, before the reclaim that stalled a dev host for five hours
#: (2026-10-05).
LINES = {"disk": 0.85, "inodes": 0.90, "memory": 0.90}


def past(reading: HostReading) -> dict[str, float]:
    """What a reading found past its line, and how much of it was used."""
    return {
        kind: share
        for kind, share in used(reading).items()
        if share is not None and share > LINES[kind]
    }


def cpu_busy(
    reading: HostReading, before: HostReading | None, cores: int
) -> float | None:
    """The share of its cores a host kept busy between two readings."""
    if (
        before is None
        or reading.cpu_usage_usec is None
        or before.cpu_usage_usec is None
        or cores <= 0
    ):
        return None
    elapsed = (reading.read_at - before.read_at).total_seconds() * 1e6
    taken = reading.cpu_usage_usec - before.cpu_usage_usec
    # A counter that went back is a host that restarted.
    if elapsed <= 0 or taken < 0:
        return None
    return min(1.0, taken / (elapsed * cores))


async def record(
    session: AsyncSession,
    host: CloudHost,
    reading: dict,
    live: list,
    now: datetime,
) -> None:
    """Keep what one disk check read of ``host``: ``live`` is the sandbox
    rows it was read for, and ``reading`` holds its homes only on a walk."""
    filesystem = reading["filesystem"]
    machine = reading.get("machine") or {}
    pressure = machine.get("memory_pressure") or {}
    some = pressure.get("some") or {}
    full = pressure.get("full") or {}
    groups = reading.get("sandboxes")
    previous = await session.scalar(
        select(HostReading)
        .where(HostReading.host_id == host.id)
        .order_by(HostReading.read_at.desc())
        .limit(1)
    )
    row = HostReading(
        host_id=host.id,
        read_at=now,
        disk_bytes=filesystem["bytes"],
        disk_free_bytes=filesystem["bytes_free"],
        inodes=filesystem.get("inodes") or None,
        inodes_free=filesystem.get("inodes_free") if filesystem.get("inodes") else None,
        memory_bytes=machine.get("memory"),
        memory_available_bytes=machine.get("memory_available"),
        swap_bytes=machine.get("swap_bytes"),
        swap_free_bytes=machine.get("swap_free_bytes"),
        memory_pressure_some_avg10=some.get("avg10"),
        memory_pressure_some_avg60=some.get("avg60"),
        memory_pressure_full_avg10=full.get("avg10"),
        memory_pressure_full_avg60=full.get("avg60"),
        zswap=machine.get("zswap"),
        cpu_usage_usec=machine.get("cpu_usage_usec"),
        load1=machine.get("load1"),
        sandboxes=len(groups) if groups is not None else None,
    )
    homes = {
        (str(holders.namespace_of(home)), home.resource_id): home.id for home in live
    }
    by_resource = {home.resource_id: home.id for home in live}
    usage: dict[uuid.UUID, dict] = {}
    for home in reading.get("homes") or []:
        home_id = homes.get((home["project"], home["resource"]))
        if home_id is not None:
            usage.setdefault(home_id, {}).update(
                disk_bytes=home["bytes"], disk_inodes=home["inodes"], disk_read_at=now
            )
    for group in groups or []:
        home_id = by_resource.get(group["resource"])
        if home_id is not None:
            usage.setdefault(home_id, {}).update(
                memory_bytes=group["memory"],
                memory_peak_bytes=group.get("memory_peak"),
                anon_bytes=group.get("anon"),
                file_bytes=group.get("file"),
                swap_bytes=group.get("swap"),
                oom_kills=group.get("oom_kill"),
                memory_read_at=now,
            )
    # What the alert needs, read now: a rollback below expires every row the
    # session has loaded, and reading one after it fails.
    who = (host.id, host.hostname)
    # Two readings running: the one before must be the check before, not one
    # from before the host went away.
    before = (
        past(previous)
        if previous is not None
        and now - previous.read_at
        <= timedelta(seconds=3 * settings.sandbox_disk_check_s)
        else {}
    )
    # The read took up to two minutes: a sandbox destroyed meanwhile has no row
    # left for its usage, and its reading is dropped rather than the host's.
    if not await _write(session, row, usage, now) and not await _write(
        session, row, {}, now
    ):
        return
    _alert(*who, row, before)


async def _write(
    session: AsyncSession,
    row: HostReading,
    usage: dict[uuid.UUID, dict],
    now: datetime,
) -> bool:
    """Write a host's reading and its sandboxes' usage in one transaction.
    False, with nothing written, when a row it belongs to went meanwhile."""
    try:
        session.add(row)
        present = (
            set(
                await session.scalars(
                    select(SandboxHome.id).where(SandboxHome.id.in_(usage))
                )
            )
            if usage
            else set()
        )
        for home_id in present:
            values = usage[home_id]
            await session.execute(
                insert(SandboxUsage)
                .values(home_id=home_id, **values)
                .on_conflict_do_update(
                    index_elements=[SandboxUsage.home_id], set_=values
                )
            )
        await session.execute(
            delete(HostReading).where(HostReading.read_at < now - KEEP)
        )
        await session.commit()
    except IntegrityError:
        # The rollback leaves ``row`` unsaved, for the caller to try again.
        await session.rollback()
        logger.info("a reading of host %s outlived what it read", row.host_id)
        return False
    return True


def _alert(
    host_id: uuid.UUID, hostname: str, row: HostReading, before: dict[str, float]
) -> None:
    for kind, share in past(row).items():
        if kind not in before:
            continue
        alerting.send(
            f"宿主机 {hostname} 的{SHORT_OF[kind]}快满了",
            [
                f"已用：{share:.0%}（告警线 {LINES[kind]:.0%}）",
                f"现在：{_amount(row, kind)}",
                f"上面的沙箱：{row.sandboxes if row.sandboxes is not None else '未知'}",
                "详情：管理后台「机器」页",
            ],
            key=f"machine-capacity|{host_id}|{kind}",
            when=row.read_at.timestamp(),
        )


def _amount(row: HostReading, kind: str) -> str:
    if kind == "disk":
        return (
            f"{(row.disk_bytes - row.disk_free_bytes) / 1e9:.1f} / "
            f"{row.disk_bytes / 1e9:.1f} GB"
        )
    if kind == "inodes":
        return f"{(row.inodes or 0) - (row.inodes_free or 0):,} / {row.inodes or 0:,}"
    total = row.memory_bytes or 0
    return (
        f"{(total - (row.memory_available_bytes or 0)) / 2**30:.1f} / "
        f"{total / 2**30:.1f} GiB"
    )


#: How the hours of readings before the last one are drawn: the most of each
#: stretch this long, so a minute's peak is not averaged away.
STEP = timedelta(minutes=5)


def _less(total: int | None, free: int | None) -> int | None:
    return total - free if total is not None and free is not None else None


def _usage_of(last: HostReading, before: HostReading | None, cores: int) -> dict:
    return {
        "read_at": last.read_at.isoformat(),
        "disk_bytes": last.disk_bytes,
        "disk_used_bytes": last.disk_bytes - last.disk_free_bytes,
        "inodes": last.inodes,
        "inodes_used": _less(last.inodes, last.inodes_free),
        "memory_bytes": last.memory_bytes,
        "memory_used_bytes": _less(last.memory_bytes, last.memory_available_bytes),
        "swap_bytes": last.swap_bytes,
        "swap_used_bytes": _less(last.swap_bytes, last.swap_free_bytes),
        "memory_pressure_some_avg10": last.memory_pressure_some_avg10,
        "memory_pressure_some_avg60": last.memory_pressure_some_avg60,
        "memory_pressure_full_avg10": last.memory_pressure_full_avg10,
        "memory_pressure_full_avg60": last.memory_pressure_full_avg60,
        "zswap": last.zswap,
        "cpu_busy": cpu_busy(last, before, cores),
        "load1": last.load1,
        "sandboxes": last.sandboxes,
    }


async def hosts_now(
    session: AsyncSession, hosts: list[CloudHost], now: datetime
) -> dict[uuid.UUID, dict]:
    """Each host's last reading, and the most of its memory and CPU used in
    each ``STEP`` of the ``KEEP`` before now. A host never read is absent."""
    cores = {host.id: int(host.cores) for host in hosts}
    if not cores:
        return {}
    rows = await session.scalars(
        select(HostReading)
        .where(HostReading.host_id.in_(cores), HostReading.read_at >= now - KEEP)
        .order_by(HostReading.host_id, HostReading.read_at)
    )
    by_host: dict[uuid.UUID, list[HostReading]] = {}
    for row in rows:
        by_host.setdefault(row.host_id, []).append(row)
    steps = int(KEEP / STEP)
    out: dict[uuid.UUID, dict] = {}
    for host_id, series in by_host.items():
        memory: list[float | None] = [None] * steps
        cpu: list[float | None] = [None] * steps
        before: HostReading | None = None
        for row in series:
            index = max(0, min(steps - 1, int((row.read_at - (now - KEEP)) / STEP)))
            share = used(row)["memory"]
            if share is not None:
                memory[index] = max(memory[index] or 0.0, share)
            busy = cpu_busy(row, before, cores[host_id])
            if busy is not None:
                cpu[index] = max(cpu[index] or 0.0, busy)
            before = row
        out[host_id] = {
            **_usage_of(
                series[-1], series[-2] if len(series) > 1 else None, cores[host_id]
            ),
            "memory_history": memory,
            "cpu_history": cpu,
        }
    return out


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at is not None else None


async def sandboxes_now(
    session: AsyncSession, home_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """Each sandbox's last memory and disk reading. One never read is absent."""
    if not home_ids:
        return {}
    rows = await session.scalars(
        select(SandboxUsage).where(SandboxUsage.home_id.in_(home_ids))
    )
    return {
        row.home_id: {
            "memory_bytes": row.memory_bytes,
            "memory_peak_bytes": row.memory_peak_bytes,
            "anon_bytes": row.anon_bytes,
            "file_bytes": row.file_bytes,
            "swap_bytes": row.swap_bytes,
            "oom_kills": row.oom_kills,
            "memory_read_at": _iso(row.memory_read_at),
            "disk_bytes": row.disk_bytes,
            "disk_inodes": row.disk_inodes,
            "disk_read_at": _iso(row.disk_read_at),
        }
        for row in rows
    }
