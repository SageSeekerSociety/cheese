"""What a tool call printed, kept on its 现场 step.

Only the tail, and only so much of it. A step is one of the most numerous rows
the platform stores — one per tool call — and a Read hands back a whole file,
so an uncapped copy would make the event table grow with every file an agent
ever looked at. The end is the part worth keeping: a command says whether it
worked, and why not, on its last lines.

8 KiB is about a hundred lines of command output: enough to read a failing test
run's summary or a build error, small enough that a busy turn of fifty steps
adds at most 400 KiB to the database. The transcript page does not carry it
(`without_output`); it is read one step at a time when somebody opens it.

Redacted with the same filter as backend tracebacks pushed into a room
(`scrub_secrets`): what a command prints can hold a token it was handed, and a
room is read by people who do not hold that token.
"""

from app.core.obs import scrub_secrets

#: How much of a step's output is kept, in UTF-8 bytes, counted from the end.
STEP_OUTPUT_MAX_BYTES = 8 * 1024


def output_tail(text: str) -> tuple[str, int]:
    """The redacted tail to keep, and how long the redacted whole was (bytes).

    Redacted before it is cut, so a credential the cut would split is still
    recognised whole.
    """
    raw = scrub_secrets(text) or ""
    data = raw.encode()
    if len(data) <= STEP_OUTPUT_MAX_BYTES:
        return raw, len(data)
    return data[-STEP_OUTPUT_MAX_BYTES:].decode("utf-8", "ignore"), len(data)


def without_output(payload: dict) -> dict:
    """A block payload without the kept output — what lists and frames carry.

    The step still says it HAS output (``output_bytes``), which is what the
    row needs to offer it; the text itself is fetched when it is opened.
    """
    meta = payload.get("meta")
    if not isinstance(meta, dict) or "output" not in meta:
        return payload
    return {**payload, "meta": {k: v for k, v in meta.items() if k != "output"}}
