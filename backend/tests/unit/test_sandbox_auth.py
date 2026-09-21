"""Scoped per-turn cheese token (review R5)."""

import base64
import json
import uuid

from app.core import sandbox_auth as sa
from app.domain.identity.handles import agent_instance_handle


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
    tok = sa.mint_scoped_token(project_id="P", topic_id="T", agent_handle="cheese-1")
    body = tok.split(".", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert set(payload) == {"p", "t", "exp", "a"}
    assert sa.SANDBOX_TOKEN not in body  # the signing secret never ships in the token


# --- an agent signs with its own handle: a token says WHO acts, not just where -


def test_token_names_the_agent_it_was_minted_for():
    """Without this claim every agent collapsed into one platform account: an
    action could not be traced to the agent that took it."""
    agent = agent_instance_handle(uuid.uuid4())
    tok = sa.mint_scoped_token(project_id="P", topic_id="T", agent_handle=agent)
    assert sa.token_agent_handle(tok) == agent


def test_two_agents_in_one_room_keep_two_identities():
    """A room may seat several agents. Anything the room could answer here
    would give both of them one name — so the room answers nothing, and the two
    tokens keep the two names they were minted with."""
    room = str(uuid.uuid4())
    handles = {
        sa.token_agent_handle(
            sa.mint_scoped_token(project_id="P", topic_id=room, agent_handle=who)
        )
        for who in (
            agent_instance_handle(uuid.uuid4()),
            agent_instance_handle(uuid.uuid4()),
        )
    }
    assert len(handles) == 2


def test_one_agent_keeps_one_identity_across_rooms():
    """The same agent in two rooms is one collaborator: its seat, and
    everything authored under it, stays valid wherever it is standing."""
    agent = agent_instance_handle(uuid.uuid4())
    minted = {
        sa.token_agent_handle(
            sa.mint_scoped_token(
                project_id="P", topic_id=str(uuid.uuid4()), agent_handle=agent
            )
        )
        for _ in range(2)
    }
    assert minted == {agent}


def test_a_token_minted_without_an_agent_claims_no_identity():
    """Minting derives no name: a project-wide capability (git-http / LLM proxy)
    names nobody because it names no room either, and a room-scoped token that
    pinned no teammate leaves the answer to that room's roster instead of to a
    name derived here — which would put writes into an agent's audit trail on
    the strength of where the token was minted."""
    assert sa.token_agent_handle(sa.mint_scoped_token(project_id="P")) is None
    assert (
        sa.token_agent_handle(sa.mint_scoped_token(project_id="P", topic_id="T"))
        is None
    )


def test_identity_is_signed_not_merely_carried():
    """The claim is inside the HMAC body — swapping it invalidates the token, so
    a sandbox cannot rename itself into another 分身."""
    tok = sa.mint_scoped_token(
        project_id="P",
        topic_id=str(uuid.uuid4()),
        agent_handle=agent_instance_handle(uuid.uuid4()),
    )
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
    expired = sa.mint_scoped_token(
        project_id="P",
        topic_id=str(uuid.uuid4()),
        ttl_s=-1,
        agent_handle=agent_instance_handle(uuid.uuid4()),
    )
    assert sa.token_agent_handle(expired) is None
    assert sa.token_agent_handle("not-a-token") is None
