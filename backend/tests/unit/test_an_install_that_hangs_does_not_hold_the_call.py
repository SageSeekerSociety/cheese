"""One machine that stopped answering costs an attempt, not the whole call.

Observed on dev on 2026-10-15: two tool calls of one session were answered
"执行环境仍在准备，对话和平台工具仍可用" after eleven minutes each, and nothing
in between had failed. Both halves were true at once: the install command was
given the same 660 s the tool call itself has, so a hung machine never failed
inside the call that waited on it; and a Cloud host that could not install was
not given up, so the next call went back to it. The budget the caller has is
what the deadline has to leave room inside, and a shared host that cannot bring
the executor up is one the session is placed away from.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.agent import execution
from app.domain.agent.device_hub import DeviceOffline
from app.domain.agent.harness.claude_code import executor_launch
from app.domain.machine import lease_claim, session_work
from app.domain.machine.sandbox_wait import SANDBOX_PREPARING

pytestmark = pytest.mark.anyio

#: What one tool call has in full (`executor_transport.call` sets its operation
#: deadline 660 s out). The install's deadline and the claim that outlives it
#: both have to fit inside this, or the attempt cannot fail while its caller is
#: still waiting.
CALL_BUDGET_S = 660.0

INFO = {"state": "/executor/state", "workspace": "/executor/work", "mcp_servers": []}


async def _start(hub, lease):
    return await session_work._start_executor(
        hub,
        lease,
        device_id="machine",
        project_id=uuid.uuid4(),
        work_resource=str(uuid.uuid4()),
        setup={},
        sandbox=False,
        platform_machine=True,
    )


async def test_the_install_is_given_less_than_the_call_that_waits_for_it(monkeypatch):
    """The machine is asked to finish — or stop answering — inside the call.

    Given the whole 660 s, a hung install consumed the call whole: the request
    gave up waiting without the attempt ever failing, and the claim stood for
    the same 660 s, so the next call found it still taken.
    """
    monkeypatch.setattr(executor_launch, "script", lambda *a, **k: "print('in')")
    hub = SimpleNamespace(
        exec=AsyncMock(return_value={"exit": 0, "stdout": json.dumps(INFO)})
    )

    assert await _start(hub, None) == INFO

    timeout = hub.exec.await_args.kwargs["timeout"]
    assert timeout == lease_claim.INSTALL_MOST_S
    assert timeout < CALL_BUDGET_S, "the install can outlast the call that waits"
    # The claim outlives the install by one TTL, so it too has to fit.
    assert lease_claim.CLAIM_MOST_S < CALL_BUDGET_S


async def test_the_claim_outlives_the_whole_installation_not_just_the_install():
    """One installation makes its calls into the machine one after another —
    the ping and the prepare that decide how to start the executor, the install,
    and the ping that asks the installed executor to answer — each of them
    bounded by INSTALL_MOST_S. A claim that covers only the install itself
    lapses with the installation still running, and the next request of the same
    session starts a second installation of it on the same machine; the first is
    then answered ``ConflictError`` when it hands its lease over.
    """
    assert lease_claim.CLAIM_MOST_S >= 3 * lease_claim.INSTALL_MOST_S
    assert lease_claim.CLAIM_MOST_S < CALL_BUDGET_S, "the waiting call runs out first"


async def test_the_calls_before_the_install_are_bounded_the_same_way(monkeypatch):
    """A hung executor is asked for its ping and its prepare on the way in; a
    deadline on the install alone would leave those holding the call."""
    monkeypatch.setattr(executor_launch, "can_prepare", lambda *a, **k: True)
    monkeypatch.setattr(executor_launch, "payload_for", lambda *a, **k: {"prepared": 1})
    seen: list[float | None] = []

    async def call(_lease, method, _params, **kwargs):
        seen.append(kwargs.get("timeout"))
        return {} if method == "ping" else INFO

    monkeypatch.setattr(execution, "call", call)
    hub = SimpleNamespace(exec=AsyncMock())

    assert await _start(hub, {"state": "/executor"}) == INFO

    assert seen == [lease_claim.INSTALL_MOST_S] * len(seen)
    assert seen, "the ping and the prepare were never made"


@pytest.mark.parametrize(
    "result, killed",
    [
        # The connector killed the command at its deadline: no exit status of
        # its own, and no account of why in its stderr.
        ({"exit": -1, "stderr": "signal: killed"}, True),
        ({"exit": -1, "stderr": ""}, True),
        # The command ran and chose to fail: this one has a reason to report,
        # and the machine is not the problem.
        ({"exit": 1, "stderr": "no such file"}, False),
        # Killed that way but with output dropped: still not an account of why.
        ({"exit": -1, "stderr": "x", "truncated": True}, True),
    ],
)
async def test_only_a_command_that_never_exited_reads_as_a_machine_that_stopped(
    monkeypatch, result, killed
):
    """The connector answers a killed command with an ordinary result, so the
    exit status is the only thing that says whether the machine ever got to
    answer. Told apart here, so the caller can tell its host to give up rather
    than report an install failure that pins the session where it is."""
    monkeypatch.setattr(executor_launch, "script", lambda *a, **k: "print('in')")
    hub = SimpleNamespace(exec=AsyncMock(return_value=result))

    with pytest.raises(session_work.ExecutorSetupFailed) as raised:
        await _start(hub, None)

    assert raised.value.killed is killed


class _Db:
    """The installation's own session, enough of it to record the outcome."""

    def __init__(self, *args, **kwargs):
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.commits += 1


