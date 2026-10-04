"""并进现有四块的缺口 —— 用量燃尽、投递/事件积压、磁盘与预览、机器普查。

放在一起是因为它们有一个共同形状：**现有四块各自都「看着对」，却都漏掉了会让读的人
得出相反结论的那半边**。少了它们：

* 用量：三个项目同时停摆时，token 曲线只是「今天用量下降」，看起来像好消息。
* 性能：接口很快、投递却发不出去 —— 用户什么都没收到，p95 还是绿的。
* 平台：磁盘写满会让所有房间一起死，而账号曲线纹丝不动。

**口径上三条硬事实**（每条都写在对应方法上，这里只点名）：

1. `compute_grants` **没有**扣减账本 —— `credits_used` 只是一个只增计数器。所以
   「燃烧速率」只能从 `resource_usage` 推，不能读余额差分（新发放一到账，差分就是
   负的）。
2. 「unlimited」**不是一列**，它是**没有适用的 grant**（`summary()['unlimited']`）。
   没有 grant 的项目默认不计量 —— 所以「已耗尽 / <10% / unlimited」是三个互斥集合，
   要分别数。
3. `_disk_snapshot` 只覆盖**后端这台机器**的 `workspace_root`。远端设备和云主机有各
   自的磁盘，这里看不见 —— 页面上写的是「这台后端」，不是「全平台」。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.device.models import DeviceRow, HostedDeviceRow
from app.domain.machine.models import (
    CloudHost,
    CloudHostHome,
    WarmMachine,
    capacity,
)
from app.domain.platform_stats.windows import utc_day_window
from app.domain.project.models import Project
from app.domain.usage import ledger
from app.domain.usage.models import ComputeGrant, GrantSource, ResourceUsage

#: 磁盘压力档位的阈值。写在读这一侧：它们是**看板的判据**（「多少算紧张」），不是
#: 平台行为的开关 —— 不要拿它去触发任何自动清理。
DISK_WARN_PCT = 85.0
DISK_CRIT_PCT = 95.0

#: 「快烧完」的线。和磁盘阈值同一逻辑：是看板的判据，不是计费开关。
LOW_CREDIT_RATIO = 0.10


class GapRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---- 用量：额度燃尽 ----------------------------------------------------

    async def credits_burnout(self, *, days: int) -> dict[str, Any]:
        """已耗尽 / 快烧完 / unlimited 三个互斥名单，外加燃烧速率与估尽时刻。

        `burn_rate` 从 `resource_usage.credits` 推，**不是**余额差分
        —— 理由见模块 docstring 第 1 条。`exhaust_eta` 是线性外推，两个半边都会在
        新发放到账或项目安静下来时立刻失效，所以 unlimited 与零燃烧都回 `None`
        （画破折号），0 和 Infinity 在这里都是谎言。
        """
        since, until, _ = utc_day_window(days)
        now = datetime.now(UTC)
        grants = (
            (
                await self._session.execute(
                    # A plan pack is every personal team's monthly allowance;
                    # summed into one pool they would say nothing. Lapsed packs
                    # can no longer be spent.
                    select(ComputeGrant)
                    .where(
                        ComputeGrant.source != GrantSource.PLAN_PERIOD.value,
                        or_(
                            ComputeGrant.expires_at.is_(None),
                            ComputeGrant.expires_at > now,
                        ),
                    )
                    .order_by(ComputeGrant.created_at)
                )
            )
            .scalars()
            .all()
        )
        by_project: dict[Any, list[ComputeGrant]] = {}
        for g in grants:
            key = g.project_id  # team-level grants sit under None
            by_project.setdefault(key, []).append(g)

        exhausted: list[dict[str, Any]] = []
        low: list[dict[str, Any]] = []
        unlimited_ids = await self._unlimited_project_ids()
        rows = await self._project_names(list(by_project.keys()))

        for pid, gs in by_project.items():
            total = sum(g.credits_total for g in gs)
            used = sum(g.credits_used for g in gs)
            remaining = total - used
            name = rows.get(pid, "（团队池）" if pid is None else str(pid))
            entry = {
                "project_id": str(pid) if pid else None,
                "name": name,
                "credits_total": total,
                "credits_used": used,
                "credits_remaining": remaining,
                "ratio": (remaining / total) if total > 0 else 0.0,
            }
            if remaining <= 0:
                exhausted.append(entry)
            elif total > 0 and (remaining / total) <= LOW_CREDIT_RATIO:
                low.append(entry)

        burn = await self._burn_rate(since=since, until=until)
        etas = []
        if burn["credits_per_day"] and burn["credits_per_day"] > 0:
            for entry in exhausted + low:
                days_left = entry["credits_remaining"] / burn["credits_per_day"]
                entry["exhaust_eta_days"] = days_left
                etas.append(days_left)
            # exhausted 名单里 remaining<=0，eta 是负数或 0 —— 保留，它说的是
            # 「已经超了」，不是「还要几天」。
        return {
            "days": days,
            "exhausted": exhausted,
            "low": low,
            "unlimited_project_ids": [str(i) for i in unlimited_ids],
            "unlimited_count": len(unlimited_ids),
            "burn": burn,
            "note_key": "usage.creditsNote",
        }

    async def _burn_rate(self, *, since, until) -> dict[str, Any]:
        """窗口内的 credit 燃烧速率：`resource_usage.credits` 之和，也就是每一行
        记账时实际折算的额度，不是余额差分。"""
        credits = float(
            await self._session.scalar(
                select(func.coalesce(func.sum(ResourceUsage.credits), 0.0)).where(
                    ResourceUsage.created_at >= since,
                    ResourceUsage.created_at < until,
                    # The rate project and team credits burn at; personal
                    # spend draws on other grants.
                    ResourceUsage.project_id.is_not(None),
                )
            )
            or 0.0
        )
        return {
            "credits_in_window": credits,
            "credits_per_day": credits / max(1, (until - since).days),
            "method": "derived_from_resource_usage",
        }

    async def _unlimited_project_ids(self) -> list:
        """不计量（unlimited）的项目：所属团队挂在不限量的方案上。判据与准入同一
        处（`usage.ledger`）——这里数的是「方案不限量」，不是「额度用完了」。"""
        projects = list((await self._session.execute(select(Project))).scalars())
        payers = await ledger.payers_for_projects(self._session, projects)
        balances = await ledger.Ledger(self._session).balances(payers)
        return [pid for pid, balance in balances.items() if balance.unlimited]

    async def _project_names(self, ids: list) -> dict:
        ids = [i for i in ids if i is not None]
        if not ids:
            return {}
        rows = await self._session.execute(
            select(Project.id, Project.name).where(Project.id.in_(ids))
        )
        return {pid: name for pid, name in rows}

    # ---- 性能：投递与事件积压 ----------------------------------------------

    async def reliability(self) -> dict[str, Any]:
        """投递账本的积压与死信。"""
        from app.domain.delivery.ledger import MAX_ATTEMPTS
        from app.domain.delivery.models import Delivery

        unsent = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(Delivery)
                    .where(Delivery.sent_at.is_(None), Delivery.attempts < MAX_ATTEMPTS)
                )
            ).scalar_one()
            or 0
        )
        dead = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(Delivery)
                    .where(
                        Delivery.sent_at.is_(None), Delivery.attempts >= MAX_ATTEMPTS
                    )
                )
            ).scalar_one()
            or 0
        )
        return {
            "delivery_unsent": unsent,
            "delivery_dead_letters": dead,
        }

    # ---- 平台：磁盘 / 预览 / 机器普查 ---------------------------------------

    async def platform_extras(self) -> dict[str, Any]:
        return {
            "disk": self._disk(),
            "preview": self._preview(),
            "machines": await self._machine_census(),
        }

    @staticmethod
    def _disk() -> dict[str, Any]:
        """后端这台机器 `workspace_root` 的剩余空间与压力档位。

        **只覆盖这一台**（模块 docstring 第 4 条）。远端设备、云主机各有各的盘。
        """
        import shutil

        root = getattr(settings, "workspace_root", None)
        if not root or not Path(root).exists():
            return {
                "available": False,
                "tier": "unknown",
                "note_key": "platform.diskMissing",
            }
        usage = shutil.disk_usage(root)
        used_pct = (usage.used / usage.total * 100.0) if usage.total else 0.0
        if used_pct >= DISK_CRIT_PCT:
            tier = "critical"
        elif used_pct >= DISK_WARN_PCT:
            tier = "warn"
        else:
            tier = "ok"
        return {
            "available": True,
            "free_gb": round(usage.free / (1024**3), 2),
            "total_gb": round(usage.total / (1024**3), 2),
            "used_pct": round(used_pct, 1),
            "tier": tier,
            "warn_pct": DISK_WARN_PCT,
            "critical_pct": DISK_CRIT_PCT,
            "note_key": "platform.diskNote",
        }

    @staticmethod
    def _preview() -> dict[str, Any]:
        """预览 helper 的在飞数量。**进程内存**，重启即清零。

        和 `MachineInventoryRepository` 报不了在线设备是同一类缺口：在线状态住在进程
        内存里，库里没有那一列。页面上写的是「这个进程里的预览连接」，不是「全平台」。
        """
        try:
            from app.domain.agent.preview_hub import preview_hub

            machines = getattr(preview_hub, "_machines", {})
            return {
                "available": True,
                "attached": len(machines),
                "note_key": "platform.previewNote",
            }
        except Exception:
            return {
                "available": False,
                "attached": None,
                "note_key": "platform.previewMissing",
            }

    async def _machine_census(self) -> dict[str, Any]:
        """温机/云端宿主机按状态的存量普查，和宿主机上的沙箱槽位。

        槽位是 ``cloud_host_homes`` 里的行数（每条会话在宿主机上的家占一格）对
        ``capacity``（核数 × ``cloud_host_slots_per_core``）；只数还在池子里的宿主
        机。整台云虚拟机不出槽位，也不占槽位。这里数的是台账里的**行**与状态
        分布，不是在线进程。
        """

        async def _count(model: Any, *where: Any) -> int:
            stmt = select(func.count()).select_from(model)
            if where:
                stmt = stmt.where(*where)
            return int((await self._session.execute(stmt)).scalar_one() or 0)

        warm_rows = (
            await self._session.execute(
                select(WarmMachine.state, func.count()).group_by(WarmMachine.state)
            )
        ).all()
        host_rows = (
            await self._session.execute(
                select(CloudHost.status, func.count()).group_by(CloudHost.status)
            )
        ).all()
        live = list(
            await self._session.scalars(
                select(CloudHost).where(CloudHost.released_at.is_(None))
            )
        )
        return {
            "devices": await _count(DeviceRow),
            "hosted_devices": await _count(HostedDeviceRow),
            "warm_total": await _count(WarmMachine),
            "warm_by_state": {str(s): int(n) for s, n in warm_rows},
            "warm_error": await _count(
                WarmMachine, WarmMachine.error.is_not(None), WarmMachine.error != ""
            ),
            "host_total": await _count(CloudHost),
            "host_by_status": {str(s): int(n) for s, n in host_rows},
            "host_active": len(live),
            "host_enroll_error": await _count(
                CloudHost,
                CloudHost.enroll_error.is_not(None),
                CloudHost.enroll_error != "",
            ),
            "host_slots_used": await _count(
                CloudHostHome,
                CloudHostHome.host_id.in_(
                    [host.id for host in live if not host.whole_machine]
                ),
            ),
            "host_slots_total": sum(capacity(host) for host in live),
            "note_key": "platform.machinesNote",
        }
