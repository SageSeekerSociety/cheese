"""How an idempotency key is derived.

The key answers one question: "is this the SAME side effect as one that already
happened?" Two things have to be true of it, and they pull in opposite
directions:

- It must MATCH across a turn and its auto-resume, or the resumed 芝士 redoes the
  work. So it cannot contain the turn id — a resume is a new turn.
- It must NOT match across unrelated turns, or a legitimate repeat is swallowed.
  "好的" twice in one topic, a week apart, are two messages, not one.

The thing that satisfies both is the **continuation id**: the id of the logical
unit of work, which a turn and every auto-resume of it share (see
``TurnRunner._execute``). Scoping keys to it means the dedup window is exactly
"this piece of work and its retries" — no wider, no narrower.

With no continuation id (a human clicking in the UI, a turn outside the runner)
there is nothing to dedup against and the caller skips the check entirely: the
duplicate-side-effect risk this exists for is created by automatic resume, and a
human pressing a button twice means it twice.
"""

import hashlib
import uuid


def action_key(
    continuation_id: uuid.UUID | str,
    action: str,
    *parts: object,
) -> str:
    """sha256 hex over the continuation, the action name, and the payload bits
    that make this effect distinct from another of the same kind.

    ``parts`` is joined with a separator that cannot appear in a uuid or an
    action name, so ("a", "bc") and ("ab", "c") never collide.
    """
    material = "\x1f".join([str(continuation_id), action, *(str(p) for p in parts)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


__all__ = ["action_key"]