class _Row:
    def __init__(self):
        self.work_lease = {"claim": "claim-1"}
        self.execution_request = None
        self.conversation_id = uuid.uuid4()


class _Topic:
    resource_id = "resource"


class _Topics:
    """The room, enough of it for the installation to hand its lease over."""

    def __init__(self, db):
        pass

    async def lock_for_execution(self, topic_id):
        return _Topic()


class _Sessions:
    def __init__(self, db):
        self.row = _Row()

    async def by_id(self, session_id, lock=False):
        return self.row


class _Pool:
    """The host pool, recording what the installation asked of it.

    ``answered`` is what the pool has to say about the session being placed
    elsewhere: True when it has another host that can run the sandbox.
    """

    answered = True
    asked: list[tuple[uuid.UUID, uuid.UUID]] = []

    def __init__(self, db, hub=None):
        self.db = db

    async def leave_unusable(self, session_id, host_id):
        type(self).asked.append((session_id, host_id))
        return type(self).answered

    async def tell_ready(self, session_id):
        return None


def _install_case(
    monkeypatch,
    *,
    exc: BaseException | None = None,
    result=None,
    host_id=None,
    whole=False,
    answered=True,
):
    """One installation on a Cloud host, failing the way ``exc`` says.

    ``result`` instead runs the real ``_start_executor`` against a machine whose
    connector answered that — so what actually comes back over the wire is
    translated by the code under test, not hand-made into the exception.
    """
    _Pool.answered = answered
    _Pool.asked = []
    monkeypatch.setattr(session_work, "AsyncSession", _Db)
    monkeypatch.setattr(session_work, "AgentSessionService", _Sessions)
    monkeypatch.setattr(session_work, "HostPool", _Pool)
    monkeypatch.setattr(session_work, "TopicService", _Topics)
    monkeypatch.setattr(session_work, "publish_line", AsyncMock())
    monkeypatch.setattr(
        session_work, "bind_resource_token", lambda *a, **k: "scoped-token"
    )
    monkeypatch.setattr(lease_claim, "kept", _kept)

    if result is None:
        assert exc is not None, "a case fails either by raising or by an answer"

        async def install(*_args, **_kwargs):
            raise exc

        monkeypatch.setattr(session_work, "_start_executor", install)
        hub = SimpleNamespace()
    else:
        monkeypatch.setattr(executor_launch, "script", lambda *a, **k: "print('in')")
        hub = SimpleNamespace(exec=AsyncMock(return_value=result))
    return session_work._install(
        "bind",
        token="launch-token",
        resource="resource",
        session_id=uuid.uuid4(),
        generation="1",
        env={},
        api="http://localhost:8000",
        host_api="http://localhost:8000",
        project_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        room_id=uuid.uuid4(),
        actor_handle="someone",
        work_resource=str(uuid.uuid4()),
        hub=hub,
        lease={"device_id": "dev-1"},
        device_id="dev-1",
        sandbox=False,
        platform_machine=True,
        # What the lease carries while the install runs, handed over without
        # them: the claim the installation took, and when it stands until —
        # one TTL past now, the way `_attempt` writes it, so a case that ever
        # runs against the real `kept` still reads as one somebody is running.
        reservation={
            "device_id": "dev-1",
            "claim": "claim-1",
            "claim_until": (
                datetime.now(UTC) + timedelta(seconds=lease_claim.CLAIM_TTL_S)
            ).isoformat(),
        },
        holding={"device_id": "dev-1"},
        claim="claim-1",
        now=datetime.now(UTC),
        cloud_host_id=host_id,
        owner_device=False,
        own=False,
        # The default is a shared host; only the session's own VM says so.
        **({"cloud_vm": True} if whole else {}),
    )


class _Kept:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


_kept = lambda *_a, **_k: _Kept()  # noqa: E731 — a context manager, not a value


async def test_a_shared_host_that_cannot_install_the_executor_is_left(monkeypatch):
    """The connector went away mid-install. Nothing on a shared host belongs to
    one session — it is placed elsewhere instead of waiting for a sweep that
    gives the host up only after ten minutes of silence."""
    host_id = uuid.uuid4()

    outcome = await _install_case(
        monkeypatch, host_id=host_id, exc=DeviceOffline("dev-1")
    )

    assert _Pool.asked, "the pool was never asked to move the session"
    assert _Pool.asked[-1][1] == host_id
    assert outcome.message == SANDBOX_PREPARING
    assert await outcome.progress() is True, "the caller is not told to try again"


