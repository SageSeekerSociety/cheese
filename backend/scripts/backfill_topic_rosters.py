"""Backfill topic rosters for topics created before roster-seeding was wired in.

Older topics (and demo topics built via raw ORM) landed with empty
`topic_memberships`, so a topic showed "0 人 + 芝士" — or nothing. This walks
every topic and seeds the roster the same way topic-create now does:

  - ROOT topic (总览/项目本体): everyone on the project roster. 芝士 is not seeded
    here — the project's own agent is seated where that agent is created
    (`AgentInstanceService.materialize_default`), the one place that decides
    「这个项目的芝士是谁」.
  - other topics: the creator (created_by) as owner + 芝士.

Idempotent — `_ensure_member` skips rows that already exist, so re-running never
duplicates. A non-root topic with no `created_by` still gets 芝士 (matches
seed()).
"""

import asyncio

from sqlalchemy import select

from app.core.db import async_session_factory
from app.domain.membership.roster import roster
from app.domain.topic.models import Topic, TopicKind
from app.domain.topic_membership.services import TopicMemberService


async def backfill() -> None:
    async with async_session_factory() as s:
        members = TopicMemberService(s)
        topics = list((await s.execute(select(Topic))).scalars().all())
        for t in topics:
            if t.kind == TopicKind.root:
                # 「这个项目里有谁」只有一个读法（``membership.roster.roster``），
                # 和 ``ProjectService.create`` 喂给 ``seed_root`` 的是同一份。队友那
                # 几行不用在这里滤掉：``_ensure_people`` 本来就只收人。
                handles = [m.handle for m in await roster(s, t.project_id)]
                await members.seed_root(
                    t.id, owner_handle=t.created_by, member_handles=handles
                )
                who = f"root: {handles or '(no project members)'}"
            else:
                await members.seed(t.id, owner_handle=t.created_by)
                who = f"{t.created_by or '(no creator)'} + cheese"
            print(f"backfilled {t.kind} '{t.title}' -> {who}")
        await s.commit()


if __name__ == "__main__":
    asyncio.run(backfill())
