"""Backfill an OWNER onto topics that were born without one (话题拥有者为空).

`seed()` deliberately refuses to make 芝士 a topic's owner, and topic-create used
to pass `created_by` straight through — so every room 芝士 opened, and every room
created by a caller whose token didn't resolve, landed with 芝士 as its only
member and no owner at all. `split_to_subtopic` defaults a child's owner to its
PARENT's owner, so one ownerless room made every sub-topic under it ownerless
too. On the dogfood project that had reached 96 of 149 topics — none of which any
human could manage, because `can_manage_roster` requires owner/admin.

`TopicService.create` now resolves an owner up a fallback ladder; this script
applies the same ladder to the topics that predate it, plus one extra rung that
only makes sense for existing data:

  1. `created_by`, when it names a real human (never 芝士)
  2. the parent topic's owner — walked all the way up, so a chain of ownerless
     rooms is repaired top-down in one pass
  3. the project's owner
  4. the first HUMAN who actually posted in the topic — for a room 芝士 opened
     inside a project that itself has no owner, this is the only evidence of who
     the room belongs to (it is what a person reading the transcript would say).
     Never applied to the ROOT topic: guessing an owner for 项目本体 makes that
     guess cascade down every room in the project via rung 2

Every candidate must survive :class:`PersonCheck`: an owner who cannot act is no
owner at all. Two kinds of junk fail it, both seen in live data:

  - ``"system"``, which authors ``author_type=human`` blocks but is nobody
  - a numeric handle (``"470"``), the degraded-token artifact where the int user
    PK was used as the handle. It is resolved back to the real username first
    (same repair as ``ActorResolver._recover_numeric_handle``) and only kept if
    that user is actually on the project

The same test is applied to owners that ALREADY exist, so a topic sitting on a
dead owner (and every sub-topic that inherited it) is repaired too, not skipped.
A topic that matches no rung is left alone and reported, not guessed at.

Idempotent — `_ensure_member` never duplicates a row. Read-only unless
`--apply` is passed.

    uv run python scripts/backfill_topic_owners.py            # dry run
    uv run python scripts/backfill_topic_owners.py --apply
"""

import asyncio
import sys
import uuid
from collections import Counter

from sqlalchemy import select

from app.core.db import async_session_factory
from app.domain.block.models import AuthorType, Block
from app.domain.membership.repositories import MemberRepository
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicKind, TopicMembership, TopicRole
from app.domain.topic_membership.services import CHEESE_HANDLE, TopicMemberService
from app.domain.user.repositories import UserRepository

Owners = dict[uuid.UUID, str]


class PersonCheck:
    """Is this handle someone who could actually own a room here?

    Membership is per-project, so the check is too. Results are cached — a
    backfill asks about the same few handles hundreds of times.
    """

    def __init__(self, session) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._members = MemberRepository(session)
        self._cache: dict[tuple[str, uuid.UUID], str | None] = {}

    async def resolve(self, handle: str | None, project_id: uuid.UUID) -> str | None:
        """The real handle behind ``handle``, or None if it is not a person on
        this project."""
        if not handle or handle == CHEESE_HANDLE:
            return None
        key = (handle, project_id)
        if key not in self._cache:
            self._cache[key] = await self._resolve_uncached(handle, project_id)
        return self._cache[key]

    async def _resolve_uncached(self, handle: str, project_id: uuid.UUID) -> str | None:
        if handle.isdigit():
            # Degraded token: the int PK leaked in as the handle. Recover the
            # username — a numeric handle matches no roster and no membership,
            # so propagating it would just mint another dead owner.
            user = await self._users.get_by_id(int(handle))
            if user is None:
                return None
            handle = user.username
        if await self._members.get(project_id=project_id, user_handle=handle):
            return handle
        project = await self._session.get(Project, project_id)
        return (
            handle if project is not None and project.owner_handle == handle else None
        )


