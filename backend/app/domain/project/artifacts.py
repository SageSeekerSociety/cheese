"""产物清单 —— 项目做出来的东西，一项一行 (#1085 结论二、三)。

## 一项产物的一生

**生**：只有交付能创建它。建项目的时候没人说得清这个项目会产出什么（#1054 已经决
定不在那时多问），所以清单上没有「先登记一项」这回事 —— 递卡时声明本次交付的是一
项新东西（`claim`），那一下它才存在。

**长**：往后每一次交付沿用同一项（`reuse`）。一版是一次交付：清单上的「第 7 版」
就是第 7 张采纳了的、点名它的卡，所以撤回采纳那一版自己就不在了。

**在不在清单上**，由声明它的那些卡决定，不由这张表里有没有行决定：

- 交付落地过（有采纳了的卡）→ 在，并显示第几版；
- 还没落地但有一张在飞的卡声明了它 → 在，显示「尚未交付」。同一个名字这时已经点
  得到，另一个房间接着交付它用 `reuse`，不会重复新建；
- 两样都没有（声明它的那张卡被驳回、作废了）→ **不在**。什么都没交出去，而且没有
  人正在交，清单就没什么可说的。表里那一行留着：它是这个名字的身份，同一个名字再
  被声明时落回同一行，那一项的历史因此是连着的。

**沿用和新建是两个动作，不是一个参数的两种值。** 这是整套东西唯一的防线：名字写
错不会撞出错误，`报告` 和 `结题报告` 都是合法名字，而清单进每一轮的开场，错的那一
项从此每轮都在场。分成两个动作之后，「沿用一个不存在的名字」和「新建一个已经存在
的名字」都当场报错，而错一次的代价只是下一轮改对。

**改名、合并、删除是人的动作**，因为「这两项是不是同一个东西」要人判断。改名改的是
这一行，卡指着的是行的 id，所以改完之前的交付照样算这一项的版本。
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

#: 还在飞的卡：等人验收、等闸门、卡在冲突上。它声明的那一项已经算在清单上 ——
#: 有人正在交付它。与 `review/services.py` 的 `_BLOCKED_BY_CARD_MESSAGES` 同一批
#: 状态，那边问的是「还能不能再递一张」，这边问的是「这一项算不算在清单上」。
_LIVE_CARD_STATUSES = (
    AcceptStatus.pending,
    AcceptStatus.pending_gate,
    AcceptStatus.conflict,
)

#: 整对出现在名字两头时会被摘掉的括号与引号。系统提示里的清单排成《结题报告》，
#: 房间里的话也是这么写的，所以照抄一行时括号跟着进来是常事 —— 而 `《结题报告》`
#: 和 `结题报告` 是两个不同的名字，且不报错。
_WRAPPERS = (("《", "》"), ("“", "”"), ("「", "」"), ("'", "'"), ('"', '"'))


@dataclass(frozen=True, slots=True)
class ArtifactSummary:
    """清单上的一行：真名、当前版本、最近更新。"""

    id: uuid.UUID
    name: str
    #: 交付过几次。0 = 有一张在飞的卡声明了它，但还没有哪一次交付落地。
    version: int
    #: 最近一次交付被采纳的时刻；一次都还没有时为空。
    delivered_at: datetime | None


def _unwrap(name: str) -> str:
    """摘掉整个名字外面的那一对括号，只摘外面这一层。

    只在里面不再出现同一对符号时才摘：`《红楼梦》与《水浒》` 两头虽然也是书名号，
    摘掉就把它改成了另一个名字。
    """
    for left, right in _WRAPPERS:
        if len(name) >= 2 and name.startswith(left) and name.endswith(right):
            inner = name[1:-1]
            if left not in inner and right not in inner:
                return inner.strip()
    return name


def clean_name(raw: str | None) -> str:
    """产物的真名 —— 交付时点名用的那个词。

    只挡「不是一个名字」的东西：空的、长过这一列的。别的一概不管：《结题报告》
    《报告.docx》《项目官网》都是用户自己会用的说法，平台没有立场替他统一。
    """
    name = _unwrap(" ".join((raw or "").split()))
    if not name:
        raise ValidationError("产物的名字不能是空的")
    if len(name) > NAME_MAX:
        raise ValidationError(f"产物的名字最长 {NAME_MAX} 个字")
    return name


async def reuse(
    session: AsyncSession, *, project_id: uuid.UUID, ref: str
) -> ProjectArtifact:
    """沿用清单上已经有的那一项 —— 这次交付是它的新一版。

    `ref` 是清单上的真名，或者那一行的 id（界面上点出来的那条路）。对不上就报错，
    并把清单现在有什么列出来：读这句话的是一个下一轮就要重递的 agent，只说「没有
    这一项」等于让它再猜一轮，而猜错的后果是清单上多一项看着像重复的东西。
    """
    listed = await list_for_project(session, project_id)
    wanted = clean_name(ref)
    by_id = _as_uuid(wanted)
    for row in listed:
        if row.id == by_id or row.name == wanted:
            found = await session.get(ProjectArtifact, row.id)
            if found is not None:
                return found
    raise ValidationError(
        f"产物清单上没有《{wanted}》。" + _what_the_list_has(listed) + "确实是一样"
        "新做出来的东西，就用 new_artifact 声明它；要交付清单上已有的那一项，"
        "把名字照抄过去。"
    )


async def claim(
    session: AsyncSession, *, project_id: uuid.UUID, name: str
) -> ProjectArtifact:
    """声明这次交付做出了一样清单上还没有的东西。

    名字已经在清单上就报错：这是「新建」这个动作唯一能替人挡住的事 —— 它挡不住
    「《报告》其实就是《结题报告》」（那要人看），但挡得住「明明是同一项却又声明
    了一次新的」。
    """
    clean = clean_name(name)
    listed = await list_for_project(session, project_id)
    if any(row.name == clean for row in listed):
        raise ValidationError(
            f"产物清单上已经有《{clean}》了。这次交付是它的新一版就用 artifact "
            "沿用它；确实是另一样东西，就换一个说得出区别的名字。"
        )
    found = await _by_name(session, project_id=project_id, name=clean)
    if found is not None:
        # 这个名字此前被声明过，但那次交付没落地，所以它不在清单上。落回同一行：
        # 同一个名字是同一项，它的历史因此是连着的。
        return found
    row = ProjectArtifact(project_id=project_id, name=clean)
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError:
        # 另一条交付在这两句之间把同一个名字建出来了。认领它。
        raced = await _by_name(session, project_id=project_id, name=clean)
        if raced is None:
            raise
        return raced
    return row


async def list_for_project(
    session: AsyncSession, project_id: uuid.UUID
) -> list[ArtifactSummary]:
    """这个项目的清单，最近交付的在前，还没落地的排在后面。

    在不在清单上由卡决定（见模块开头）：交付落地过，或者有一张在飞的卡正在交付它。
    """
    claims = _claims()
    rows = await session.execute(
        select(
            ProjectArtifact,
            claims.c.landed,
            claims.c.live,
            claims.c.delivered_at,
        )
        .join(claims, claims.c.artifact_id == ProjectArtifact.id)
        .where(ProjectArtifact.project_id == project_id)
        .order_by(claims.c.delivered_at.desc().nullslast(), ProjectArtifact.created_at)
    )
    return [
        ArtifactSummary(
            id=row.id, name=row.name, version=landed, delivered_at=delivered_at
        )
        for row, landed, live, delivered_at in rows
        if landed or live
    ]


async def summary(
    session: AsyncSession, artifact_id: uuid.UUID
) -> ArtifactSummary | None:
    """清单上那一行，单独取一项。"""
    claims = _claims()
    found = await session.execute(
        select(ProjectArtifact, claims.c.landed, claims.c.delivered_at)
        .join(claims, claims.c.artifact_id == ProjectArtifact.id)
        .where(ProjectArtifact.id == artifact_id)
    )
    row = found.first()
    if row is None:
        return None
    artifact, landed, delivered_at = row
    return ArtifactSummary(
        id=artifact.id,
        name=artifact.name,
        version=landed,
        delivered_at=delivered_at,
    )


def _claims():
    """每一项产物被声明的情况：落地了几次、有没有人正在交付、最近一次是什么时候。

    落地的那个数就是版本 —— 一版是一次交付。存一个计数器要在每条合并成功的路上都
    记得加一、在撤回采纳的路上都记得减一，漏掉任何一条都不报错，只会让清单上的版
    本号和真交出去过的东西悄悄对不上。数出来的那个数没有这种失效方式。
    """
    landed = func.count(1).filter(AcceptCard.status == AcceptStatus.accepted)
    live = func.count(1).filter(AcceptCard.status.in_(_LIVE_CARD_STATUSES))
    return (
        select(
            AcceptCard.artifact_id.label("artifact_id"),
            landed.label("landed"),
            live.label("live"),
            func.max(AcceptCard.decided_at)
            .filter(AcceptCard.status == AcceptStatus.accepted)
            .label("delivered_at"),
        )
        .where(AcceptCard.artifact_id.is_not(None))
        .group_by(AcceptCard.artifact_id)
        .subquery()
    )


def _what_the_list_has(listed: list[ArtifactSummary]) -> str:
    if not listed:
        return "这个项目还没有交付过任何东西，清单是空的。"
    names = "、".join(f"《{row.name}》" for row in listed)
    return f"清单上现在有：{names}。"


def _as_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


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
