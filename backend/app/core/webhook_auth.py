"""Auth for the external-facing webhook ingress (POST /webhooks/{topic_id}).

A webhook credential is a long-lived, revocable/rotatable config item pasted
into an external system (a GitHub Actions secret, a CI pipeline var), not a
short-TTL per-turn grant. So instead of an expiry, the token embeds a
``version`` int that must match the topic's current version in
``webhook_tokens`` (app.domain.webhook.models.WebhookToken). Minting bumps that
version and signs a token embedding the new value; any token embedding an
older version — the entire population of previously-minted tokens — instantly
stops verifying. That's how rotation and revocation both work without ever
storing the live secret itself.
"""

from app.core import credentials
from app.core.credentials import Kind


def mint_webhook_token(*, project_id: str, topic_id: str, version: int) -> str:
    """Sign a webhook credential for (project_id, topic_id) embedding `version`.
    No expiry — validity is governed entirely by the version match at verify
    time, not by when this was minted."""
    return credentials.mint(
        Kind.WEBHOOK, {"p": project_id, "t": topic_id, "v": version}
    )


def webhook_token_claims(token: str, *, topic_id: str) -> dict | None:
    """The {p, t, v} claims of `token` iff its signature is valid AND it is
    scoped to `topic_id` — else None. Does NOT check the version against the
    DB; the caller compares the returned `v` against WebhookTokenRepository's
    current_version()."""
    payload = credentials.read(token, Kind.WEBHOOK)
    if payload is None or payload.get("t") != topic_id:
        return None
    return payload