async def _current_owners(session, people: PersonCheck, topics: list[Topic]) -> Owners:
    """Existing owners, minus the ones that aren't real people — those topics
    need repairing just as much as the ownerless ones."""
    rows = (
        await session.execute(
            select(TopicMembership.topic_id, TopicMembership.member_handle).where(
                TopicMembership.role == TopicRole.owner
            )
        )
    ).all()
    project_of = {t.id: t.project_id for t in topics}
    owners: Owners = {}
    for topic_id, handle in rows:
        if (project_id := project_of.get(topic_id)) is None:
            continue
        if (real := await people.resolve(handle, project_id)) is not None:
            owners[topic_id] = real
    return owners


async def _first_human_author(session, topic_id: uuid.UUID) -> list[str]:
    """Human block authors in the topic, oldest first (deduped, order kept)."""
    rows = (
        (
            await session.execute(
                select(Block.author)
                .where(
                    Block.topic_id == topic_id,
                    Block.author_type == AuthorType.human,
                )
                .order_by(Block.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(dict.fromkeys(rows))


def _inherited_owner(
    topic: Topic, topics: dict[uuid.UUID, Topic], owners: Owners
) -> str | None:
    """The nearest ancestor's owner. Guards against a parent cycle so a corrupt
    tree doesn't loop forever in nobody's console."""
    seen: set[uuid.UUID] = {topic.id}
    parent_id = topic.parent_id
    while parent_id is not None and parent_id not in seen:
        if (handle := owners.get(parent_id)) is not None:
            return handle
        seen.add(parent_id)
        if (parent := topics.get(parent_id)) is None:
            break
        parent_id = parent.parent_id
    return None


async def backfill(*, apply: bool) -> int:
    async with async_session_factory() as session:
        members = TopicMemberService(session)
        people = PersonCheck(session)
        topics = list((await session.execute(select(Topic))).scalars().all())
        by_id = {t.id: t for t in topics}
        owners = await _current_owners(session, people, topics)
        project_owner = {
            p.id: p.owner_handle
            for p in (await session.execute(select(Project))).scalars().all()
        }

        # Parents before children: repairing a room first lets its sub-topics
        # inherit the freshly-decided owner in the SAME pass (rung 2).
        ordered = sorted(topics, key=lambda t: (t.parent_id is not None, t.created_at))

        repaired = 0
        why_count: Counter[str] = Counter()
        unresolved: list[Topic] = []
        for topic in ordered:
            if topic.id in owners:
                continue
            pid = topic.project_id
            handle = await people.resolve(topic.created_by, pid)
            why = "创建者"
            if handle is None:
                handle, why = _inherited_owner(topic, by_id, owners), "父话题 owner"
            if handle is None:
                handle = await people.resolve(project_owner.get(pid), pid)
                why = "项目 owner"
            if handle is None and topic.kind != TopicKind.root:
                # NOT for the root topic (项目本体): its owner is the project's
                # owner by definition, and guessing one from the transcript
                # would then cascade down the WHOLE tree as rung 2 — one stale
                # first-poster silently becoming the owner of every room in the
                # project. A root with no project owner is reported instead.
                why = "首个真人发言"
                for candidate in await _first_human_author(session, topic.id):
                    if (handle := await people.resolve(candidate, pid)) is not None:
                        break
            if handle is None:
                unresolved.append(topic)
                continue

            owners[topic.id] = handle
            repaired += 1
            why_count[why] += 1
            print(f"  {topic.kind:9} {handle:16} [{why}]  {topic.title[:44]}")
            if apply:
                await members.seed(topic.id, owner_handle=handle)

        if apply:
            await session.commit()

        print(f"\n{'applied' if apply else 'would repair'}: {repaired} topic(s)")
        print(f"  按判据: {dict(why_count)}")
        if unresolved:
            print(f"\nno owner could be determined for {len(unresolved)} topic(s):")
            for topic in unresolved:
                print(f"  {topic.id} {topic.title[:48]}")
        return repaired


if __name__ == "__main__":
    should_apply = "--apply" in sys.argv
    if not should_apply:
        print("DRY RUN — pass --apply to write\n")
    asyncio.run(backfill(apply=should_apply))
