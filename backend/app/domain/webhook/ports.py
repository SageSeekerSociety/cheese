"""Calls ``webhook`` makes that only a higher layer can answer.

``post_with_retries`` lands a system-authored post in a topic's timeline, and
landing one is ``announce()``'s job: it writes the row, the delivery, the
notification. ``app.domain.agent`` sits *above* this package in the layer table
(``docs/manual/dev/architecture.md``), so ``app.domain.webhook`` may not import
it. The write happens on the other side of that line and reaches this package as
a value.

That is the whole shape of this port: ``webhook`` names the one call it needs,
``app.domain.agent.announce.announce`` already has that shape, and each caller
hands it in — the HTTP door (``app.api.routes.webhooks``), once it has verified
the token, and the merge-result-back-to-room card
(``app.domain.review.room_notices``), which imports ``announce`` for its own
posts anyway.
"""

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class Poster(Protocol):
    """把一句话落进一个房间的时间线。"""

    async def __call__(
        self,
        session: AsyncSession,
        *,
        place_id: uuid.UUID,
        task_id: uuid.UUID | None,
        content: str,
        meta: dict | None,
        author: str,
    ) -> object | None:
        """Returns what it landed, or ``None`` if the topic is gone.

        Opaque on purpose: the one thing this package does with it is ask
        whether the topic still existed. ``announce()`` returns the ``Block``
        it wrote, and ``Block`` lives above this package too.
        """
        ...
