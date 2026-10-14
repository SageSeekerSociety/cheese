"""The model streams this process is still relaying.

A rollout moves traffic to the next backend and then stops this one. A model
stream relayed through ``/llm/v1`` (``app/api/routes/llm_proxy.py``) can run for
minutes on a reasoning model and has no way to move: stopping the process ends
it, and the harness that asked loses the whole answer. So the rollout asks this
count on the backend it is about to stop and waits, up to a bound, until it is
zero (``wait_for_model_streams`` in ``deploy/deploy-docker.sh``). No new stream
can start here by then, since traffic has already left.

The count lives as long as the process does; nothing resets it.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator


class ModelStreams:
    def __init__(self) -> None:
        self._open = 0

    @property
    def open(self) -> int:
        return self._open

    @contextlib.contextmanager
    def held(self) -> Iterator[None]:
        self._open += 1
        try:
            yield
        finally:
            self._open -= 1


model_streams = ModelStreams()
