"""频道的写 —— 建行、加锁、开一间私聊。

从 `repositories.py` 拆出来的一段。`lock` 和 `lock_all_in_project` 是给归档用的：
归档要冻结整棵话题树，所以它取的是 KEY SHARE 锁，让只带外键的挂钩写入照样能过。

`TopicRepository` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select
from sqlalchemy.sql import Select

from app.domain.topic.models import (
    Topic,
    TopicKind,
    TopicMembership,
    new_private_chat,
    private_chat,
)
from app.domain.topic.repositories.statements import _tasks

if TYPE_CHECKING:
    from app.domain.topic import repositories as pkg


async def add(
    repo: pkg.TopicRepository,
    *,
    project_id: uuid.UUID,
    number: int,
    environment: dict,
    title: str,
    parent_id: uuid.UUID | None = None,
    kind: TopicKind = TopicKind.topic,
    created_by: str | None = None,
    upgraded_from_block_id: uuid.UUID | None = None,
) -> Topic:
    """``number`` and ``environment`` are the project's to hand out: the counter
    row and the settings snapshot belong to `project`, and reading them from
    here made the two packages a cycle."""
    topic = Topic(
        project_id=project_id,
        number=number,
        environment=environment,
        title=title,
        parent_id=parent_id,
        kind=kind,
        created_by=created_by,
        upgraded_from_block_id=upgraded_from_block_id,
    )
    repo._session.add(topic)
    await repo._session.flush()
    await repo._session.refresh(topic)
    return topic


async def lock(repo: pkg.TopicRepository, topic_id: uuid.UUID) -> Topic | None:
    return (
        await repo._session.scalars(
            select(Topic)
            .where(Topic.id == topic_id)
            # Serialize execution admission with archival while allowing
            # hook writes whose foreign keys only take KEY SHARE.
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()


async def lock_all_in_project(
    repo: pkg.TopicRepository, project_id: uuid.UUID
) -> list[Topic]:
    """Every topic of the project, private chats included, locked the way
    :meth:`lock` locks one — what archiving the whole project walks."""
    return list(
        (
            await repo._session.scalars(
                select(Topic)
                .where(Topic.project_id == project_id)
                .order_by(Topic.created_at)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def lock_open_tasks(repo: pkg.TopicRepository, room_id: uuid.UUID) -> None:
    """Lock the room's open tasks' rows, in id order, the way closing them
    will (FOR NO KEY UPDATE).

    Lock order: a task's row before the block numbering of its conversation.
    Everything that writes into a task's conversation and decides on the
    task does it in that order — a message read against its task
    (`agent.turn.store.human`), a task closed with a line said in it
    (`room_task.closing`). Archiving says things in its tasks' conversations
    (a card's notice) before it closes them, so it takes these rows first;
    taken later, archive and a send each hold what the other waits for."""
    await repo._session.execute(
        select(_tasks.c.id)
        .where(_tasks.c.room_id == room_id, _tasks.c.status == "open")
        .order_by(_tasks.c.id)
        .with_for_update(key_share=True)
    )


async def get_or_create_private(
    repo: pkg.TopicRepository,
    *,
    project_id: uuid.UUID,
    owner: str,
    peer: str,
    title: str,
    environment: dict,
) -> Topic:
    """The 1:1 private conversation between ``owner`` and ``peer`` in this
    project, created with ``title`` if it does not exist yet.

    Who the two are — two people in canonical order, or a person and an AI
    teammate's seat — is the service's decision; here a DM is two handles.

    Found by its two seats, because that is where they live (结论 19): the
    private room of this project that seats both handles. Two memberships,
    each asked for on its own, so which of the two opens the conversation
    does not decide whether it is found.

    名册长到三席的房间也认，认的是同一间。多出一席这间房确实答不出对面是谁，
    但那件事有它自己的出处（`TopicMemberService.private_seats` 答 None，调用方
    退回项目默认那位），而这里只答「再打开的是不是同一间」。不认它就等于每次
    打开都新开一间：私聊不进话题树（`TopicService.list_for_project` 把它们藏
    起来），角标又被 `private_unread_counts` 的两席闸滤掉，旧那间房在界面上
    一个入口都没有，里面的对话就此找不回来。

    同时匹配上好几间时次序是定死的：还是两席的那间先，然后按建的时间。没有
    `order_by` 的 `.first()` 返回哪一间没有定数，同一个人两次打开可能落进两间
    不同的房。两席那一项不是把闸装回来，是在好几间都坐着这两位的时候挑出哪一
    间才是这两位的 DM：第三个人被加进 A 和 B 的那间房之后，A 打开与他的私聊，
    两间都匹配得上，而只有一间是 A 和他的。
    """

    def seats(handle: str) -> Select[tuple[uuid.UUID]]:
        return select(TopicMembership.topic_id).where(
            TopicMembership.member_handle == handle
        )

    seat_count = (
        select(func.count())
        .select_from(TopicMembership)
        .where(TopicMembership.topic_id == Topic.id)
        .scalar_subquery()
    )
    stmt = (
        select(Topic)
        .where(
            Topic.project_id == project_id,
            private_chat(),
            Topic.id.in_(seats(owner)),
            Topic.id.in_(seats(peer)),
        )
        .order_by(case((seat_count == 2, 0), else_=1), Topic.created_at)
    )
    existing = (await repo._session.scalars(stmt)).first()
    if existing is not None:
        return existing
    topic = new_private_chat(
        project_id=project_id,
        environment=environment,
        title=title,
        kind=TopicKind.topic,
        created_by=owner,
    )
    repo._session.add(topic)
    await repo._session.flush()
    await repo._session.refresh(topic)
    return topic
