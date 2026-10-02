"""The input marker, shared by the platform and the machine-side runner.

One platform input carries one of these in its prompt text, and the native
user entry carries it back. The platform binds the entry to its durable
ledger row with it; the runner reads it for exact per-entry attribution.
Kept free of any database import so the harness bundle may ship it.
"""

import secrets

NONCE_PREFIX = "⟪w:"
NONCE_SUFFIX = "⟫"
_NONCE_HEX = 12  # 96 bits, minted once per input


def new_nonce() -> str:
    """A 96-bit marker planted in one input's prompt text."""
    return f"{NONCE_PREFIX}{secrets.token_hex(_NONCE_HEX)}{NONCE_SUFFIX}"


def nonce_in(text: str) -> str | None:
    """The marker in a text, when there is exactly one. Anything else —
    none, several, a broken one — is not a marker at all."""
    if text.count(NONCE_PREFIX) != 1:
        return None
    start = text.index(NONCE_PREFIX) + len(NONCE_PREFIX)
    end = text.find(NONCE_SUFFIX, start)
    if end < 0:
        return None
    candidate = text[start:end]
    if len(candidate) != _NONCE_HEX * 2:
        return None
    try:
        int(candidate, 16)
    except ValueError:
        return None
    return text[start - len(NONCE_PREFIX) : end + len(NONCE_SUFFIX)]
