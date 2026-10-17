"""A project's local MCP server, started by the room's machine.

The rules, stated before they were written:

- on a member's own computer — the session hands the list of what its owner
  allowed — a command that is not on it does not start, and the refusal names
  the command and its fingerprint for the owner to be asked about;
- once its fingerprint is on the list, it starts;
- the fingerprint is over the definition as written: a secret's value is not
  part of it, so a secret changing does not ask again;
- `${VAR}` in a definition is filled from the values the session hands, with
  `${VAR:-default}` falling back to its default.
"""

import json
import sys

import pytest

from app.domain.agent.executor_transport import APPROVAL_REQUIRED, RemoteClient
from app.domain.agent.harness.claude_code.remote_execution.runtime_calls import (
    server_fingerprint,
)
from tests.support.room_machine import room_machine

SERVER = """import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request['method']
    if method == 'initialize':
        result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                  'serverInfo': {'name': 'notes', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': 'mark', 'inputSchema': {'type': 'object'}}]}
    elif method == 'tools/call':
        said = ' '.join(os.environ.get(k, '') for k in ('NOTES_TOKEN', 'NOTES_MODE'))
        result = {'content': [{'type': 'text', 'text': said + ' ' + sys.argv[1]}]}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}),
          flush=True)
"""


@pytest.fixture
def notes(tmp_path):
    script = tmp_path / "notes.py"
    script.write_text(SERVER)
    spec = {
        "command": sys.executable,
        "args": [str(script), "${FLAG}"],
        "env": {"NOTES_TOKEN": "${NOTES_TOKEN}", "NOTES_MODE": "${NOTES_MODE:-read}"},
    }
    with room_machine(tmp_path / "machine", mcp_servers={"notes": spec}) as target:
        yield RemoteClient(dict(target)), spec


def _list(client, **handed):
    return client.call(
        "mcp",
        {"server": "notes", "method": "tools/list", "params": {}, **handed},
    )


def test_a_command_its_owner_has_not_allowed_does_not_start(notes):
    client, spec = notes

    with pytest.raises(RuntimeError) as refused:
        _list(client, allowed=[])

    said = str(refused.value)
    assert APPROVAL_REQUIRED in said
    asked = json.loads(
        said[said.index(APPROVAL_REQUIRED) + len(APPROVAL_REQUIRED) :].splitlines()[0]
    )
    assert asked["fingerprint"] == server_fingerprint(spec)
    assert "${FLAG}" in asked["command"], "the owner reads the command as written"


def test_an_allowed_command_starts_with_the_values_it_was_handed(notes):
    client, spec = notes

    listed = _list(
        client,
        allowed=[server_fingerprint(spec)],
        values={"NOTES_TOKEN": "notes-secret", "FLAG": "--fast"},
    )
    called = client.call(
        "mcp",
        {
            "server": "notes",
            "method": "tools/call",
            "params": {"name": "mark", "arguments": {}},
        },
    )

    assert [tool["name"] for tool in listed["tools"]] == ["mark"]
    assert called["content"][0]["text"] == "notes-secret read --fast"


def test_a_changed_secret_is_not_a_changed_command():
    written = {"command": "uvx", "args": ["notes", "${TOKEN}"]}

    assert server_fingerprint(written) == server_fingerprint(dict(written))
    assert server_fingerprint(written) != server_fingerprint(
        {**written, "args": ["notes", "--delete-everything"]}
    )


def test_the_platforms_own_machines_start_a_command_unasked(notes):
    client, _ = notes

    assert _list(client)["tools"][0]["name"] == "mark"
