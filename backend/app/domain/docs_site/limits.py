"""How many questions 问芝士 will answer at once, per person and per process.

What a person may spend is their personal credits (``usage/personal.py``); these
limits are about load, not money:

* **one question at a time per user** (a Valkey lock that expires): a script
  cannot open twenty streams from one account.
* **per process concurrency** (a counter, never waited on): a burst gets an
  immediate "busy" instead of piling up streams that hold the model for minutes.

Valkey down means refusing, not waving through.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    message: str = ""
    retry_after: int = 0


class AskLimits:
    def __init__(self, redis: Callable[[], Redis | None]) -> None:
        # A getter, not a client: the shared client is rebuilt whenever its
        # cache is cleared, and holding the old one would pin a dead loop.
        self._client = redis
        self._busy = 0

    async def admit(self, user_id: int) -> Verdict:
        redis = self._client()
        if redis is None:
            return Verdict(False, "问芝士暂时不可用，稍后再试。", 60)
        try:
            if not await redis.set(f"docs-ask:busy:{user_id}", "1", nx=True, ex=120):
                return Verdict(False, "上一个问题还在回答，等它答完再问。", 5)
        except Exception:  # noqa: BLE001 — an unreachable limiter refuses
            logger.warning("docs ask limiter unavailable", exc_info=True)
            return Verdict(False, "问芝士暂时不可用，稍后再试。", 60)
        return Verdict(True)

    async def release(self, user_id: int) -> None:
        redis = self._client()
        if redis is None:
            return
        try:
            await redis.delete(f"docs-ask:busy:{user_id}")
        except Exception:  # noqa: BLE001 — the lock expires by itself
            pass

    def try_slot(self) -> bool:
        """Take a process slot without waiting; release it with ``free_slot``.

        A plain counter is enough: it is only touched from the event loop, and
        nothing awaits between the check and the increment."""
        if self._busy >= settings.docs_assistant_concurrency:
            return False
        self._busy += 1
        return True

    def free_slot(self) -> None:
        self._busy = max(0, self._busy - 1)
