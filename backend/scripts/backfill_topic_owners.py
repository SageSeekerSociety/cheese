"""Backfill an OWNER onto topics that were born without one (话题拥有者为空).

`seed()` deliberately refuses to make 芝士 a topic's owner, and topic-create used
to pass `created_by` straight through — so every room 芝士 opened, and every room
created by a caller whose token didn't resolve, landed with 芝士 as its only
member and no owner at all. `split_to_subtopic` defaults a child's owner to its
PARENT's owner, so one ownerless room made every sub-topic under it ownerless
too. On the dogfood project that had reached 89 of 123 topics — none of which any
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
     the room belongs to (it is what a person reading the transcript would say)

A topic that matches no rung is left alone and reported, not guessed at.

Idempotent: topics that already have an owner are skipped, and `_ensure_member`
never duplicates a row. Read-only unless `--apply` is passed.

    uv run python scripts/backfill_topic_owners.py            # dry run
    uv run python scripts/backfill_topic_owners.py --apply
"""

import asyncio
import sys
import uuid

from sqlalchemy import select

from app.core.db import async_session_factory
from app.domain.block.models import AuthorType, Block
from app.domain.project.models import Project
from app.domain.topic.models import Topic, TopicMembership, TopicRole
from app.domain.topic_membership.services import CHEESE_HANDLE, TopicMemberService

Owners = dict[uuid.UUID, str]


async def _current_owners(session) -> Owners:
    rows = (
        await session.execute(
            select(TopicMembership.topic_id, TopicMembership.member_handle).where(
                TopicMembership.role == TopicRole.owner
            )
        )
    ).all()
    return {topic_id: handle for topic_id, handle in rows}


async def _first_human_author(session, topic_id: uuid.UUID) -> str | None:
    return (
        await session.execute(
            select(Block.author)
            .where(Block.topic_id == topic_id, Block.author_type == AuthorType.human)
            .order_by(Block.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()


def _is_human(handle: str | None) -> bool:
    # A numeric handle is the degraded-token artifact (`sub` used as the handle);
    # it matches no roster and no project membership, so it is not an owner.
    return bool(handle) and handle != CHEESE_HANDLE and not handle.isdigit()


def _inherited_owner(
    topic: Topic, topics: dict[uuid.UUID, Topic], owners: Owners
) -> str | None:
    """The nearest ancestor's owner. Guards against a parent cycle so a corrupt
    tree loops forever in nobody's console."""
    seen: set[uuid.UUID] = {topic.id}
    parent_id = topic.parent_id
    while parent_id is not None and parent_id not in seen:
        if (handle := owners.get(parent_id)) is not None:
            return handle
        seen.add(parent_id)
        parent = topics.get(parent_id)
        if parent is None:
            break
        parent_id = parent.parent_id
    return None


async def backfill(*, apply: bool) -> int:
    async with async_session_factory() as session:
        members = TopicMemberService(session)
        topics = list((await session.execute(select(Topic))).scalars().all())
        by_id = {t.id: t for t in topics}
        owners = await _current_owners(session)
        project_owner = {
            p.id: p.owner_handle
            for p in (await session.execute(select(Project))).scalars().all()
        }

        # Parents before children: repairing a room first lets its sub-topics
        # inherit the freshly-decided owner in the SAME pass (rung 2).
        ordered = sorted(topics, key=lambda t: (t.parent_id is not None, t.created_at))

        repaired = 0
        unresolved: list[Topic] = []
        for topic in ordered:
            if topic.id in owners:
                continue
            handle = None
            if _is_human(topic.created_by):
                handle = topic.created_by
            if handle is None:
                handle = _inherited_owner(topic, by_id, owners)
            if handle is None and _is_human(project_owner.get(topic.project_id)):
                handle = project_owner[topic.project_id]
            if handle is None:
                candidate = await _first_human_author(session, topic.id)
                handle = candidate if _is_human(candidate) else None
            if handle is None:
                unresolved.append(topic)
                continue

            owners[topic.id] = handle
            repaired += 1
            print(f"  {topic.kind:9} {handle:16} <- {topic.title[:48]}")
            if apply:
                await members.seed(topic.id, owner_handle=handle)

        if apply:
            await session.commit()

        print(f"\n{'applied' if apply else 'would repair'}: {repaired} topic(s)")
        if unresolved:
            print(f"no owner could be determined for {len(unresolved)} topic(s):")
            for topic in unresolved:
                print(f"  {topic.id} {topic.title[:48]}")
        return repaired


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    if not apply:
        print("DRY RUN — pass --apply to write\n")
    asyncio.run(backfill(apply=apply))
