"""Where the platform's time goes on one message to a session."""

import contextlib
import logging
import time
from collections.abc import Iterator


class Stopwatch:
    """Splits elapsed time into named steps: each call charges the time since
    the last call (or ``lap``) to ``name``, in ms. A caller and a callee can
    share one ``phases`` dict, so their steps land in the same line."""

    def __init__(self, phases: dict[str, float] | None = None) -> None:
        self.phases = phases
        self._start = self._last = time.monotonic()

    def __call__(self, name: str) -> None:
        now = time.monotonic()
        if self.phases is not None:
            spent = (now - self._last) * 1000
            self.phases[name] = self.phases.get(name, 0.0) + spent
        self._last = now

    def lap(self) -> None:
        """Start the next step now, charging the time since to nobody: a callee
        that timed its own steps into ``phases`` has accounted for it."""
        self._last = time.monotonic()

    def total(self) -> float:
        return (time.monotonic() - self._start) * 1000


@contextlib.contextmanager
def timed(logger: logging.Logger, event: str, **fields: object) -> Iterator[Stopwatch]:
    """One ``event`` line when the block ends, however it ends: its fields, the
    outcome, the total and each step the block charged to the stopwatch."""
    mark = Stopwatch({})
    outcome = "failed"
    try:
        yield mark
        outcome = "ok"
    finally:
        named = " ".join(f"{key}={value}" for key, value in fields.items())
        logger.info(
            "%s %s outcome=%s elapsed_ms=%.1f phases_ms=%s",
            event,
            named,
            outcome,
            mark.total(),
            {name: round(ms, 1) for name, ms in (mark.phases or {}).items()},
        )
