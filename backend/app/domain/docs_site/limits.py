"""How much 问芝士 one person may ask, and how much one process will answer at once.

Three limits, each for a different failure:

* **per user, per hour and per day** (Valkey counters): a signed-in account
  cannot turn the assistant into a free model. Counted when a question is
  accepted, whether or not the docs could answer it.
* **one question at a time per user** (a Valkey lock that expires): a script
  cannot open twenty streams from one account.
* **per process concurrency** (a counter, never waited on): a burst gets an
  immediate "busy" instead of piling up streams that hold the model for minutes.

Valkey down means refusing, not waving through: the limits are the only thing
standing between an account and the gateway budget.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_WINDOWS = (("h", 3600), ("d", 86400))


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    message: str = ""
    retry_after: int = 0


# INCR every window, and give each its expiry the first time it is created.
# Returns the counts in window order.
_COUNT = """
local out = {}
for i, key in ipairs(KEYS) do
  local n = redis.call('INCR', key)
  if n == 1 then redis.call('EXPIRE', key, ARGV[i]) end
  out[i] = n
end
return out
"""


class AskLimits:
    def __init__(self, redis: Callable[[], Redis | None]) -> None:
        # A getter, not a client: the shared client is rebuilt whenever its
        # cache is cleared, and holding the old one would pin a dead loop.
        self._client = redis
        self._busy = 0

    def _keys(self, user_id: int) -> list[str]:
        return [f"docs-ask:{w}:{user_id}" for w, _ in _WINDOWS]

    async def admit(self, user_id: int) -> Verdict:
        redis = self._client()
        if redis is None:
            return Verdict(False, "问芝士暂时不可用，稍后再试。", 60)
        try:
            hourly, daily = await redis.eval(  # type: ignore[misc]
                _COUNT,
                len(_WINDOWS),
                *self._keys(user_id),
                *[str(s) for _, s in _WINDOWS],
            )
            if int(daily) > settings.docs_assistant_daily_limit:
                ttl = await redis.ttl(self._keys(user_id)[1])
                return Verdict(
                    False,
                    f"今天已经问了 {settings.docs_assistant_daily_limit} 个问题，"
                    "明天再来吧。",
                    max(int(ttl), 60),
                )
            if int(hourly) > settings.docs_assistant_hourly_limit:
                ttl = await redis.ttl(self._keys(user_id)[0])
                return Verdict(
                    False, "这一小时问得有点多了，稍后再问。", max(int(ttl), 60)
                )
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
