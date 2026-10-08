"""`cheese_ask`: an agent asks, in the room, and ends its turn.

The question is written as ordinary messages with quick replies
(`block.questions`). Asking needs no live turn and holds none open, so it does
not matter which backend serves the call, or whether the turn that asked is
still running when the answer arrives: the answer is a person's message, and a
person's message addressed to an agent starts that agent's next turn like any
other (`announce.answer_questions` does the addressing).
"""

from app.domain.agent.announce import instance_of_seat, notify_question
from app.domain.agent.realtime.broker import get_broker
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.block.questions import parse_questions, post_questions
from app.domain.identity.handles import names_a_person


async def ask(session, *, place, seat, body) -> list:
    """Post ``body``'s questions under ``seat`` and notify whoever they wait on.

    The person a question waits on is whoever started the seat's open turn, read
    from the turn's row rather than from this process's memory. With no such
    turn, or one the platform started, it waits on nobody in particular.
    """
    questions = parse_questions(body)
    asked = await _who_started_the_open_turn(session, place, seat)
    request_id = body.get("request_id")
    rows = await post_questions(
        session,
        project_id=place.project_id,
        conversation_id=place.conversation_id,
        seat=seat,
        questions=questions,
        asked=asked,
        request_id=str(request_id) if request_id else None,
    )
    # One notice per call, keyed by its first question: a retry lands on it again.
    await notify_question(
        session,
        place=place,
        block=rows[0],
        question=rows[0].content,
        asker=seat,
        asked=(rows[0].meta or {}).get("asked"),
    )
    return rows


async def _who_started_the_open_turn(session, place, seat) -> str | None:
    """The person whose message opened ``seat``'s running turn, if a person did.

    A turn row names the agent by its instance's handle (the conversation it
    runs in), a seat by the instance's id; either may be on the row.
    """
    instance = await instance_of_seat(session, place.project_id, seat)
    turns = AgentTurnRepository(session)
    for handle in (seat, *((instance.handle,) if instance is not None else ())):
        author = await turns.open_turn_author_for_topic(
            place.conversation_id, agent_handle=handle
        )
        if author is not None:
            return author if names_a_person(author) else None
    return None


async def publish_answered(conversation_id, questions) -> None:
    """Show the room what each question now records, once that is committed."""
    from app.domain.block.schemas import BlockOut

    for question in questions:
        await get_broker().publish(
            str(conversation_id),
            {
                "type": "block_updated",
                "block": BlockOut.model_validate(question).model_dump(mode="json"),
            },
        )
