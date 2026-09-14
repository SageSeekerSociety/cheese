"""Agent uids come from a range of their own.

One ``user`` table holds people and agents. Which one a row is comes from its
``AgentBinding`` and never from the number — that stays true here. What the
range buys is *readability*: an id that says on its face "this is an agent" is
worth having for whoever is looking at a URL, an audit line or a foreign key,
and a reserved range is the cheapest way to get that without a second column
that could drift out of sync with the row it describes.

It is deliberately NOT an invariant. Rows created before the range existed keep
their ordinary ids, and a hand-made row inside the range with no binding is
still a person as far as the platform is concerned. Compare numbers only to
explain a uid you are looking at; to *decide* anything, ask
``IdentityService.is_agent``.

The ceiling is int4's, not a policy choice: ``user.id`` and the columns that
reference it are stored as ``integer``, so an agent uid above 2,147,483,647
would not fit in ``agent_bindings.user_id`` or ``device.owner_user_id``.
"""

from sqlalchemy import Sequence

# Where agent ids start. Far above the human sequence (which starts at 1 and is
# around a thousand in production), so the two cannot meet in practice, and low
# enough to leave ~147M agent ids inside int4.
AGENT_UID_START = 2_000_000_000

# Handed out by the database rather than computed from ``MAX(id)``, so two
# agents enrolled at the same moment cannot be given the same number.
AGENT_UID_SEQUENCE_NAME = "agent_uid_seq"

# Shared with the migration that creates the sequence. ``start`` only takes
# effect where the sequence is CREATE'd; carrying it here keeps the number in
# one place and lets a test read it back instead of hard-coding it twice.
AGENT_UID_SEQUENCE = Sequence(AGENT_UID_SEQUENCE_NAME, start=AGENT_UID_START)
