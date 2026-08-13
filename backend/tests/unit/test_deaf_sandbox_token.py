"""A sandbox whose hook token stopped verifying is DEAF, and used to stay deaf.

The failure shape, which is what made this expensive: the turn observes nothing
at all. `first_output_s: null`, `tools: 0`, runs to its ceiling — identical to a
model that never spoke, so every hypothesis went to the model side (credentials,
gateway, disk, container creation) while `claude` inside the box was working
perfectly and its every hook was being 401'd and dropped without a log line.

The mechanism:

* the hook forwarder sends the container's baked ``CHEESE_TOKEN`` on every event;
* that token is baked at container CREATION and never refreshed — the box is
  long-lived and reused across many turns, and the freshly minted per-turn token
  never reaches it;
* ``sandbox_auth.SANDBOX_TOKEN`` falls back to a fresh random per PROCESS when
  the deployment does not pin it, so a backend restart re-signs with a new secret
  and every existing box's token stops verifying at once;
* nothing rebuilt those boxes. The image tag only changes when a merge changes
  it, so a restart alone left every topic permanently unable to reply.

Two of the three defects are covered here (the box must be rebuilt; a deployment
that will do this on every restart must say so at boot). The third — the 401
must leave a trace — goes over HTTP, so it lives in
`tests/integration/test_rejected_hook_is_visible.py`.
"""

import uuid

import pytest

from app.core import sandbox_auth
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.tmux_provider import TmuxHooksProvider

PROJECT = str(uuid.uuid4())
TOPIC = str(uuid.uuid4())


def _env_dump(**pairs: str) -> str:
    """What `docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}'` prints."""
    return "".join(f"{k}={v}\n" for k, v in pairs.items())


def _inspect_returning(out: str, rc: int = 0):
    async def _docker(*args: str):
        return rc, out, ""

    return _docker


@pytest.mark.anyio
async def test_a_box_signed_by_a_previous_process_is_declared_dead(monkeypatch):
    """The exact production shape: the box was baked while the backend held a
    different signing secret (i.e. before its last restart)."""
    stale = mint_scoped_token(project_id=PROJECT, topic_id=TOPIC)
    # The restart: a new process, a new random secret.
    monkeypatch.setattr(sandbox_auth, "_SECRET", b"a-different-process-secret")

    monkeypatch.setattr(
        "app.domain.agent.tmux_provider._docker",
        _inspect_returning(
            _env_dump(CHEESE_TOKEN=stale, CHEESE_PROJECT=PROJECT, CHEESE_TOPIC=TOPIC)
        ),
    )

    assert await TmuxHooksProvider._hook_token_dead("box") is True


@pytest.mark.anyio
async def test_a_box_whose_token_still_verifies_is_left_alone(monkeypatch):
    """Rebuilding costs the topic its tmux session — its conversational
    continuity. A healthy box must never pay that."""
    good = mint_scoped_token(project_id=PROJECT, topic_id=TOPIC)
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider._docker",
        _inspect_returning(
            _env_dump(CHEESE_TOKEN=good, CHEESE_PROJECT=PROJECT, CHEESE_TOPIC=TOPIC)
        ),
    )

    assert await TmuxHooksProvider._hook_token_dead("box") is False


@pytest.mark.anyio
async def test_a_token_scoped_to_another_topic_is_dead(monkeypatch):
    """Signature alone is not enough — a token that verifies but names a
    different topic cannot reach this one's hook endpoint either."""
    other = mint_scoped_token(project_id=PROJECT, topic_id=str(uuid.uuid4()))
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider._docker",
        _inspect_returning(
            _env_dump(CHEESE_TOKEN=other, CHEESE_PROJECT=PROJECT, CHEESE_TOPIC=TOPIC)
        ),
    )

    assert await TmuxHooksProvider._hook_token_dead("box") is True


@pytest.mark.anyio
async def test_a_box_with_no_baked_token_is_not_declared_dead(monkeypatch):
    """Unknown is not evidence of death — same rule as the env stamp. A box
    predating scoped hook tokens must not be destroyed on a guess."""
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider._docker",
        _inspect_returning(_env_dump(CHEESE_PROJECT=PROJECT, CHEESE_TOPIC=TOPIC)),
    )

    assert await TmuxHooksProvider._hook_token_dead("box") is False


@pytest.mark.anyio
async def test_a_failed_inspect_never_destroys_a_box(monkeypatch):
    """`docker inspect` failing says nothing about the token. Treating "can't
    tell" as "dead" would rebuild every box (and drop every session) the moment
    the docker socket hiccups."""
    monkeypatch.setattr(
        "app.domain.agent.tmux_provider._docker", _inspect_returning("", rc=1)
    )

    assert await TmuxHooksProvider._hook_token_dead("box") is False


@pytest.mark.anyio
async def test_a_dead_token_actually_triggers_a_rebuild(monkeypatch):
    """The wiring, not just the predicate.

    `_hook_token_dead` deciding "dead" is worth nothing if `_ensure_container`
    does not act on it — and that is precisely the shape of the original bug: a
    box whose token no longer worked, with nothing anywhere that rebuilt it.
    """
    stale = mint_scoped_token(project_id=PROJECT, topic_id=TOPIC)
    monkeypatch.setattr(sandbox_auth, "_SECRET", b"a-different-process-secret")

    calls: list[tuple[str, ...]] = []
    env_line = _env_dump(CHEESE_TOKEN=stale, CHEESE_PROJECT=PROJECT, CHEESE_TOPIC=TOPIC)

    async def _docker(*args: str):
        calls.append(args)
        if args[0] == "inspect" and "{{.Config.Image}}" in args:
            return 0, "the-current-image", ""
        if args[0] == "inspect" and "Config.Env" in " ".join(args):
            return 0, env_line, ""
        return 0, "", ""

    provider = TmuxHooksProvider.__new__(TmuxHooksProvider)
    provider._image = "the-current-image"  # type: ignore[attr-defined]
    created: list[str] = []

    async def _create(name: str, env: dict) -> None:
        created.append(name)

    monkeypatch.setattr("app.domain.agent.tmux_provider._docker", _docker)
    monkeypatch.setattr(TmuxHooksProvider, "_create_container", staticmethod(_create))
    monkeypatch.setattr(
        TmuxHooksProvider, "_cli_mount_stale", staticmethod(lambda *a: _false())
    )
    monkeypatch.setattr("app.domain.agent.tmux_provider.warn_container_rebuilt", _noop)

    await provider._ensure_container(
        uuid.UUID(TOPIC),
        {"SBX_SESSION": "/tmp/session", "CHEESE_API": "http://backend"},
    )

    assert any(args[:2] == ("rm", "-f") for args in calls), (
        "a box that can no longer be heard must be rebuilt, not reused"
    )
    assert created, "and a fresh one put in its place"


async def _false() -> bool:
    return False


async def _noop(*args, **kwargs) -> None:
    return None
