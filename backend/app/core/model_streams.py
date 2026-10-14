"""The model streams this process is still relaying.

A rollout moves traffic to the next backend and then stops this one. A model
stream relayed through ``/llm/v1`` (``app/api/routes/llm_proxy.py``) can run for
minutes on a reasoning model, and the model may take more than a minute to send
its first byte; neither can move, and stopping the process ends both. So the
rollout asks this count on the backend it is about to stop and waits, up to a
bound, until it is zero (``deploy/wait-model-streams.sh``). No new stream can
start here by then, since traffic has already left.

The count lives as long as the process does; nothing resets it.
"""

from __future__ import annotations

from collections.abc import Callable


class ModelStreams:
    def __init__(self) -> None:
        self._open = 0

    @property
    def open(self) -> int:
        return self._open

    def opened(self) -> Callable[[], None]:
        """Count one stream, and return what ends it. Ending it twice ends it
        once, so every path out of the stream can call it."""
        self._open += 1
        ended = False

        def end() -> None:
            nonlocal ended
            if not ended:
                ended = True
                self._open -= 1

        return end


model_streams = ModelStreams()
