"""How often the platform will mail an address that a request names.

Anyone can type any address into the registration and recovery forms, so each
of those mails goes to a mailbox the requester need not own. The quota is per
address and per purpose: one mail per ``MAIL_COOLDOWN_SECONDS``, and at most
``MAIL_HOURLY_LIMIT`` in any hour counted from the first. Where the requester's
own address is known, it may have at most ``MAIL_CLIENT_HOURLY_LIMIT`` mails
sent per purpose in an hour, whichever addresses they go to.

Above all of those, the whole site may send at most ``MAIL_SITE_HOURLY_LIMIT``
such mails in an hour, whatever the purpose: the per-address and per-source
limits do not stop many sources each mailing many strangers a little. That
allowance is taken only by a mail about to leave, never by a request, so it
counts real mail to real mailboxes; see ``take_site_mail``.
"""

import logging
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core import alerting
from app.core.errors import SystemBusyError

logger = logging.getLogger(__name__)

MAIL_QUOTA_PREFIX = "cheese:mail_quota:"
MAIL_COOLDOWN_SECONDS = 60
MAIL_HOURLY_LIMIT = 5
# Generous on purpose: a whole class signing up together from behind one campus
# NAT address must get through. It only stops one source mailing strangers in
# bulk.
MAIL_CLIENT_HOURLY_LIMIT = 200
# Generous for the same reason: several classes signing up in the same hour,
# each person asking for a code twice, stay well under it. Only mail that
# actually leaves counts, so a request naming an address nobody has an account
# at, or a placeholder address, spends none of it: exhausting it takes real
# mail to real mailboxes, which the per-address quota already bounds. Where
# saying "refused" would tell whether an address has an account, the caller
# answers as if the mail had gone and sends nothing.
MAIL_SITE_HOURLY_LIMIT = 1000
MAIL_SITE_KEY = f"{MAIL_QUOTA_PREFIX}site:hour"
_HOUR = 60 * 60

# Checked and counted in one script: done as separate calls, a burst of
# simultaneous requests would all see a free slot before any of them took it.
# KEYS: cooldown, address hour, and the requester's hour if known.
# Returns {1, 0} when claimed, or {0, seconds until the refusing limit lifts}.
_CLAIM_SCRIPT = """
local cooling = redis.call('TTL', KEYS[1])
if cooling > 0 then
  return {0, cooling}
end
local sent = redis.call('INCR', KEYS[2])
if sent == 1 then
  redis.call('EXPIRE', KEYS[2], ARGV[3])
end
if sent > tonumber(ARGV[2]) then
  redis.call('DECR', KEYS[2])
  return {0, math.max(redis.call('TTL', KEYS[2]), 1)}
end
if #KEYS == 3 then
  local from_client = redis.call('INCR', KEYS[3])
  if from_client == 1 then
    redis.call('EXPIRE', KEYS[3], ARGV[3])
  end
  if from_client > tonumber(ARGV[4]) then
    redis.call('DECR', KEYS[3])
    redis.call('DECR', KEYS[2])
    return {0, math.max(redis.call('TTL', KEYS[3]), 1)}
  end
end
if tonumber(ARGV[1]) > 0 then
  redis.call('SET', KEYS[1], '1', 'EX', ARGV[1])
end
return {1, 0}
"""

# One slot of the site's hourly allowance, for a mail about to leave.
_SITE_SCRIPT = """
local site = redis.call('INCR', KEYS[1])
if site == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
end
if site > tonumber(ARGV[1]) then
  redis.call('DECR', KEYS[1])
  return 0
end
return 1
"""

_SITE_GIVE_BACK_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  redis.call('DECR', KEYS[1])
end
return 1
"""

_GIVE_BACK_SCRIPT = """
redis.call('DEL', KEYS[1])
for i = 2, #KEYS do
  if redis.call('EXISTS', KEYS[i]) == 1 then
    redis.call('DECR', KEYS[i])
  end
end
return 1
"""

_SITE_GIVE_BACK_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 1 then
  redis.call('DECR', KEYS[1])
end
return 1
"""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def site_mail_limit_reached() -> SystemBusyError:
    return SystemBusyError(
        "The site has sent all the mail it may this hour. Please try again later",
        {"reason": "mail_limit_reached"},
    )


@dataclass(frozen=True)
class MailClaim:
    granted: bool
    #: Refused: how long until the limit that refused it lifts.
    retry_after_seconds: int = 0


async def take_site_mail(redis: Redis) -> bool:
    """Take one mail from the site's hourly allowance, right before sending
    it; False when the allowance is spent and the mail must not go out.

    The first refusal in an hour raises an alert, and later ones repeat it
    only to the log.
    """
    taken = await redis.eval(  # type: ignore[misc]
        _SITE_SCRIPT, 1, MAIL_SITE_KEY, MAIL_SITE_HOURLY_LIMIT, _HOUR
    )
    if taken == 1:
        return True
    logger.warning("Site-wide mail limit reached; a mail was not sent")
    # One alert per hour however many mails are refused: the key makes every
    # refusal the same problem.
    alerting.send(
        "发信数量已达全站上限",
        [
            f"一小时内最多发送 {MAIL_SITE_HOURLY_LIMIT} 封验证邮件，此后的邮件不再发出",
        ],
        key="mail:site-hourly-limit",
    )
    return False


async def give_back_site_mail(redis: Redis) -> None:
    """Return the slot of a mail that never left."""
    await redis.eval(_SITE_GIVE_BACK_SCRIPT, 1, MAIL_SITE_KEY)  # type: ignore[misc]


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

    async def claim(self, email: str, client: str | None = None) -> MailClaim:
        """Claim one mail to ``email``, or say how long until one may be sent.

        ``client`` is the requester's address as ``resolved_client_address``
        gives it, and None where the server cannot tell clients apart, which
        leaves the per-client quota out rather than sharing one among all.
        """
        keys = self._keys(email, client)
        granted, wait = await self._redis.eval(  # type: ignore[misc]
            _CLAIM_SCRIPT,
            len(keys),
            *keys,
            MAIL_COOLDOWN_SECONDS,
            MAIL_HOURLY_LIMIT,
            _HOUR,
            MAIL_CLIENT_HOURLY_LIMIT,
        )
        return MailClaim(granted=granted == 1, retry_after_seconds=int(wait))

    async def give_back(self, email: str, client: str | None = None) -> None:
        """Return a claim whose mail never left, so a retry need not wait."""
        keys = self._keys(email, client)
        await self._redis.eval(_GIVE_BACK_SCRIPT, len(keys), *keys)  # type: ignore[misc]
