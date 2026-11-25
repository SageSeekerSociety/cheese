"""A task lists the shell commands its teammates ran, each with what it printed.

The rule, stated without the code: whoever may read a task's record can ask
for the commands run in it, newest first, and each comes with the output it
printed and whether it failed — so a person can run the same command again in
their own terminal. A step that only read or changed a file is not a command,
and a command that drove the platform as the teammate is not offered either: a
person's shell cannot act as the teammate. A page boundary falling between a
command and its output does not make the command read as unanswered.
"""

import functools
import uuid
from datetime import UTC, datetime, timedelta

from app.api.routes import topics_session_records
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.session_record import service
from tests.integration.conftest import session_auth_headers
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


def _call(name: str, call: str, args: dict) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call, "name": name, "input": args}],
        },
    }


def _answer(call: str, text: str, *, failed: bool = False) -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call,
                    "content": text,
                    "is_error": failed,
                }
            ],
        },
    }


TURN = [
    _call("Bash", "c1", {"command": "make", "description": "Build the kernel"}),
    _answer("c1", "gcc -o kernel kernel.c\nok"),
    _call("Read", "c2", {"file_path": "/work/kernel.c"}),
    _answer("c2", "int main() {}"),
    _call("Bash", "c3", {"command": "cheese sync"}),
    _answer("c3", "synced"),
    _call("Bash", "c4", {"command": "make grade"}),
    _answer("c4", "test_fork: FAIL\nscore 40/100", failed=True),
]


def _keep(client, topic: uuid.UUID, records: list[dict]) -> None:
    ref = SessionRef(uuid.uuid4(), topic, "cheese", harness=CLAUDE_CODE)
    start = datetime.now(UTC)
    rows = [
        {
            "sequence": n + 1,
            "at": (start + timedelta(seconds=n)).isoformat(),
            "record": record,
        }
        for n, record in enumerate(records)
    ]
    keep = functools.partial(service.append, client.test_request_factory)

    async def run():
        return await keep(ref, rows)

    client.portal.call(run)


def _commands(client, topic, **params) -> dict:
    response = client.get(
        f"/topics/{topic}/commands",
        params=params,
        headers=session_auth_headers("user-1"),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_the_commands_come_newest_first_with_their_output(client):
    topic = uuid.UUID(_room(client, _project(client, "OS lab"), "fork"))
    _keep(client, topic, TURN)

    page = _commands(client, topic)

    assert [(c["command"], c["output"], c["failed"]) for c in page["items"]] == [
        ("make grade", "test_fork: FAIL\nscore 40/100", True),
        ("make", "gcc -o kernel kernel.c\nok", False),
    ]
    assert page["items"][1]["description"] == "Build the kernel"
    assert page["next"] is None


def test_a_page_boundary_between_command_and_output_keeps_the_output(
    client, monkeypatch
):
    topic = uuid.UUID(_room(client, _project(client, "DB lab"), "join"))
    _keep(client, topic, TURN)
    # The newest page ends right after `make`'s output; `make` itself is alone
    # on the page before it.
    monkeypatch.setattr(topics_session_records, "COMMAND_RECORDS_PER_PAGE", 7)

    seen: list[dict] = []
    params: dict = {}
    while True:
        page = _commands(client, topic, **params)
        seen += page["items"]
        if not page["next"]:
            break
        params = {"before": page["next"]}

    assert [(c["command"], c["output"]) for c in seen] == [
        ("make grade", "test_fork: FAIL\nscore 40/100"),
        ("make", "gcc -o kernel kernel.c\nok"),
    ]


def test_someone_outside_the_project_cannot_read_them(client):
    topic = uuid.UUID(_room(client, _project(client, "Private lab"), "fork"))
    _keep(client, topic, TURN)

    response = client.get(
        f"/topics/{topic}/commands", headers=session_auth_headers("stranger")
    )

    assert response.status_code in (403, 404), response.text
