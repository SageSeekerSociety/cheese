"""A task's session starts in the task's checkout, a room's where it did.

A task's work is in a checkout of its own under the session's home on the
machine (`cheese worktree`), and the project view a room's session starts in is
not a checkout at all: an agent there spends its first commands — and part of
most later ones — spelling that UUID out. So the launch starts a task's session
where its work is, resolved by `cheese worktree` itself rather than by a path
composed here, and every other session keeps the directory it always started
in.
"""

import pytest

from app.domain.agent.executor_transport import DEFERRED_WORKSPACE
from app.domain.agent.harness.claude_code.remote_execution import client

ROOM = "/home/agent/.cheese/home/project/resource/room"
CHECKOUT = "/home/agent/.cheese/home/project/resource/.cheese/tasks/0a1b2c"
TASK = "0a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"
ROOM_TOPIC = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def pinned(tmp_path):
    """A build that answers the one question `prepare` asks it — its version."""
    binary = tmp_path / "claude"
    binary.write_text(f'#!/bin/sh\necho "{client.PINNED_VERSION}"\n')
    binary.chmod(0o755)
    return str(binary)


@pytest.fixture
def machine(monkeypatch):
    """The machine's answers: where it holds the project, its context tree,
    and what `cheese worktree` says for a task and for a room."""
    asked = []

    def call(self, method, params=None, **_):
        asked.append((method, params))
        if method == "ping":
            return {"workspace": ROOM}
        if method == "context_fs":
            return {"generation": "g1", "entries": {}, "instructions": ""}
        if method == "invoke":
            # Taking the hands does this when there is no token file yet
            # (`RemoteClient.acquire`); `prepare` must not keep it.
            self.config["execution_token"] = "lease-token"
            command = params["args"]["command"]
            if TASK in command:
                # A first open installs before it prints the path, which is
                # therefore the command's last line.
                return {"value": {"stdout": "installed\n" + CHECKOUT + "\n"}}
            return {
                "value": {
                    "stdout": "Exit code 1\n[cheese] POST /projects/p/git/tasks/"
                    + ROOM_TOPIC
                    + " 失败 HTTP 404: workTaskNotInProject\n"
                }
            }
        raise AssertionError(f"unexpected call {method}")

    monkeypatch.setattr(client.RemoteClient, "call", call)
    # The forwarded view is a FUSE mount, which a test machine need not have.
    monkeypatch.setattr(client, "take_view", lambda *args, **kwargs: None)
    return asked


def _prepare(tmp_path, pinned, monkeypatch, topic, **target):
    monkeypatch.setenv("CHEESE_TOPIC", topic)
    directory = tmp_path / "seat" / "remote-session"
    directory.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return client.prepare(
        directory,
        {
            "kind": "device",
            "local": True,
            "workspace": ROOM,
            "execution_token": "token",
            **target,
        },
        claude=pinned,
        home_override=home,
        config_override=home / ".claude",
    )


def test_a_task_session_starts_in_its_checkout(tmp_path, pinned, machine, monkeypatch):
    launch = _prepare(tmp_path, pinned, monkeypatch, TASK)
    assert launch["workspace"] == CHECKOUT
    assert launch["cwd"] == CHECKOUT
    assert launch["env"][client.WORKING_DIRECTORY] == CHECKOUT
    assert launch["workspace"] != ROOM
    # The checkout is the machine's to name: `cheese worktree` opened it.
    assert any(
        method == "invoke" and TASK in params["args"]["command"]
        for method, params in machine
    )


def test_the_leases_credential_stays_out_of_the_target(
    tmp_path, pinned, machine, monkeypatch
):
    """Taking the hands keeps the credential beside the target, never in it —
    a target that carries it is written to disk and embedded in the plugin."""
    _prepare(tmp_path, pinned, monkeypatch, TASK)
    written = (tmp_path / "seat" / "remote-session" / "execution.json").read_text()
    assert "lease-token" not in written
    for path in (tmp_path / "seat" / "remote-session").rglob("*"):
        if path.is_file() and path.suffix in {".json", ".js"}:
            assert "lease-token" not in path.read_text(), path


def test_a_relative_file_path_is_anchored_at_the_checkout(monkeypatch):
    """`SendUserFile` reads a relative path from where the session is, not from
    the project view, which for a task is its checkout."""
    from app.domain.agent.harness.claude_code.remote_execution import send_user_file

    monkeypatch.setenv("CHEESE_TOPIC", TASK)

    class Client:
        def working_directory(self):
            return CHECKOUT

        def platform_request(self, args):
            raise AssertionError("nothing to publish")

        def publish_message(self, payload, args):
            raise AssertionError("nothing to caption")

    def invoke(call, args):
        raise RuntimeError("no machine in this test")

    result = send_user_file.deliver_send_user_file(
        Client(),
        {"workspace": ROOM},
        {"id": "relative"},
        {"files": [{"path": "docs/report.pdf"}]},
        invoke,
    )
    entry = result["value"]["attachments"][0]
    assert entry["path"] == CHECKOUT + "/docs/report.pdf"


