"""Secret handling for user-issued agent tokens (外部 agent 凭证).

A user delegates platform access to their own agent (a local Claude Code, a
bot they run) by issuing one of these. It is a bearer secret, not a JWT: it
must be revocable the instant the user says so, and a self-contained signed
token cannot be. So the secret is random, the database stores only its SHA-256
digest, and every request re-derives the digest to find the row.

SHA-256 rather than bcrypt/argon2 on purpose: unlike a password this secret is
256 bits of CSPRNG output, so there is no dictionary to attack — and the digest
is computed on *every* authenticated request, where a deliberately slow KDF
would be a self-inflicted DoS. The same reasoning is why the lookup is by
digest (indexed) instead of a per-row verify.
"""

import hashlib
import secrets

# Distinctive prefix so a leaked credential is greppable and a human can tell
# it apart from a session JWT at a glance.
TOKEN_PREFIX = "cxat_"

# How much of the token is stored in the clear for display ("cxat_A1b2c3d4…").
# Short enough to be useless as a brute-force head start, long enough that a
# user with several tokens can tell which row is which.
DISPLAY_PREFIX_LEN = len(TOKEN_PREFIX) + 8


def generate_token() -> str:
    """A fresh agent-token secret. 32 bytes of CSPRNG, URL-safe."""
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """The digest stored in ``agent_tokens.token_hash`` (hex, 64 chars)."""
    return hashlib.sha256(token.encode()).hexdigest()


def display_prefix(token: str) -> str:
    """The clear-text head kept for listing, so the user can identify a token
    they can no longer read in full."""
    return token[:DISPLAY_PREFIX_LEN]


def looks_like_agent_token(candidate: str) -> bool:
    """Cheap discriminator used before touching the database.

    Session JWTs and agent tokens both arrive as ``Authorization: Bearer``, and
    every request that is *not* an agent token must not pay for a lookup.
    """
    return candidate.startswith(TOKEN_PREFIX)
