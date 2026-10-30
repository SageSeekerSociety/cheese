"""话题这一面各路由共用的东西：房间那道门、时刻解析，以及几个领域别名。

从 ``app/api/routes/topics.py`` 搬来，逐行未改。

搬家的理由和 ``task_deps.py`` 一样：判据不是「谁在 import」，是「这个东西属于谁」。
``_actor_in_place`` 是「这儿的来客是谁、能不能进」—— 先解身份，再对房间的花名册。
十一个 ``topics_*`` 模块里的大半、加上 ``addresses`` 与 ``living_docs``，都从这里过；
它不属于任何一个话题路由。``_parse_moment`` 同理，``topics_reads`` 与 topics.py 读的
是同一种 ISO 时刻。

行的形状（``TopicRow`` / ``CardRow``）不在这里，在 ``app/api/topics_rows.py``：那两个
协议是纯类型，先一步由另一笔改动搬出了 ``routes/``。

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

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver
from app.api.doc_access import require_role
from app.auth.project_roles import standing
from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import say
from app.domain.agent_instance.adapters import project_team
from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut, reply_preview
from app.domain.identity.actor import Actor
from app.domain.membership.roster import Member
from app.domain.project.repositories import ProjectRepository
from app.domain.project_permissions import Permission, Seat
from app.domain.room_task.place import Place
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.repositories import UsageRepository

__all__ = [
    "Block",
    "BlockKind",
    "BlockOut",
    "BlockRepository",
    "ProjectRepository",
    "UsageRepository",
    "reply_preview",
]


async def _actor_in_place(resolver: ActorResolver, place: Place) -> Actor:
    """Who is calling here, and whether they may be — identity, then the
    room's roster."""
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    # The place already carries the room row: hand it in so the door does not
    # look the conversation's room up and read that row again.
    await resolver.authorize_topic(actor, project_id=place.project_id, room=place.room)
    return actor


async def require_person_speaks(
    db: AsyncSession, actor: Actor, place: Place
) -> tuple[Member, ...] | None:
    """A person saying something here, on the terms every message of theirs
    is said on — a message, or a checklist they post.

    An archived channel is read, not spoken in: its main line, its 支线 and
    its tasks alike; unarchiving it is how it is spoken in again. A channel's
    main line is its members speaking: anyone in the project reads it and
    answers in a 支线 under it, and speaking in the main line is joining first.
    And their role has to speak at all.

    Returns the project roster when asking that read it (综合's main line is
    the project's members), for the message's writer to use instead of reading
    it again; ``None`` when it was not read.
    """
    if place.room.status == "archived":
        raise ForbiddenError(say("roomArchivedUnarchiveFirst"))
    members = TopicMemberService(db)
    if place.inner_id is None and not await members.may_speak(
        place.room, actor.handle, team=project_team
    ):
        raise ForbiddenError(say("channelJoinToPost"))
    await require_role(db, actor, place.project_id, Permission.speak)
    await _require_channel_speaker(db, actor, place)
    return members.roster_read


async def _require_channel_speaker(
    db: AsyncSession, actor: Actor, place: Place
) -> None:
    """A channel may let only some roles speak in its main line (#4795): the
    owner always may, and with 支线 left open anyone answers under a message,
    and in a task, there."""
    limited = place.room.speaker_roles
    if limited is None or (
        place.inner_id is not None and place.room.thread_replies_open
    ):
        return
    held = await standing(db, place.project_id, actor.handle)
    if held is None or (held.seat is not Seat.owner and held.role not in limited):
        raise ForbiddenError(say("channelSpeakersOnly"))


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