def test_bytes_already_in_hand_do_not_ask_the_machine(monkeypatch):
    """An entry the plugin already read, or already refused, is anchored at the
    launch's recorded start; asking the machine could wait on a lease."""
    from app.domain.agent.harness.claude_code.remote_execution import send_user_file

    monkeypatch.setenv("CHEESE_TOPIC", TASK)

    class Client:
        def working_directory(self):
            raise AssertionError("the machine must not be asked")

        def platform_request(self, args):
            raise AssertionError("nothing to publish")

        def publish_message(self, payload, args):
            raise AssertionError("nothing to caption")

    result = send_user_file.deliver_send_user_file(
        Client(),
        {"workspace": ROOM, "start_directory": CHECKOUT},
        {"id": "pre-read"},
        {"files": [{"path": "docs/report.pdf", "data_b64": "aGk="}]},
        lambda call, args: None,
    )
    entry = result["value"]["attachments"][0]
    assert entry["path"] == CHECKOUT + "/docs/report.pdf"


def test_the_checkout_is_opened_on_the_machine(
    tmp_path, pinned, machine, monkeypatch
):
    _prepare(tmp_path, pinned, monkeypatch, TASK)
    request = next(params for method, params in machine if method == "invoke")
    # The machine's own Bash runs it, not this host's; the project's hooks never
    # see a bootstrap command.
    assert request["tool"] == "Bash"
    assert request["platform"] is True
    # The path is the command's last line: the environment's own preparation
    # prints first, and the executor hands back only the front of its output.
    assert f"cheese worktree {TASK}" in request["args"]["command"]
    assert "tail -n 1" in request["args"]["command"]
    assert request["args"]["timeout"] == 600_000


def test_a_forwarded_session_sees_the_project_where_it_is_and_starts_in_its_checkout(
    tmp_path, pinned, machine, monkeypatch
):
    """The session that runs on another machine mounts the project view where
    the project is (`session_workspace`) and starts elsewhere (`start_directory`):
    `enter` makes that directory and chdirs into it."""
    import json

    _prepare(tmp_path, pinned, monkeypatch, TASK, local=False)
    target = json.loads(
        (tmp_path / "seat" / "remote-session" / "execution.json").read_text()
    )
    assert target["session_workspace"] == ROOM
    assert target["start_directory"] == CHECKOUT


def test_a_room_session_starts_where_it_did(tmp_path, pinned, machine, monkeypatch):
    launch = _prepare(tmp_path, pinned, monkeypatch, ROOM_TOPIC)
    assert launch["workspace"] == ROOM
    assert launch["cwd"] == ROOM
    assert launch["env"][client.WORKING_DIRECTORY] == ROOM


def test_a_room_is_not_a_checkout_that_failed(
    tmp_path, pinned, machine, monkeypatch, capsys
):
    """`workTaskNotInProject` is the platform's answer that this conversation
    is a room, not a failure to report to whoever reads the launch's stderr."""
    _prepare(tmp_path, pinned, monkeypatch, ROOM_TOPIC)
    assert capsys.readouterr().err == ""


def test_a_machine_that_could_not_be_asked_is_asked_again(monkeypatch):
    """A failed lookup is not the answer "this is a room": the session keeps
    the project view this time, and the next call asks again instead of being
    fixed to the project for the rest of its life."""
    from app.domain.agent.executor_transport import RemoteClient

    monkeypatch.setenv("CHEESE_TOPIC", TASK)
    client = RemoteClient({"kind": "device", "workspace": ROOM})
    asked = []

    def call(method, params=None, **_):
        asked.append(method)
        raise RuntimeError("the machine is out of reach")

    client.call = call
    assert client.working_directory() == ROOM
    assert client.working_directory() == ROOM
    assert "working_directory" not in client.config
    # Two attempts per lookup (`task_checkout` asks again once), and the second
    # lookup asked again rather than remembering the failure.
    assert len(asked) == 4


def test_a_session_without_its_machine_keeps_the_placeholder(
    tmp_path, pinned, machine, monkeypatch
):
    launch = _prepare(
        tmp_path,
        pinned,
        monkeypatch,
        TASK,
        kind="deferred",
        local=False,
        workspace=DEFERRED_WORKSPACE,
    )
    assert launch["workspace"] == DEFERRED_WORKSPACE
    assert launch["env"][client.WORKING_DIRECTORY] == DEFERRED_WORKSPACE
    # Nothing was opened: there is no machine to open it on.
    assert not [method for method, _ in machine if method == "invoke"]
