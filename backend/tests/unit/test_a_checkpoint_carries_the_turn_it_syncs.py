"""The sync a turn ends with is told which turn it was.

A push that fails is written into 现场 under the turn whose checkpoint ran. That
turn is not guessed after the fact — the platform's checkpoint names it, and
every harness passes the id along: the runner's own turn for pi and Codex, and
the runner's answer to `checkpoint_turn` for Claude Code.
"""

import asyncio
import io
import json

from app.domain.agent.harness.claude_code.remote_execution import (
    client,
    runtime_calls,
)
from app.domain.agent.harness.driven.runner import Runner


def _executor(recorded):
    class Executor:
        def hooks(self, *args):
            return None

        def invoke(self, params):
            recorded.update(params)
            return {"value": {}}

    return Executor()


def test_a_checkpoint_tells_the_sync_which_turn_it_ends():
    """The controls's turn reaches the command as CHEESE_TURN; the CLI forwards
    it as X-Cheese-Turn and the note joins that turn."""
    recorded = {}
    runtime_calls.control(
        _executor(recorded),
        {"subtype": "checkpoint", "request_id": "r1", "turn": "turn-1"},
    )

    assert recorded["args"]["platform_env"] == {"CHEESE_TURN": "turn-1"}


def test_a_checkpoint_without_a_turn_leaves_the_environment_alone():
    recorded = {}
    runtime_calls.control(
        _executor(recorded), {"subtype": "checkpoint", "request_id": "r1"}
    )

    assert "platform_env" not in recorded["args"]


class _Journal:
    def __init__(self, values):
        self.values = values

    def recall(self, key):
        return self.values.get(key)


def test_a_driven_runner_reads_the_turn_when_the_turn_ends():
    """`turn_work` is read at the end, not when the sync runs: by then the
    next turn may already have started."""
    runner = Runner.__new__(Runner)
    runner.journal = _Journal({"owner": json.dumps({"work_id": "turn-1"})})
    assert runner.turn_work() == "turn-1"

    runner.journal = _Journal({"owner": "{}"})
    assert runner.turn_work() is None


def _driven(checkpointer):
    runner = Runner.__new__(Runner)
    runner.checkpointer = checkpointer
    runner.checkpointing = None
    runner.checkpoint_again = False
    runner.journal = _Journal({"owner": json.dumps({"work_id": "turn-1"})})
    return runner


def test_a_driven_runner_hands_the_turn_to_the_checkpoint():
    seen = []

    class Checkpointer:
        def __call__(self, request_id, turn):
            seen.append(turn)
            return {}

    runner = _driven(Checkpointer())
    asyncio.run(runner._checkpoint("turn-1"))
    asyncio.run(runner._checkpoint("turn-2"))

    assert seen == ["turn-1", "turn-2"]


def test_a_turn_that_ends_during_a_sync_retries_with_its_own_turn():
    """The retry exists to cover the newer turn; the note it files must be
    that turn's, not the first one's."""
    seen = []

    class Checkpointer:
        def __call__(self, request_id, turn):
            seen.append(turn)
            if len(seen) == 1:
                runner.checkpoint_again = True
                runner.journal = _Journal({"owner": json.dumps({"work_id": "turn-2"})})
            return {}

    runner = _driven(Checkpointer())
    asyncio.run(runner._checkpoint("turn-1"))

    assert seen == ["turn-1", "turn-2"]


def test_turn_ended_checkpoints_with_the_turn_it_reads():
    seen = []

    class Checkpointer:
        def __call__(self, request_id, turn):
            seen.append(turn)
            return {}

    runner = _driven(Checkpointer())

    async def ended():
        runner.turn_ended()
        await runner.checkpointing

    asyncio.run(ended())
    assert seen == ["turn-1"]


def test_codex_reads_the_turn_from_its_thread():
    """Codex names the work per thread, not in the owner record."""
    from types import SimpleNamespace

    from app.domain.agent.harness.codex.runner import Runner as CodexRunner

    runner = CodexRunner.__new__(CodexRunner)
    runner.journal = _Journal({"work:thread": "turn-1"})
    runner.session = SimpleNamespace(thread_id="thread")

    assert runner.turn_work() == "turn-1"


def test_the_checkpoint_request_carries_the_turn(monkeypatch):
    """`RemoteClient.checkpoint(turn)` names it in the control request."""
    from app.domain.agent import executor_transport

    client = executor_transport.RemoteClient({"kind": "device", "execution_token": "t"})
    captured = {}
    monkeypatch.setattr(
        client, "control", lambda request: captured.update(request) or {}
    )

    client.checkpoint("r1", turn="turn-1")
    client.checkpoint("r2")

    assert captured["turn"] == "turn-1"


def test_claude_codes_checkpoint_asks_the_runner_for_the_turn(monkeypatch):
    """`client.py checkpoint` runs after the turn ended; the runner still holds
    its id (`work`, or `last_work` once it has ended)."""
    asked = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def sendall(self, data):
            asked.append(json.loads(data)["method"])

        def makefile(self, mode):
            return io.BytesIO(
                json.dumps({"result": {"work_id": "turn-9"}}).encode() + b"\n"
            )

    monkeypatch.setenv(client.SESSION_SOCKET, "/tmp/session.sock")
    monkeypatch.setattr(client, "dial_runner", lambda path, timeout: Connection())

    assert client.checkpoint_turn() == "turn-9"
    assert asked == ["checkpoint_turn"]


def test_no_runner_to_ask_means_no_turn(monkeypatch):
    monkeypatch.delenv(client.SESSION_SOCKET, raising=False)
    assert client.checkpoint_turn() is None