async def test_an_install_that_times_out_on_a_shared_host_is_left_the_same_way(
    monkeypatch,
):
    """The deadline in this change is what raises here — the machine answered
    nothing for two minutes. It is the same fact as a connector that left, and
    is answered the same way."""
    host_id = uuid.uuid4()

    outcome = await _install_case(
        monkeypatch, host_id=host_id, exc=TimeoutError("no answer")
    )

    assert [asked[1] for asked in _Pool.asked] == [host_id]
    assert outcome.message == SANDBOX_PREPARING


async def test_a_shared_host_is_waited_for_when_nowhere_else_can_run_it(
    monkeypatch,
):
    """The pool has no other host: leaving would take the session's slot and
    leave it waiting for the same machine, so it stays and the call is answered
    that the environment is still being prepared."""
    outcome = await _install_case(
        monkeypatch, host_id=uuid.uuid4(), exc=TimeoutError("no answer"), answered=False
    )

    assert _Pool.asked, "the pool was not asked"
    assert outcome is session_work._CLOUD_PREPARING


async def test_an_install_killed_at_its_deadline_leaves_the_shared_host(monkeypatch):
    """The hang this change is about does not arrive as silence: asked to wait
    two minutes, the connector waits, kills the command and answers with no exit
    status. Before this, that read as an install that failed, the room was told
    so, and the next attempt was placed on the same host."""
    host_id = uuid.uuid4()

    outcome = await _install_case(
        monkeypatch,
        host_id=host_id,
        result={"exit": -1, "stdout": "", "stderr": ""},
    )

    assert [asked[1] for asked in _Pool.asked] == [host_id]
    assert outcome.message == SANDBOX_PREPARING
    assert await outcome.progress() is True, "the caller is not told to try again"


async def test_the_decision_to_leave_is_not_made_from_the_machines_own_words(
    monkeypatch,
):
    """An install the machine ran and failed is a different fact from a machine
    that never got to answer, and only the second one is given up."""
    outcome = await _install_case(
        monkeypatch,
        host_id=uuid.uuid4(),
        result={"exit": 1, "stdout": "", "stderr": "no space left on device\n"},
    )

    assert _Pool.asked == [], "a host that answered was given up"
    assert "no space left on device" in outcome["unavailable"]


async def test_a_machine_that_is_nobody_s_cloud_host_keeps_its_old_answer(monkeypatch):
    """A person's own machine is not in the pool and has nowhere to be placed
    away from: the room is told the executor did not come up, as before."""
    outcome = await _install_case(
        monkeypatch,
        host_id=None,
        exc=session_work.ExecutorSetupFailed("signal: killed", killed=True),
    )

    assert _Pool.asked == []
    assert "unavailable" in outcome


async def test_an_install_that_ran_and_failed_is_still_reported_to_the_room(
    monkeypatch,
):
    """A machine that answered is not a machine to leave: the reason it gave is
    what the room reads, and leaving would take the session's slot away over an
    install that only has to be fixed."""
    outcome = await _install_case(
        monkeypatch,
        host_id=uuid.uuid4(),
        exc=session_work.ExecutorSetupFailed("no space left on device"),
    )

    assert _Pool.asked == [], "a host that answered was given up anyway"
    assert "no space left on device" in outcome["unavailable"]


async def test_a_whole_vm_is_not_left_when_its_install_fails(monkeypatch):
    """A session's own VM is not a slot: there is no other host to place it on,
    and the machine coming back is the only thing that helps."""
    _Pool.answered = True

    outcome = await _install_case(
        monkeypatch,
        host_id=uuid.uuid4(),
        whole=True,
        exc=TimeoutError("no answer"),
    )

    assert _Pool.asked == [], "the session's own VM was given up"
    assert outcome is session_work._CLOUD_PREPARING


async def test_a_whole_vm_whose_install_was_killed_is_waited_for(monkeypatch):
    """The session's own VM is the one machine that is not given up, whatever
    the install did: there is no other host to place it on, so a killed install
    is answered by waiting where it is, exactly as one that timed out."""
    outcome = await _install_case(
        monkeypatch,
        host_id=uuid.uuid4(),
        whole=True,
        result={"exit": -1, "stdout": "", "stderr": ""},
    )

    assert _Pool.asked == [], "the session's own VM was given up"
    assert outcome is session_work._CLOUD_PREPARING


async def test_the_installed_executor_is_asked_to_answer_inside_the_same_budget(
    monkeypatch,
):
    """The executor is installed and running; the attempt ends by asking it to
    answer, and that too is a call into the machine. Half the machines this
    change is about hung here, after installing — an unbounded ping would hold
    the call that waited exactly like the install did."""
    seen: list[float | None] = []

    async def call(_target, method, _params, **kwargs):
        seen.append(kwargs.get("timeout"))
        return {}

    monkeypatch.setattr(execution, "call", call)

    outcome = await _install_case(
        monkeypatch,
        host_id=uuid.uuid4(),
        result={"exit": 0, "stdout": json.dumps(INFO), "stderr": ""},
    )

    assert seen == [lease_claim.INSTALL_MOST_S], "the installed executor was not asked"
    assert outcome["target"]["state"] == INFO["state"]
