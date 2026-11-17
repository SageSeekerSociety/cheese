"""评测花的算力记在谁头上（方案 §算力与费用）。

记在题目所在空间的**所有者**头上 —— 他的个人团队付，和他自己在项目外用模型一样
（``usage.payers.payer_for_person``）。不记在学生的领题项目上：那样学生自己的额度会被
评测吃掉，而要不要挂测试点是出题一方定的。

开跑前问一次能不能跑（``usage.compute.admit_start``，``kind = JUDGE``）：平台没设评测
价格就一律不跑（和云沙箱一样，没有默认价格），所有者没额度了也不跑 —— 这两种都让这次
评测标「失败」并带上那句话，不扣钱。跑完按整分钟向上取整记一笔（``Ledger.record``，
``route = compute``、``kind = judge``），和评测结果在同一个事务里写下。
"""

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.space.models import SpaceAdminRelation, SpaceAdminRole
from app.domain.usage import compute
from app.domain.usage.ledger import Ledger, Payer, payer_for_person
from app.domain.usage.models import COMPUTE_ROUTE


async def space_owner(session: AsyncSession, space_id: int | None) -> int | None:
    """The user id of the space's owner; None when it has none."""
    if space_id is None:
        return None
    return await session.scalar(
        select(SpaceAdminRelation.user_id).where(
            SpaceAdminRelation.space_id == space_id,
            SpaceAdminRelation.role == SpaceAdminRole.OWNER.value,
            SpaceAdminRelation.deleted_at.is_(None),
        )
    )


async def payer_for_space(session: AsyncSession, owner_id: int) -> Payer:
    """Who pays for judging in a space: its owner's personal team."""
    return await payer_for_person(session, owner_id)


async def admit(session: AsyncSession, payer: Payer) -> None:
    """Whether a judging ``payer`` pays for may run now. Raises
    ``compute.ComputeRefused`` with the sentence that says why not: no price
    is set for judging, or the payer has no credits left."""
    await compute.admit_start(session, payer, kind=compute.JUDGE, spec=compute.JUDGE)


def minutes_of(machine_ms: int) -> int:
    """Whole minutes a judging is charged for: its time on the host, rounded
    up, at least one."""
    return max(1, math.ceil(max(machine_ms, 0) / 60_000))


async def charge(
    session: AsyncSession,
    payer: Payer,
    *,
    owner_id: int,
    machine_ms: int,
    runtime: str,
) -> float:
    """Write and charge one judging. Returns the credits it cost."""
    per_minute = settings.judge_credits_per_minute
    if per_minute is None:
        return 0.0
    credits = minutes_of(machine_ms) * per_minute
    await Ledger(session).record(
        payer,
        credits=credits,
        model=runtime,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        route=COMPUTE_ROUTE,
        kind=compute.JUDGE,
        user_id=owner_id,
    )
    return credits
