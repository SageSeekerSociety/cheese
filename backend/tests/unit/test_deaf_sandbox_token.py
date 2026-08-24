"""A sandbox whose hook token stopped verifying is DEAF, and used to stay deaf.

The failure shape, which is what made this expensive: the turn observes nothing
at all. `first_output_s: null`, `tools: 0`, runs to its ceiling — identical to a
model that never spoke, so every hypothesis went to the model side (credentials,
gateway, disk, container creation) while `claude` inside the box was working
perfectly and its every hook was being 401'd and dropped without a log line.

The mechanism:

* the hook forwarder sends the ``CHEESE_TOKEN`` its `claude` was launched with,
  on every event;
* that token is written into the tmux session's environment at session creation
  and never refreshed — `claude` read it once at exec, and the session is reused
  across many turns, so the freshly minted per-turn token never reaches it;
* ``sandbox_auth.SANDBOX_TOKEN`` used to fall back to a fresh random per PROCESS
  when the deployment did not pin it, so a backend restart re-signed with a new
  secret and every live session's token stopped verifying at once;
* nothing rebuilt them. The image tag only changes when a merge changes it, so a
  restart alone left every topic permanently unable to reply.

The THIRD bullet is now fixed at the root: the signing secret is derived from
`jwt_secret` when unpinned, so it survives a restart and the deployment-wide
outage cannot happen at all (first test below). The rebuild path stays as the
backstop for what remains — a deliberately rotated SANDBOX_TOKEN, a token past
its TTL — and it now costs only the SESSION, not the whole room's box.

The remaining defect — the 401 must leave a trace — goes over HTTP, so it lives
in `tests/integration/test_rejected_hook_is_visible.py`.
"""

import uuid

import pytest

from app.core import sandbox_auth
from app.core.config import Settings
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.tmux_provider import TmuxChannel, TmuxScreen

PROJECT = str(uuid.uuid4())
TOPIC = str(uuid.uuid4())
SCREEN = TmuxScreen("room-box", "cheese-deadbeef")


def _session_env(**pairs: str):
    """What `docker exec <box> tmux show-environment -t <session> KEY` prints:
    ``KEY=value`` when set, ``-KEY`` when not."""

    async def _docker(*args: str, stdin=None):
        key = args[-1]
        return (0, f"{key}={pairs[key]}\n", "") if key in pairs else (0, f"-{key}", "")

    return _docker


def test_the_signing_secret_survives_a_restart_without_being_pinned():
    """The root cause, and the reason the whole class of outage is gone: two
    processes of the same deployment must derive the SAME secret when
    SANDBOX_TOKEN is not pinned. A random per process re-signed everything on
    every restart and took the whole deployment deaf at once."""
    one = Settings(jwt_secret="a-real-deployment-secret")
    two = Settings(jwt_secret="a-real-deployment-secret")
    assert one.sandbox_signing_secret == two.sandbox_signing_secret
    # ...and it is neither empty nor the jwt secret itself (a domain separator
    # keeps a rotation of one from silently rotating the other).
    assert one.sandbox_signing_secret
    assert one.sandbox_signing_secret != one.jwt_secret
    # A pinned value still wins, so an operator can rotate it independently.
    assert Settings(sandbox_token="pinned").sandbox_signing_secret == "pinned"
    # A different deployment gets a different secret.
    assert (
        Settings(jwt_secret="another-deployment").sandbox_signing_secret
        != one.sandbox_signing_secret
    )


@pytest.mark.anyio
async def test_a_session_signed_by_a_rotated_secret_is_declared_dead(monkeypatch):
    """The backstop: SANDBOX_TOKEN deliberately rotated under a live session."""
    stale = mint_scoped_token(project_id=PROJECT, topic_id=TOPIC)
    monkeypatch.setattr(sandbox_auth, "_SECRET", b"a-rotated-secret")
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider._docker",
        _session_env(CHEESE_TOKEN=stale, CHEESE_PROJECT=PROJECT, CHEESE_TOPIC=TOPIC),
    )

    provider = TmuxChannel(image="img:test")
    assert await provider._hook_token_dead(SCREEN) is True


@pytest.mark.anyio
async def test_a_session_whose_token_still_verifies_is_left_alone(monkeypatch):
    """Restarting costs the topic its tmux session — its conversational
    continuity. A healthy session must never pay that."""
    good = mint_scoped_token(project_id=PROJECT, topic_id=TOPIC)
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider._docker",
        _session_env(CHEESE_TOKEN=good, CHEESE_PROJECT=PROJECT, CHEESE_TOPIC=TOPIC),
    )

    provider = TmuxChannel(image="img:test")
    assert await provider._hook_token_dead(SCREEN) is False


@pytest.mark.anyio
async def test_a_token_scoped_to_another_topic_is_dead(monkeypatch):
    """Signature alone is not enough — a token that verifies but names a
    different topic cannot reach this one's hook endpoint either. In a shared
    box this is also the shape a leaked-by-inheritance token would take: a
    sibling's perfectly valid credential."""
    other = mint_scoped_token(project_id=PROJECT, topic_id=str(uuid.uuid4()))
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider._docker",
        _session_env(CHEESE_TOKEN=other, CHEESE_PROJECT=PROJECT, CHEESE_TOPIC=TOPIC),
    )

    provider = TmuxChannel(image="img:test")
    assert await provider._hook_token_dead(SCREEN) is True


@pytest.mark.anyio
async def test_a_session_with_no_token_is_not_declared_dead(monkeypatch):
    """Unknown is not evidence of death — same rule as the env stamp. A session
    predating scoped hook tokens must not be destroyed on a guess."""
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider._docker",
        _session_env(CHEESE_PROJECT=PROJECT, CHEESE_TOPIC=TOPIC),
    )

    provider = TmuxChannel(image="img:test")
    assert await provider._hook_token_dead(SCREEN) is False


@pytest.mark.anyio
async def test_a_failed_probe_never_destroys_a_session(monkeypatch):
    """`docker exec` failing says nothing about the token. Treating "can't tell"
    as "dead" would restart every session (and drop every conversation) the
    moment the docker socket hiccups."""

    async def _docker(*args: str, stdin=None):
        return 1, "", "cannot connect to the docker daemon"

    monkeypatch.setattr("app.domain.agent.tmux_provider._docker", _docker)

    provider = TmuxChannel(image="img:test")
    assert await provider._hook_token_dead(SCREEN) is False
