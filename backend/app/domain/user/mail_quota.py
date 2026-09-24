"""How often the platform will mail an address that a request names.

Anyone can type any address into the registration and recovery forms, so each
of those mails goes to a mailbox the requester need not own. The quota is per
address and per purpose: one mail per ``MAIL_COOLDOWN_SECONDS``, and at most
``MAIL_HOURLY_LIMIT`` in any hour counted from the first. Where the requester's
own address is known, it may have at most ``MAIL_CLIENT_HOURLY_LIMIT`` mails
sent per purpose in an hour, whichever addresses they go to.
"""

from redis.asyncio import Redis

MAIL_QUOTA_PREFIX = "cheese:mail_quota:"
MAIL_COOLDOWN_SECONDS = 60
MAIL_HOURLY_LIMIT = 5
# Generous on purpose: a whole class signing up together from behind one campus
# NAT address must get through. It only stops one source mailing strangers in
# bulk.
MAIL_CLIENT_HOURLY_LIMIT = 200
_HOUR = 60 * 60

# Checked and counted in one script: done as separate calls, a burst of
# simultaneous requests would all see a free slot before any of them took it.
_TAKE_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  return 0
end
local sent = redis.call('INCR', KEYS[2])
if sent == 1 then
  redis.call('EXPIRE', KEYS[2], ARGV[3])
end
if sent > tonumber(ARGV[2]) then
  return 0
end
if #KEYS == 3 then
  local from_client = redis.call('INCR', KEYS[3])
  if from_client == 1 then
    redis.call('EXPIRE', KEYS[3], ARGV[3])
  end
  if from_client > tonumber(ARGV[4]) then
    redis.call('DECR', KEYS[3])
    redis.call('DECR', KEYS[2])
    return 0
  end
end
if tonumber(ARGV[1]) > 0 then
  redis.call('SET', KEYS[1], '1', 'EX', ARGV[1])
end
return 1
"""

_GIVE_BACK_SCRIPT = """
redis.call('DEL', KEYS[1])
if redis.call('EXISTS', KEYS[2]) == 1 then
  redis.call('DECR', KEYS[2])
end
if #KEYS == 3 and redis.call('EXISTS', KEYS[3]) == 1 then
  redis.call('DECR', KEYS[3])
end
return 1
"""


def normalize_email(email: str) -> str:
    return email.strip().lower()


class MailQuota:
    def __init__(self, redis: Redis, purpose: str) -> None:
        self._redis = redis
        self._purpose = purpose

    def _keys(self, email: str, client: str | None) -> list[str]:
        base = f"{MAIL_QUOTA_PREFIX}{self._purpose}:{normalize_email(email)}"
        keys = [f"{base}:cooldown", f"{base}:hour"]
        if client is not None:
            keys.append(f"{MAIL_QUOTA_PREFIX}{self._purpose}:from:{client}:hour")
        return keys

    async def take(self, email: str, client: str | None = None) -> bool:
        """Claim one mail to ``email``; False when the quota is spent.

        ``client`` is the requester's address as ``resolved_client_address``
        gives it, and None where the server cannot tell clients apart, which
        leaves the per-client quota out rather than sharing one among all.
        """
        keys = self._keys(email, client)
        taken = await self._redis.eval(  # type: ignore[misc]
            _TAKE_SCRIPT,
            len(keys),
            *keys,
            MAIL_COOLDOWN_SECONDS,
            MAIL_HOURLY_LIMIT,
            _HOUR,
            MAIL_CLIENT_HOURLY_LIMIT,
        )
        return taken == 1

    async def give_back(self, email: str, client: str | None = None) -> None:
        """Return a claim whose mail never left, so a retry need not wait."""
        keys = self._keys(email, client)
        await self._redis.eval(_GIVE_BACK_SCRIPT, len(keys), *keys)  # type: ignore[misc]
