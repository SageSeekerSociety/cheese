"""Shared token operations for user credentials."""

import hashlib


def digest_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
