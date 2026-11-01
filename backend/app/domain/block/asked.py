"""Whom a question waits on, read off the question itself.

A question (`block.questions`) records whom it asks in ``meta.asked``: a list of
people's handles, chosen by the agent the way a message @-mentions people —
one, several, or everyone in the channel (``meta.asked_all``, the list then
holding the channel's people at the moment it was asked). An empty list asks
nobody in particular: a question from a turn the platform started, which a
task's people answer.

Every response to it is an entry of ``meta.answer_log``. An answer — an option
or a person's own words — settles it for everyone. A skip is one person saying
he will not answer: it takes the question off his hands only. Only when every
person a question names has skipped it is it settled by the skips, and a
question asked of everyone never is: the channel is not a fixed list of people
who could all decline.

Three entries are neither: the asking agent taking its own question back
(``withdrawn``, `block.questions.withdraw_question`), the clock running out
on one nobody answered (``expired``, `block.questions.expire_question`), and
the delivery a question's buttons decide being decided some other way
(``decided``, `block.questions.settle_decision`). All three settle it — it
stops waiting and leaves the 待你回答 lists — but none is an answer, so
``answered`` is false for them; only an expired one may still be answered
afterwards (the card keeps its field).

Who may respond to it at all — click an option, write an answer, skip it — is
`may_answer`, one rule for all three: the people it names; anyone in the
channel when it is asked of everyone; and anyone when it names nobody in
particular, as it always has been.

The readers — the 待你回答 list and the boards (`BlockRepository`), the answer
path (`agent.announce`), the skip itself (`block.questions`) — all take these
facts from here, so they cannot disagree on whom a question still waits on.
"""

from dataclasses import dataclass

from app.domain.identity.handles import names_a_person

#: The ``kind`` of the `answer_log` entry a skip leaves.
SKIPPED = "skipped"
#: The asking agent took its own question back before anyone answered it
#: (`block.questions.withdraw_question`).
WITHDRAWN = "withdrawn"
#: Nobody answered it within its deadline (`block.questions.expire_question`).
EXPIRED = "expired"
#: The delivery its buttons decide was decided another way — on the card, on
#: the forge, or by the task closing — with none of its buttons
#: (`block.questions.settle_decision`).
DECIDED = "decided"
#: The kinds that settle a question without anybody answering it. They are
#: not answers: ``answered`` stays false, and the card is free to take one.
CLOSED_BY_THE_ASKER = (WITHDRAWN, EXPIRED, DECIDED)


def asked_of(meta: dict | None) -> tuple[str, ...]:
    """The people a question asks; empty when it asks nobody in particular."""
    asked = (meta or {}).get("asked") or ()
    # A bare handle is what a backend from before the list wrote; blue/green
    # deploys keep one running past the migration, so its questions land in
    # this shape. tuple() of it would split the handle into letters.
    return (asked,) if isinstance(asked, str) else tuple(asked)


def asked_everyone(meta: dict | None) -> bool:
    """Whether the question was asked of everyone in the channel."""
    return bool((meta or {}).get("asked_all"))


def may_answer(meta: dict | None, handle: str | None) -> bool:
    """Whether ``handle`` may respond to the question — answer it by an option or
    in his own words, or skip it.

    Always a person: a teammate does not answer a teammate's question. Among
    people, those it names; everyone in the channel when it was asked of
    everyone, someone who joined after it was asked included (the list holds
    who was there at that moment, the question asks the channel); and anyone
    when it names nobody in particular (asked in a turn the platform started)."""
    asked = asked_of(meta)
    return names_a_person(handle) and (
        not asked or asked_everyone(meta) or handle in asked
    )


def skipped_by(meta: dict | None) -> frozenset[str]:
    """Who has skipped the question, and has not taken the skip back."""
    return frozenset(
        str(entry.get("by"))
        for entry in ((meta or {}).get("answer_log") or [])
        if (entry or {}).get("kind") == SKIPPED
    )


def _said(meta: dict | None) -> list[dict]:
    """The `answer_log` entries that are somebody's response to the question."""
    return [
        entry
        for entry in ((meta or {}).get("answer_log") or [])
        if (entry or {}).get("kind") not in CLOSED_BY_THE_ASKER
    ]


def answered(meta: dict | None) -> bool:
    """Whether anyone has answered it: an entry that is not a skip, or the
    `answered` key a person's historical question still carries (the
    migration `e5a1c7d3b284` left those rows alone).

    Taking the question back and letting it expire are not answers, so a
    question the asker closed has ``answered`` false."""
    meta = meta or {}
    return bool(meta.get("answered")) or any(
        (entry or {}).get("kind") != SKIPPED for entry in _said(meta)
    )


def closed_by_the_asker(meta: dict | None) -> bool:
    """Whether the question stopped waiting for a reason other than an answer:
    the asking agent took it back, or its deadline passed."""
    return any(
        (entry or {}).get("kind") in CLOSED_BY_THE_ASKER
        for entry in ((meta or {}).get("answer_log") or [])
    )


def withdrawn_by_the_asker(meta: dict | None) -> bool:
    """Whether it is closed for good — the asking agent took it back, or what it
    asked about was decided — as opposed to its clock simply running out. Only
    those are a reason to refuse a late answer."""
    return any(
        (entry or {}).get("kind") in (WITHDRAWN, DECIDED)
        for entry in ((meta or {}).get("answer_log") or [])
    )


def skipped_by_all(meta: dict | None) -> bool:
    """Whether every person the question names has skipped it — the skips then
    settle it, and the agent that asked is told. Never for a question asked of
    everyone or of nobody in particular."""
    asked = asked_of(meta)
    return bool(asked) and not asked_everyone(meta) and set(asked) <= skipped_by(meta)


def settled(meta: dict | None) -> bool:
    """Whether the question no longer waits on anybody — so it leaves the 待你
    回答 lists and stops lighting the channel's yellow light."""
    return answered(meta) or skipped_by_all(meta) or closed_by_the_asker(meta)


def answerable(meta: dict | None) -> bool:
    """Whether a person may still answer it.

    The same as ``settled`` except for one case: a question whose deadline
    passed may still be answered (the card keeps its field), so an expired one
    is settled without being closed to answers. A question the asker took back
    is closed, and one that has been answered takes no second answer."""
    if answered(meta) or skipped_by_all(meta) or withdrawn_by_the_asker(meta):
        return False
    return True


@dataclass(frozen=True)
class Awaited:
    """Whom an open question still waits on, for the lists and the boards."""

    asked: tuple[str, ...]
    skipped: frozenset[str] = frozenset()

    def waiting(self, otherwise: tuple[str, ...] = ()) -> tuple[str, ...]:
        """The people it still waits on. ``otherwise`` is whom a question that
        asks nobody in particular waits on where it was asked (a task's
        people); a channel has nobody to put there."""
        return tuple(h for h in (self.asked or otherwise) if h not in self.skipped)

    def only(self) -> str | None:
        """The one person it waits on, when it names exactly one."""
        waiting = self.waiting()
        return waiting[0] if len(waiting) == 1 else None

    @classmethod
    def of(cls, meta: dict | None) -> "Awaited":
        return cls(asked_of(meta), skipped_by(meta))
