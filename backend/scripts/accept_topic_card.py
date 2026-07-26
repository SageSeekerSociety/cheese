"""Accept the pending card on a topic and report where the work landed upstream.

采纳 IS the merge: this runs the same path the UI button does (merge the topic
branch into the project's base, then propagate that merge to the upstream repo)
and prints the outcome the accept records, so a CI run can show whether the
upstream actually advanced:

    STATUS=accepted|conflict|none
    NOTE=<what the accept recorded about the upstream push>

Usage: python accept_topic_card.py <topic_id>
"""

import asyncio
import sys
import uuid

TOPIC_ID = uuid.UUID(sys.argv[1])


async def main() -> int:
    from sqlalchemy import select

    from app.core.db import async_session_factory
    from app.domain.review.models import AcceptCard, AcceptStatus
    from app.domain.review.services import AcceptService

    async with async_session_factory() as session:
        card = (
            (
                await session.execute(
                    select(AcceptCard)
                    .where(AcceptCard.topic_id == TOPIC_ID)
                    .where(AcceptCard.status == AcceptStatus.pending)
                    .order_by(AcceptCard.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
        if card is None:
            print("STATUS=none")
            print("NOTE=no pending card on this topic")
            return 1
        decided = await AcceptService(session).accept(
            card_id=card.id, decided_by="andy"
        )
        await session.commit()
        print(f"STATUS={decided.status.value}")
        print(f"NOTE={(decided.note or '').replace(chr(10), ' ')}")
    return 0


sys.exit(asyncio.run(main()))
