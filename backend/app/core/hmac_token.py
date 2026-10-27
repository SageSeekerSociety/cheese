"""The one HMAC signature the credential families are built on.

``app.core.sandbox_auth`` (sandbox and project/personal/delegated credentials)
and ``app.core.webhook_auth`` (webhook credentials) sign the same way under the
same key, and each family's own signing domain and prefix are what keep one
kind of credential useless to the other. The construction itself must not be
one of the things that differs between them: two copies of the algorithm mean a
change to one is invisible to the other, and nothing in either module notices.

So the algorithm lives here once. Each module keeps only the secret it signs
with and passes it explicitly.
"""

import base64
import hashlib
import hmac


def sign_body(secret: str, body: str) -> str:
    """HMAC-SHA256 of ``body`` under ``secret``, URL-safe base64, unpadded.

    The signature half of the ``{body}.{signature}`` tokens both credential
    families mint. Unpadded because that pair travels through URLs, headers and
    environment variables unchanged.
    """
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def constant_time_equal(left: str, right: str) -> bool:
    """Compare attacker-controlled credential text without Unicode failures."""
    return hmac.compare_digest(left.encode(), right.encode())
