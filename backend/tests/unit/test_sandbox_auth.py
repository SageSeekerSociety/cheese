"""Scoped per-turn cheese token (review R5)."""

import base64
import json
import uuid

from app.core import sandbox_auth as sa
from app.domain.identity.handles import topic_agent_handle


def test_scoped_token_roundtrip_matches_only_its_claims():
    tok = sa.mint_scoped_token(project_id="P", topic_id="T")
    assert sa.verify_scoped_token(tok, project_id="P", topic_id="T")
    assert sa.verify_scoped_token(tok, topic_id="T")
    assert sa.verify_scoped_token(tok, project_id="P")
    # Wrong project / topic → rejected (tenant isolation).
    assert not sa.verify_scoped_token(tok, project_id="OTHER")
    assert not sa.verify_scoped_token(tok, topic_id="OTHER")


def test_tampered_signature_rejected():
    tok = sa.mint_scoped_token(project_id="P", topic_id="T")
    body, _sig = tok.split(".", 1)
    forged = f"{body}.{'A' * 43}"
    assert not sa.verify_scoped_token(forged, project_id="P", topic_id="T")


def test_expired_token_rejected():
    tok = sa.mint_scoped_token(project_id="P", topic_id="T", ttl_s=-1)
    assert not sa.verify_scoped_token(tok, project_id="P", topic_id="T")


def test_garbage_token_rejected():
    assert not sa.verify_scoped_token("not-a-token", project_id="P")
    assert not sa.verify_scoped_token("a.b.c", project_id="P")


def test_is_valid_accepts_scoped_or_global():
    scoped = sa.mint_scoped_token(project_id="P", topic_id="T")
    assert sa.is_valid_cheese_token(scoped, topic_id="T")
    # a scoped token for another topic is NOT valid for this topic
    assert not sa.is_valid_cheese_token(scoped, topic_id="OTHER")
    # the global secret is accepted (dev / trusted-single-host override)
    assert sa.is_valid_cheese_token(sa.SANDBOX_TOKEN, topic_id="anything")
    # nothing matches with no scope
    assert not sa.is_valid_cheese_token(scoped)


def test_token_payload_has_no_secret():
    tok = sa.mint_scoped_token(project_id="P", topic_id="T")
    body = tok.split(".", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert set(payload) == {"p", "t", "exp", "a"}
    assert sa.SANDBOX_TOKEN not in body  # the signing secret never ships in the token


# --- 分身独立身份: a token says WHO acts, not just where ------------------------


def test_token_names_the_topics_agent():
    """Without this claim every 分身 collapsed into one platform account: an
    action could not be traced to the 分身 that took it."""
    topic = uuid.uuid4()
    tok = sa.mint_scoped_token(project_id="P", topic_id=str(topic))
    assert sa.token_agent_handle(tok) == topic_agent_handle(topic)


def test_two_topics_get_two_identities():
    a, b = uuid.uuid4(), uuid.uuid4()
    handles = {
        sa.token_agent_handle(sa.mint_scoped_token(project_id="P", topic_id=str(t)))
        for t in (a, b)
    }
    assert len(handles) == 2


def test_the_same_topic_always_gets_the_same_identity():
    """Derived, not allocated — a re-minted token names the same 分身, so its
    roster seat (and everything authored under it) stays valid across turns."""
    topic = str(uuid.uuid4())
    first = sa.mint_scoped_token(project_id="P", topic_id=topic)
    second = sa.mint_scoped_token(project_id="P", topic_id=topic)
    assert sa.token_agent_handle(first) == sa.token_agent_handle(second)


def test_project_scoped_token_names_no_agent():
    """A project-wide capability (git-http / LLM proxy) is not a 分身 acting."""
    assert sa.token_agent_handle(sa.mint_scoped_token(project_id="P")) is None


def test_identity_is_signed_not_merely_carried():
    """The claim is inside the HMAC body — swapping it invalidates the token, so
    a sandbox cannot rename itself into another 分身."""
    tok = sa.mint_scoped_token(project_id="P", topic_id=str(uuid.uuid4()))
    body, sig = tok.split(".", 1)
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["a"] = "cheese-deadbeefcafe"
    forged_body = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    assert sa.token_agent_handle(f"{forged_body}.{sig}") is None


def test_expired_or_garbage_token_names_nobody():
    expired = sa.mint_scoped_token(project_id="P", topic_id=str(uuid.uuid4()), ttl_s=-1)
    assert sa.token_agent_handle(expired) is None
    assert sa.token_agent_handle("not-a-token") is None
