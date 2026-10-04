"""Authenticated Ask creation's exact active-work provenance."""

import logging

from app.domain.agent.repositories import AgentTurnRepository
from app.domain.identity.handles import names_a_person

logger = logging.getLogger(__name__)


def refused(reason: str, topic_id, seat, **facts) -> None:
    """Say which check refused a question: every refusal reads the same to the
    agent (403 askOriginUnknown), so this line is the only place they differ."""
    detail = " ".join(f"{key}={value}" for key, value in facts.items())
    logger.info(
        "ask origin refused: %s topic=%s seat=%s %s", reason, topic_id, seat, detail
    )


async def ask_origin(chat, project_id, topic_id, author):
    """Return native provenance and answer addressee values, or no active origin.

    Caller authenticates the seated author. This read owns its DB session and
    never commits or starts native work; it rechecks live ownership after I/O.
    """
    states = [
        state
        for (topic, _), state in chat._hook_work.items()
        if topic == topic_id
        and state.project_id == project_id
        and state.acting_agent == author
        and state.work_id in chat._active_turn_ids.get(topic_id, ())
    ]
    if len(states) != 1:
        refused(
            "no live turn" if not states else "several live turns",
            topic_id,
            author,
            works=sorted(str(state.work_id) for state in states),
            active=sorted(
                str(work) for work in chat._active_turn_ids.get(topic_id, ())
            ),
        )
        return None
    state = states[0]
    origin = await chat._compute.ask_origin(
        project_id, topic_id, state.agent_instance_handle
    )
    if origin is None:
        # The session side names its own reason (`RoomSessions.ask_origin`).
        refused("session has no matching work", topic_id, author, work=state.work_id)
        return None
    if (
        origin["work_id"] != str(state.work_id)
        or chat._hook_work.get((topic_id, state.work_id)) is not state
        or state.work_id not in chat._active_turn_ids.get(topic_id, ())
    ):
        refused(
            "live turn changed while reading",
            topic_id,
            author,
            work=state.work_id,
            session_work=origin["work_id"],
        )
        return None
    async with chat.session_factory() as session:
        turn = await AgentTurnRepository(session).get(state.work_id)
        if turn is None or turn.topic_id != topic_id or turn.stopped_at is not None:
            refused(
                "turn row missing or closed",
                topic_id,
                author,
                work=state.work_id,
                stopped_at=turn.stopped_at if turn is not None else None,
            )
            return None
        asked = turn.author if names_a_person(turn.author) else None
        task_id = str(turn.task_id) if turn.task_id else None
    # Delivery addresses the authenticated roster seat; native lookup used the
    # project's session handle above.
    return {
        **origin,
        "recipient_handle": author,
        "asked_by": author,
        "asked": asked,
        "task_id": task_id,
    }
