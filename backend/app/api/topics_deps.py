"""话题这一面各路由共用的东西：房间那道门、时刻解析、行形状，以及几个领域别名。

从 ``app/api/routes/topics.py`` 与 ``app/api/routes/topics_rows.py`` 搬来，逐行未改。

搬家的理由和 ``task_deps.py`` 一样：判据不是「谁在 import」，是「这个东西属于谁」。

- ``_actor_in_place`` 是「这儿的来客是谁、能不能进」—— 先解身份，再对房间的
  花名册。十一个 ``topics_*`` 模块里的大半、加上 ``addresses`` 与 ``living_docs``，
  都从这里过；它不属于任何一个话题路由。``_parse_moment`` 同理，``topics_reads``
  与 topics.py 读的是同一种 ISO 时刻。
- ``TopicRow`` / ``CardRow`` 是这一层从 ORM 行上读到的形状，纯类型（搬自
  ``routes/topics_rows.py``，那个文件只剩这两个协议，随之删除）。

**领域别名。** ``Block``、``BlockKind``、``BlockOut``、``BlockRepository``、
``ProjectRepository``、``UsageRepository`` 以前由 ``routes/topics.py`` 转发，各
``topics_*`` 因此从它那里拿 —— 这么做是为了不让那些模块直接 import
``app.domain.block.*``。转发本身正是 C4
不许的那种形状：一个路由成了另一个路由的依赖，它就不能被搬动、拆分或改名而不惊动
引用方。所以转发点挪到路由包外面 —— 这里是 ``app/api`` 下的一处共享依赖模块，不属
于任何领域，谁都可以依赖它。

**代价记在两本账上，两笔都是「同一笔债换了发起方」而不是新债**，形状与
``topics_tasks`` / ``projects_agents`` 那两次切片相同：

- ``.importlinter`` 的 C2 多一行 ``app.api.topics_deps`` 作源（它 import 了
  ``app.domain.block.models``），外加一行冻结边；
- ``tests/unit/test_domain_import_guard.py`` 的 ``_EXEMPT`` 多三行（block /
  project / usage 三个领域的 repositories），发起方从那个路由模块换成这个模块。

之所以走这层转发而不是让每个引用方自己去摸领域，是为了让债按「发起方模块」记账：
六个模块各摸一次 ``block.repositories`` 是六行，经由这里只有一行。还法与别处相同
—— 改成调对方的 service，然后删行。
"""

import uuid
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Mapped

from app.api.auth import ActorResolver
from app.core.errors import ValidationError
from app.core.sentences import say
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.identity.actor import Actor
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task import presentation
from app.domain.room_task.place import Place
from app.domain.usage.repositories import UsageRepository

__all__ = [
    "Block",
    "BlockKind",
    "BlockOut",
    "BlockRepository",
    "CardRow",
    "ProjectRepository",
    "TopicRow",
    "UsageRepository",
]


async def _actor_in_place(resolver: ActorResolver, place: Place) -> Actor:
    """Who is calling here, and whether they may be — identity, then the
    room's roster."""
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    return actor


def _parse_moment(raw: object) -> datetime | None:
    """一个可选的 ISO-8601 时刻；空串和缺席是一回事。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError(say("sinceUntilIso")) from None
    # 不带时区的按 UTC 读：否则它和 now() 相减时 naive/aware 直接抛，而调用方
    # 只写了个「2026-09-06」也得能用。
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class TopicRow(presentation.TopicSignals, Protocol):
    """这一层从一个房间行上读到的形状。

    结构协议，不是 ORM 类：它手上的每一行都来自同域的 `TopicService`（ORM 行）。成员
    按模型的原样声明 `Mapped[...]`（`presentation.TopicSignals`），pyright 才拿得它
    比得过那一行；`TopicOut.model_validate` 只读属性、不认类，所以这几位就是这里需要
    的全部。
    """


class CardRow(presentation.CardRow, Protocol):
    """这一层从一张验收卡上读到的形状 —— ORM 行，按 `AcceptCard` 的原样声明 `Mapped`。

    行来自 `AcceptService`。除了卡面要的五个信号（`presentation.CardRow`）之外，这
    里还读它挂在哪、骑哪个 PR、什么时候建的。和 `presentation.CardRow` 一样，成员是
    `Mapped[...]` 而不是普通标量 —— pyright 比的是类成员，ORM 行的属性是 `Mapped`
    描述符，读出来才解包成值。
    """

    id: Mapped[uuid.UUID]

    task_id: Mapped[uuid.UUID | None]

    topic_id: Mapped[uuid.UUID]

    reviewer_handle: Mapped[str]

    decided_at: Mapped[datetime | None]

    note: Mapped[str]

    gate_passed_at: Mapped[datetime | None]

    gate_output: Mapped[str]

    pr_number: Mapped[int | None]

    pr_url: Mapped[str | None]

    created_at: Mapped[datetime]
