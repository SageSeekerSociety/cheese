"""Shared token operations for credentials kept as digests: user sessions,
trusted devices and the durable tokens enrolled machines connect with."""

import hashlib


def digest_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
