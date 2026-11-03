"""A long answer of a person's 芝士 keeps going: the credential its tools act
with is renewed before it runs out, and near its ceiling it is told, once, to
write down where it got to."""

import time
import uuid

import pytest

from app.domain.agent.personal import service as personal
from app.domain.agent.session_host.answer import Words
from app.domain.agent.session_host.consumptions import Consumption


class _Session:
    """What was said to the session while the answer was read."""

    def __init__(self) -> None:
        self.steered: list[str] = []
        self.acting: list[str] = []

    async def steer(self, consumption, text: str) -> None:
        self.steered.append(text)

    async def act_as(self, consumption, acting: str) -> None:
        self.acting.append(acting)

    async def publish(self, consumption, event: str, data: dict) -> None:
        pass


def _answer(said_ago_s: float) -> Consumption:
    """An answer said ``said_ago_s`` ago, with the whole ceiling to go."""
    return Consumption(
        work_id=str(uuid.uuid4()),
        kind=personal.KIND,
        key=str(uuid.uuid4()),
        harness="pi",
        home="home",
        host=None,
        data={"user": 7, "acting": {"handle": "lin", "project": None}},
        said=True,
        deadline=time.time() - said_ago_s + personal.ANSWER_S,
    )


def _answers(session: _Session) -> personal.Answers:
    return personal.Answers(None, session, lambda: None)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_a_credential_given_long_ago_is_renewed_once_and_a_fresh_one_is_not():
    session = _Session()
    answers = _answers(session)
    old, fresh = _answer(41 * 60), _answer(60)

    for _ in range(3):
        await answers.took(old, Words("好", 0))
        await answers.took(fresh, Words("好", 0))

    assert len(session.acting) == 1 and session.acting[0]


@pytest.mark.anyio
async def test_an_answer_near_its_ceiling_is_told_once_to_wrap_up():
    session = _Session()
    answers = _answers(session)
    near = _answer(personal.ANSWER_S - 60)
    far = _answer(60)

    for _ in range(3):
        await answers.took(near, Words("好", 0))
        await answers.took(far, Words("好", 0))

    assert session.steered == [personal.WRAP_UP]
