"""产物清单 —— 项目做出来的东西，一项一行 (#1085 结论二、三)。

## 一项产物的一生

**生**：只有交付能创建它。清单上没有「先登记一项」这回事 —— 一样东西是在第一次
交付它的那一下才存在的，那时候才有人说得清它是什么，也才轮得到人看一眼这个名字对
不对。

怎么生出来，看这次交出去的是什么：

- **交的是一次合并**（代码仓库这类项目）—— 交出去的就是这个项目的那个仓库，一个
  项目只有一个（`ProjectForge`），所以**没有什么可判断的，平台自己认**
  （`for_repository`）。谁都不用起名，也没人能在这里多声明一项出来。
- **交的是一份文件或一个地址** —— 这时才真的有得选：一个项目可以既交一份报告又交
  一个网站。所以这条路上保留「沿用一项」（`reuse`）和「声明一项新的」（`claim`）
  这两个动作。

**长**：往后每一次交付沿用同一项（`reuse`）。一版是一次交付：清单上的「第 7 版」
就是第 7 张采纳了的、点名它的卡，所以撤回采纳那一版自己就不在了。

**在不在清单上**，由声明它的那些卡决定，不由这张表里有没有行决定：

- 交付落地过（有采纳了的卡）→ 在，并显示第几版；
- 还没落地但有一张在飞的卡声明了它 → 在，显示「尚未交付」。同一个名字这时已经点
  得到，另一个房间接着交付它用 `reuse`，不会重复新建；
- 两样都没有（声明它的那张卡被驳回、作废了）→ **不在**。什么都没交出去，而且没有
  人正在交，清单就没什么可说的。表里那一行留着：它是这个名字的身份，同一个名字再
  被声明时落回同一行，那一项的历史因此是连着的。

**沿用和新建是两个动作，不是一个参数的两种值**（只在交文件、交地址那条路上；合并
那条路上一个都没有），而且两者收的东西不同：沿用只认
id（`reuse`），新建才写名字（`claim`）。名字是给人读的，写错它不报错 —— `报告` 和
`结题报告` 都是合法名字，按名字认一次手滑就在清单上多一项，而这份清单进每一轮的开
场，错的那一项从此每轮都在场。id 错了则解析不出来或者不在清单上，两种都是当场的报
错；新建那一次把新的 id 返回来，往后照抄它。

**改名、合并、删除是人的动作**，因为「这两项是不是同一个东西」要人判断。改名改的是
这一行，卡指着的是行的 id，所以改完之前的交付照样算这一项的版本。

## 那一句话（`about`）

清单上只有名字的时候，下一次交付判断不了「我做出来的是不是它的新一版」——《数据分
析报告》和手上这份「期中分析.pdf」，光看名字谁也说不准。所以每一项带一句话，说清它
是什么、给谁的。

约束全部来自它唯一的用途，所以它们是可检验的，不是文风建议：

1. **写这样东西本身，不写这一版。** 检验：这句话在第 1 版和第 20 版都得成立。一交
   新版就得改的，就是写错了 —— 也正因为写对了，它不需要每一版重写；要改只有一种情
   况，这东西真的变成了另一样东西。
2. **得跟清单上别的项区分得开。** 检验：换到另一项头上也说得通，就是白写。
3. **说「是什么、给谁」，不说「做了什么」。** 这一版做了什么在卡的 `change_subject`
   上，那里已经有了。把改动标题抄进来是这里最容易犯的错，而它正是清单长成一份改动
   列表的那条老路 —— `clean_about` 当场挡掉它。
4. **一句话。** `ABOUT_MAX` 是硬上限。

前三条机器判不了（判得了就不用人写了），所以它们写在提示词和 cheese 技能里，**连检
验方法一起**：规则会忘，检验方法当场能自查。同一条检验对名字也成立 ——《壳（Shell）
声明与入口渲染》过不了第 1 条。
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.project.models import ProjectArtifact
from app.domain.review.models import AcceptCard, AcceptStatus

#: 名字的长度上限，与 `ProjectArtifact.name` 这一列一致。
NAME_MAX = 200

#: 那一句话的长度上限，与 `ProjectArtifact.about` 这一列一致。一句话就是一句话：
#: 上限本身就是约束，没有它这里会长出小作文，而小作文没人读。
ABOUT_MAX = 80

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
    """清单上的一行：真名、那一句话、当前版本、最近更新。"""

    id: uuid.UUID
    name: str
    #: 交付过几次。0 = 有一张在飞的卡声明了它，但还没有哪一次交付落地。
    version: int
    #: 最近一次交付被采纳的时刻；一次都还没有时为空。
    delivered_at: datetime | None
    #: 这是什么东西、给谁的（见模块开头）。没人写过时是空串。
    about: str = ""
    #: 这一项就是项目那个仓库。合并型的交付落在它身上，不用谁声明。
    delivers_repository: bool = False


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


def clean_about(raw: str | None, *, subject: str | None = None) -> str:
    """那一句话 —— 这是什么东西、给谁的（约束见模块开头）。

    这里只挡机器判得了的两条：太长，和「把改动标题抄过来」。另外三条要人读了才知
    道，它们在提示词和 cheese 技能里，连检验方法一起给。

    抄改动标题这一条单独挡，是因为它不是随便一种写错：清单长成一份改动列表就是这
    么长出来的，而那正是这套东西要治的病。按归一化后比（去掉首尾空白、把中间的空
    白压成一个），不然一个尾随空格就绕过去了。
    """
    about = " ".join((raw or "").split())
    if not about:
        return ""
    if len(about) > ABOUT_MAX:
        raise ValidationError(
            f"这一句话最长 {ABOUT_MAX} 个字，现在有 {len(about)} 个。"
            "它说的是这样东西本身（是什么、给谁的），不是这一版的说明。"
        )
    if subject is not None and about == " ".join(subject.split()):
        raise ValidationError(
            "这一句话和本次改动的标题一模一样。标题说的是这一版做了什么（卡上已经"
            "有了），这一句话说的是这样东西本身 —— 它在第 1 版和第 20 版都得成立。"
        )
    return about


async def for_repository(
    session: AsyncSession, *, project_id: uuid.UUID, project_name: str
) -> ProjectArtifact:
    """项目那个仓库这一项 —— 合并型的交付交出去的就是它。

    **这里没有判断，所以不问任何人。** 一个项目只有一个仓库（`ProjectForge`），
    而一次合并交出去的就是那个仓库往前走一步。此前这个问题是在递卡时问 agent 的，
    而 agent 的视野只有自己那一条分支：五条分支并行，就是五个人各自正确地回答
    「这是一样新东西」，清单于是长成一份改动列表。

    认的是 `delivers_repository` 这一位，不是名字：人随时会把它改成想要的名字，按
    名字找的话改完名的下一次合并就再长出一行 —— 正是要治的那个病。

    第一次合并交付时把它建出来，名字先用项目名（起得不对就改名，改名是无损的：卡
    指着行的 id，之前的版本照样算在它名下）。项目名已经被清单上另一项占着时，认下
    那一项：在一个代码项目里，一个跟项目同名的产物就是有人手工做了这件事。
    """
    found = await session.execute(
        select(ProjectArtifact)
        .where(
            ProjectArtifact.project_id == project_id,
            ProjectArtifact.delivers_repository.is_(True),
        )
        # 取最早的那一行而不是要求「有且只有一行」：一个项目只该有一项为真，但如
        # 果哪天有两项，该发生的是这次交付照常落在先来的那一项上，而不是这个项目
        # 从此一次也交付不出去。
        .order_by(ProjectArtifact.created_at)
    )
    existing = found.scalars().first()
    if existing is not None:
        return existing
    # 名字取项目名，但取不出名字也不能让这次交付递不上去：起名是可以事后改的，
    # 交付这一刻错过了就没了。
    clean = clean_name(project_name if " ".join(project_name.split()) else "这个项目")
    taken = await _by_name(session, project_id=project_id, name=clean)
    if taken is not None:
        taken.delivers_repository = True
        await session.flush()
        return taken
    row = ProjectArtifact(project_id=project_id, name=clean, delivers_repository=True)
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError:
        # 同一个项目的另一条交付在这两句之间把它建出来了。认领它。
        raced = await _by_name(session, project_id=project_id, name=clean)
        if raced is None:
            raise
        raced.delivers_repository = True
        await session.flush()
        return raced
    return row


async def reuse(
    session: AsyncSession, *, project_id: uuid.UUID, artifact_id: str
) -> ProjectArtifact:
    """沿用清单上已经有的那一项 —— 这次交付是它的新一版。

    **点名已有的一项只认 id，不认名字。** 名字是给人读的，而名字写错不报错：
    `报告` 和 `结题报告` 都是合法名字，按名字认的话一次手滑就是清单上多一项。id
    错了要么解析不出来、要么不在这个项目的清单上，两种都是当场的报错。清单每一轮
    都在系统提示里，id 连着名字一起给，照抄即可；新建的那一次会把新的 id 返回来。
    """
    listed = await list_for_project(session, project_id)
    wanted = (artifact_id or "").strip()
    asked = _as_uuid(wanted)
    for row in listed:
        if row.id == asked:
            found = await session.get(ProjectArtifact, row.id)
            if found is not None:
                return found
    same_name = next((row for row in listed if row.name == _unwrap(wanted)), None)
    if same_name is not None:
        raise ValidationError(
            f"artifact 要的是清单上那一项的 id，不是名字。《{same_name.name}》的 "
            f"id 是 {same_name.id}。"
        )
    raise ValidationError(
        f"产物清单上没有 id 为 {wanted} 的那一项。"
        + _what_the_list_has(listed)
        + "确实是一样新做出来的东西，就用 new_artifact 加上它的名字声明它。"
    )


async def claim(
    session: AsyncSession, *, project_id: uuid.UUID, name: str, about: str
) -> ProjectArtifact:
    """声明这次交付做出了一样清单上还没有的东西。

    只有交文件、交地址的交付走得到这里：合并交出去的是项目那个仓库，平台自己认
    （`for_repository`），没有什么可声明的。

    名字已经在清单上就报错：这是「新建」这个动作唯一能替人挡住的事 —— 它挡不住
    「《报告》其实就是《结题报告》」（那要人看），但挡得住「明明是同一项却又声明
    了一次新的」。

    那一句话在这里是必填的：新建是唯一一次非写不可的时机 —— 此后每一版都不必重写
    （写对了的那句话不会过期），而清单上一项没有它，下一次交付就又只能看着名字猜。
    """
    if not about:
        raise ValidationError(
            "新声明一项产物要用一句话说清它是什么、给谁的 —— 清单上只有名字的话，"
            "下一次交付判断不了「我做的是不是它的新一版」。写这样东西本身，不写这"
            "一版做了什么：这句话在第 1 版和第 20 版都得成立。"
        )
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
        found.about = about
        await session.flush()
        return found
    row = ProjectArtifact(project_id=project_id, name=clean, about=about)
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


async def describe(
    session: AsyncSession, artifact: ProjectArtifact, *, about: str
) -> ProjectArtifact:
    """把那一句话换成新的 —— 空的表示这次不改。

    每一版都重写是白费力气：写对了的那句话说的是这样东西本身，交一版新的不会让它
    变得不对。所以沿用一项时它是可给可不给的，给了就以新的为准 —— 该改的那一种情
    况是这东西真的变成了另一样东西，那时候写的人正好就在现场。
    """
    if not about or about == artifact.about:
        return artifact
    artifact.about = about
    await session.flush()
    return artifact


async def get_or_404(
    session: AsyncSession, *, project_id: uuid.UUID, artifact_id: uuid.UUID
) -> ProjectArtifact:
    """这个项目清单上的那一行。别的项目的一行在这里就是不存在。"""
    row = await session.get(ProjectArtifact, artifact_id)
    if row is None or row.project_id != project_id:
        raise NotFoundError("产物清单上没有这一项")
    return row


async def rename(
    session: AsyncSession, artifact: ProjectArtifact, *, name: str
) -> ProjectArtifact:
    """给这一项换个名字。

    改的是这一行，而卡指着的是行的 id —— 所以改完之前的每一次交付照样算这一项的
    版本。这也是「芝士 把名字起错了」的正解：改名，而不是删掉重来。

    重名不自动合并：两项叫同一个名字，「它们是不是同一个东西」是人的判断，而这里
    只知道用户在改字。要合并就用合并 —— 那个动作会把另一项的交付都算过来。
    """
    clean = clean_name(name)
    if clean == artifact.name:
        return artifact
    taken = await _by_name(session, project_id=artifact.project_id, name=clean)
    if taken is not None:
        raise ValidationError(
            f"《{clean}》已经是清单上另一项了。它们确实是同一个东西，就把这一项"
            "合并到它；不是的话，换一个说得出区别的名字。"
        )
    artifact.name = clean
    await session.flush()
    return artifact


async def merge(
    session: AsyncSession, *, source: ProjectArtifact, target: ProjectArtifact
) -> ProjectArtifact:
    """两项其实是同一个东西：把 `source` 的交付都算到 `target` 上，然后扔掉它。

    交付记录在卡上，所以合并就是把那些卡改指向留下来的这一项 —— 版本数随之变成两
    边加起来，不需要另外搬什么。方向由人定：留哪个名字是他的判断。
    """
    if source.id == target.id:
        raise ValidationError("不能把一项合并到它自己")
    if source.project_id != target.project_id:
        raise ValidationError("只能在同一个项目的清单里合并")
    await session.execute(
        update(AcceptCard)
        .where(AcceptCard.artifact_id == source.id)
        .values(artifact_id=target.id)
    )
    await session.delete(source)
    await session.flush()
    return target


async def delete(session: AsyncSession, artifact: ProjectArtifact) -> None:
    """把这一项从清单上去掉。

    声明过它的那些卡留在原处，只是不再指向任何一项（外键 SET NULL）：那些交付确实
    发生过，改写它们等于往已经落地的历史上安一个别的声明。清单上不留这一项，是用
    户说的「它本来就不该是一项」。
    """
    await session.delete(artifact)
    await session.flush()


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
            id=row.id,
            name=row.name,
            version=landed,
            delivered_at=delivered_at,
            about=row.about,
            delivers_repository=row.delivers_repository,
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
        about=artifact.about,
        delivers_repository=artifact.delivers_repository,
    )


@dataclass(frozen=True, slots=True)
class ArtifactVersion:
    """这一项产物的第 N 版 —— 就是第 N 张采纳了的卡 (#1085 结论五)。

    版号不在任何一张表上：按采纳时间排开数下来就是它。所以撤回一次采纳，它后面那
    几版的号自己往前挪，而不需要有谁记得去改。
    """

    number: int
    card_id: uuid.UUID
    #: 这次交付改了什么 —— 卡上那句 Conventional Commit 标题。
    subject: str | None
    delivered_at: datetime | None
    decided_by: str | None
    #: 交出去的是什么形态：file / link / merge，或者 None —— 交付物落地之前递的那
    #: 几张卡没有这个字段，那几版没有可下载的东西。
    kind: str | None
    filename: str | None
    url: str | None
    revision: str | None


async def versions(
    session: AsyncSession, artifact_id: uuid.UUID
) -> list[ArtifactVersion]:
    """这一项交付过的每一版，第一版在前。"""
    found = await session.execute(
        select(AcceptCard)
        .where(
            AcceptCard.artifact_id == artifact_id,
            AcceptCard.status == AcceptStatus.accepted,
        )
        .order_by(AcceptCard.decided_at, AcceptCard.created_at)
    )
    return [
        ArtifactVersion(
            number=number,
            card_id=card.id,
            subject=card.change_subject,
            delivered_at=card.decided_at,
            decided_by=card.decided_by,
            kind=card.deliverable_kind.value if card.deliverable_kind else None,
            filename=card.deliverable_name,
            url=card.deliverable_url,
            revision=card.pr_head_sha,
        )
        for number, card in enumerate(found.scalars().all(), start=1)
    ]


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
    """清单现在有什么，连 id 一起 —— 读这句话的是一个下一轮就要重递的 agent，
    只说「没有这一项」等于让它再猜一轮。"""
    if not listed:
        return "这个项目还没有交付过任何东西，清单是空的。"
    rows = "；".join(f"《{row.name}》 id={row.id}" for row in listed)
    return f"清单上现在有：{rows}。"


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
