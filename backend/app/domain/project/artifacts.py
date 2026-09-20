"""产物清单 —— 项目做出来的东西，一项一行 (#1085 结论二、三)。

**清单由交付长出来，不事先声明。** 建项目的时候没人说得清要产出什么（#1054 已经
决定不在那时多问），所以这里没有「新建产物」这个动作：每次交付时点名本次更新的是
哪一项，名字还不在清单上就当场长出一项。这样清单永远等于真的做出来过的东西，不会
出现声明了却从未做的空项。

**新建是少见动作**，因为一个项目产出的东西就那么几样，而报错的方向是明确的：把
「报告」写成「结题报告」不会撞出一个错误，只会在清单上多出一项看着像重复的东西。
所以这一下要在房间里说出来（`review/services.py` 的 `_announce_declared`），让当场
就有人看见。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.domain.project.models import ProjectArtifact
from app.domain.review.models import AcceptCard, AcceptStatus

#: 名字的长度上限，与 `ProjectArtifact.name` 这一列一致。
NAME_MAX = 200


def clean_name(raw: str | None) -> str:
    """产物的真名 —— 交付时点名用的那个词。

    只挡「不是一个名字」的东西：空的、换行的（清单一行一项，名字里带换行会把它撑
    开）、长过这一列的。别的一概不管：《结题报告》《报告.docx》《项目官网》都是用
    户自己会用的说法，平台没有立场替他统一。
    """
    name = " ".join((raw or "").split())
    if not name:
        raise ValidationError(
            "没说这次交付更新了哪一项产物。用它的真名点名一项 —— "
            "沿用清单上已有的名字就是同一项的新一版，新名字会新建一项。"
        )
    if len(name) > NAME_MAX:
        raise ValidationError(f"产物的名字最长 {NAME_MAX} 个字")
    return name


async def declare(
    session: AsyncSession, *, project_id: uuid.UUID, name: str
) -> tuple[ProjectArtifact, bool]:
    """按名字认领这个项目的一项产物，返回它和「这一下是不是新建的」。

    同名就是同一项：名字是产物的身份，第 1 版和第 7 版叫同一个名字才说得出它们是
    同一个东西。两个房间同时交付一个还不存在的名字时，先落地的那一个建出来，另一
    个认领它 —— 谁先谁后不影响结果，清单上都只有一项。
    """
    clean = clean_name(name)
    found = await _by_name(session, project_id=project_id, name=clean)
    if found is not None:
        return found, False
    row = ProjectArtifact(project_id=project_id, name=clean)
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError:
        # 另一条交付在这两句之间把同一个名字建出来了。认领它。
        raced = await _by_name(session, project_id=project_id, name=clean)
        if raced is None:
            raise
        return raced, False
    return row, True


@dataclass(frozen=True, slots=True)
class ArtifactSummary:
    """清单上的一行：真名、当前版本、最近更新。"""

    id: uuid.UUID
    name: str
    #: 交付过几次。0 = 声明过但还没有任何一次交付被采纳。
    version: int
    #: 最近一次交付被采纳的时刻；一次都还没有时为空。
    delivered_at: datetime | None


def _accepted_deliveries():
    """采纳了的、点名某一项产物的卡 —— 一张就是一版。

    只数 `accepted`：撤回采纳把卡退回 `revoked`，那一版随之不在了，而这正是数出来
    比存一个计数器好的地方 —— 不需要在撤回那条路上再记得减一次。
    """
    return (
        select(
            AcceptCard.artifact_id.label("artifact_id"),
            func.count().label("version"),
            func.max(AcceptCard.decided_at).label("delivered_at"),
        )
        .where(
            AcceptCard.artifact_id.is_not(None),
            AcceptCard.status == AcceptStatus.accepted,
        )
        .group_by(AcceptCard.artifact_id)
        .subquery()
    )


async def list_for_project(
    session: AsyncSession, project_id: uuid.UUID
) -> list[ArtifactSummary]:
    """这个项目的清单，最近交付的在前，还没交付过的按声明顺序排在后面。"""
    deliveries = _accepted_deliveries()
    rows = await session.execute(
        select(
            ProjectArtifact,
            func.coalesce(deliveries.c.version, 0),
            deliveries.c.delivered_at,
        )
        .outerjoin(deliveries, deliveries.c.artifact_id == ProjectArtifact.id)
        .where(ProjectArtifact.project_id == project_id)
        .order_by(
            deliveries.c.delivered_at.desc().nullslast(),
            ProjectArtifact.created_at.asc(),
        )
    )
    return [
        ArtifactSummary(
            id=row.id, name=row.name, version=version, delivered_at=delivered_at
        )
        for row, version, delivered_at in rows
    ]


async def summary(
    session: AsyncSession, artifact_id: uuid.UUID
) -> ArtifactSummary | None:
    """清单上那一行，单独取一项。"""
    deliveries = _accepted_deliveries()
    found = await session.execute(
        select(
            ProjectArtifact,
            func.coalesce(deliveries.c.version, 0),
            deliveries.c.delivered_at,
        )
        .outerjoin(deliveries, deliveries.c.artifact_id == ProjectArtifact.id)
        .where(ProjectArtifact.id == artifact_id)
    )
    row = found.first()
    if row is None:
        return None
    artifact, version, delivered_at = row
    return ArtifactSummary(
        id=artifact.id,
        name=artifact.name,
        version=version,
        delivered_at=delivered_at,
    )


async def _by_name(
    session: AsyncSession, *, project_id: uuid.UUID, name: str
) -> ProjectArtifact | None:
    found = await session.execute(
        select(ProjectArtifact).where(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.name == name,
        )
    )
    return found.scalar_one_or_none()
