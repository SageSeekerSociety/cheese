"""When the runner has Claude Code compact the conversation: when the session
asks for it, and when a turn starts on a conversation gone cold.

**Asked.** ``cheese_session(action=compact)`` reaches the runner as its
``compact`` operation (``request``); ``/compact`` is written once the turn it
was asked in has ended (``now``).

**Cold.** A provider keeps a conversation's cached prefix warm only for so long
after the last call (the model's ``cheese_cache_lifetime_s`` in the gateway
model table). A turn that starts after that re-reads the whole context at full
price, so that is the moment compacting costs least: the full read is paid
anyway, and every call after it reads the summary instead. The platform hands
the turn's input the model's lifetime and the size worth compacting
(``compact_if_cold``); the runner, the one place that knows when the session
last called the model and how large its context was then (``note``), decides,
and holds the input until ``/compact`` has answered (``when_cold``). Claude
Code's own proportional compaction is untouched: this only brings one forward.

Separate from ``runner`` because that module is at its size cap. Standard
library only: this module travels in the runner archive.
"""

import json
import sys
import time

from app.domain.agent.harness.driven import runner as driven

#: How long the runner waits for a compaction it asked the build for to report
#: its `result`. Summarising a full window takes minutes; this is the outer
#: bound on believing it will come at all, not an expected duration.
COMPACT_TIMEOUT_S = 1800.0

#: Journal key: when the session last had a model call answered (wall clock,
#: so a runner started again on the same conversation still knows it) and how
#: large its context was then, in tokens.
_LAST_CALL = "cold:last_call"


def request(runner) -> dict:
    """The session asked to compact its own context: `/compact`, written once
    the turn it asked in has ended.

    Written then, and not mid-turn: a tool call in flight is answered at a
    tool boundary, and the build takes what comes in on stdin at those
    boundaries, so a slash command written from inside a tool call arrives
    as part of that turn — where the pinned build's contract says nothing
    about what a slash command does. A tool call is always inside a turn,
    so the request always defers.

    Once a turn is the whole rate limit: a request still waiting and one
    that has already run look the same to the agent that asked, and a
    second `/compact` would only summarise the first one's output.
    """
    if runner.compact_when_idle:
        return {"already": True}
    runner.compact_when_idle = True
    # A tool call is answered inside a turn, so `_end` is where this
    # normally goes. A session already idle has no turn coming.
    if not runner.working and not runner.closing:
        runner.compact_when_idle = False
        runner.helpers.append(driven.watched(now(runner)))
    return {"requested": True, "already": False}


async def now(runner) -> None:
    """Write `/compact` the way `/reload-skills` is written, and wait for
    the command's `result` in the background.

    Not through `dispatch`: the tool call that asked has been answered
    already, and a compaction of a full window takes minutes to report.
    What the room shows meanwhile comes from the build's own
    `system/status` records (`events._compacting`)."""
    try:
        await runner.command("/compact", COMPACT_TIMEOUT_S)
    except Exception as error:  # the session goes on without it
        print(f"compaction not asked: {error!r}", file=sys.stderr, flush=True)


def note(runner, record: dict) -> None:
    """Keep when the session last had a model call answered, and how large its
    context was then: an assistant message's usage is the whole prompt that
    call read and what it wrote. A compaction leaves the context small, and
    the next call says how small."""
    if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
        runner.journal.remember(
            _LAST_CALL, json.dumps({"at": time.time(), "tokens": 0})
        )
        return
    if record.get("type") != "assistant":
        return
    usage = (record.get("message") or {}).get("usage")
    if not isinstance(usage, dict):
        return
    tokens = sum(
        value
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "output_tokens",
        )
        if isinstance(value := usage.get(key), int)
    )
    runner.journal.remember(
        _LAST_CALL, json.dumps({"at": time.time(), "tokens": tokens})
    )


def cold(runner, asked: dict | None) -> bool:
    """Whether a turn starting now finds the conversation cold and large
    enough to compact first, by what the platform said counts as either."""
    if not asked:
        return False
    try:
        idle_s = float(asked["idle_s"])
        above = int(asked["above_tokens"])
    except (KeyError, TypeError, ValueError):
        return False
    last = json.loads(runner.journal.recall(_LAST_CALL) or "{}")
    at, tokens = last.get("at"), last.get("tokens")
    if not isinstance(at, int | float) or not isinstance(tokens, int):
        return False
    return time.time() - at >= idle_s and tokens >= above


async def when_cold(runner, asked: dict | None) -> bool:
    """Compact before the turn's input goes in, if the conversation is cold
    and large (``cold``). True when it was compacted: the input then says
    what a compacted session has to be told again (``asked['text']``).

    The input waits for `/compact` to answer rather than queueing behind it:
    an input on stdin while the build compacts would be taken into the
    compaction's own work. A compaction that fails costs the turn nothing but
    the wait; the input goes in as it was."""
    if not cold(runner, asked):
        return False
    # The input being held owes a person a reply, but nothing the compaction
    # does is that reply, and its own ending must not hold it to one: nothing
    # is owed while it runs, and the reply is owed again once it is done.
    owed = runner.owed
    if owed is not None:
        runner.reply_settled()
    try:
        answer = await runner.command("/compact", COMPACT_TIMEOUT_S)
    except Exception as error:  # the turn goes on uncompacted
        print(f"cold compaction failed: {error!r}", file=sys.stderr, flush=True)
        return False
    finally:
        if owed is not None:
            runner.owe_reply(owed)
    return not answer.get("is_error")
