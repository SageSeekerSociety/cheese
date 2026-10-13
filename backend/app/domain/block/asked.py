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

The readers — the 待你回答 list and the boards (`BlockRepository`), the answer
path (`agent.announce`), the skip itself (`block.questions`) — all take these
facts from here, so they cannot disagree on whom a question still waits on.
"""

from dataclasses import dataclass

#: The ``kind`` of the `answer_log` entry a skip leaves.
SKIPPED = "skipped"


def asked_of(meta: dict | None) -> tuple[str, ...]:
    """The people a question asks; empty when it asks nobody in particular."""
    return tuple((meta or {}).get("asked") or ())


def asked_everyone(meta: dict | None) -> bool:
    """Whether the question was asked of everyone in the channel."""
    return bool((meta or {}).get("asked_all"))


def skipped_by(meta: dict | None) -> frozenset[str]:
    """Who has skipped the question, and has not taken the skip back."""
    return frozenset(
        str(entry.get("by"))
        for entry in ((meta or {}).get("answer_log") or [])
        if (entry or {}).get("kind") == SKIPPED
    )


def answered(meta: dict | None) -> bool:
    """Whether anyone has answered it: an entry that is not a skip, or the
    `answered` key a person's historical question still carries (the
    migration `e5a1c7d3b284` left those rows alone)."""
    meta = meta or {}
    return bool(meta.get("answered")) or any(
        (entry or {}).get("kind") != SKIPPED for entry in meta.get("answer_log") or []
    )


def skipped_by_all(meta: dict | None) -> bool:
    """Whether every person the question names has skipped it — the skips then
    settle it, and the agent that asked is told. Never for a question asked of
    everyone or of nobody in particular."""
    asked = asked_of(meta)
    return bool(asked) and not asked_everyone(meta) and set(asked) <= skipped_by(meta)


def settled(meta: dict | None) -> bool:
    """Whether the question no longer waits on anybody."""
    return answered(meta) or skipped_by_all(meta)


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
