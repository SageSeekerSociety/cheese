"""Auth for the external-facing webhook ingress (POST /webhooks/{topic_id}).

Signs with the same key and the same construction as ``app.core.sandbox_auth``
— both take the algorithm from ``app.core.hmac_token`` — but has a different
lifetime semantic: a webhook credential is a long-lived, revocable/rotatable
config item pasted into an external system (a GitHub Actions secret, a CI
pipeline var), not a short-TTL per-turn grant. So instead of an expiry, the
token embeds a ``version`` int that must match the topic's current version in
``webhook_tokens`` (app.domain.webhook.models.WebhookToken). Minting bumps that
version and signs a token embedding the new value; any token embedding an
older version — the entire population of previously-minted tokens — instantly
stops verifying. That's how rotation and revocation both work without ever
storing the live secret itself.
"""

import base64
import json

from app.core.hmac_token import constant_time_equal, sign_body
from app.core.sandbox_auth import SANDBOX_TOKEN

# The key is the sandbox signing secret: a webhook credential and a sandbox
# credential are signed by the same deployment secret, and each family's own
# prefix/signing domain is what keeps one from verifying as the other.
_SECRET = SANDBOX_TOKEN


def mint_webhook_token(*, project_id: str, topic_id: str, version: int) -> str:
    """Sign a webhook credential for (project_id, topic_id) embedding `version`.
    No expiry — validity is governed entirely by the version match at verify
    time, not by when this was minted."""
    payload = {"p": project_id, "t": topic_id, "v": version}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{body}.{sign_body(_SECRET, body)}"


def webhook_token_claims(token: str, *, topic_id: str) -> dict | None:
    """The {p, t, v} claims of `token` iff its signature is valid AND it is
    scoped to `topic_id` — else None. Does NOT check the version against the
    DB; the caller compares the returned `v` against WebhookTokenRepository's
    current_version()."""
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    if not constant_time_equal(sig, sign_body(_SECRET, body)):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("t") != topic_id:
        return None
    return payload
