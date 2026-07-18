"""Scoped per-turn cheese token (review R5)."""

import base64
import json

from app.core import sandbox_auth as sa


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
    assert set(payload) == {"p", "t", "exp"}
    assert sa.SANDBOX_TOKEN not in body  # the signing secret never ships in the token
