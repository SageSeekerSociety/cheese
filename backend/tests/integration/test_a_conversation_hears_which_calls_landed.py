"""A conversation hears which tool calls have come back, as their records land.

The rule, stated without the code: when the record of a call's result is kept,
everyone watching the conversation is told that call's id and whether it
failed — the moment an edit the teammate was writing is on disk, or did not
make it. A record that answers no call says nothing.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.api.deps import get_broker
from app.domain.agent import landed_frames
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


def _edit(call: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call,
                    "name": "Edit",
                    "input": {
                        "file_path": "/work/kernel/proc.c",
                        "old_string": "return -1;",
                        "new_string": "return pid;",
                    },
                }
            ],
        },
    }


def _result(call: str, text: str, *, failed: bool = False) -> dict:
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


def _heard(client, topic: uuid.UUID, records: list[dict]) -> list[dict]:
    """Keep `records` as one session's journal; the frames the conversation heard."""
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

    async def run():
        heard = []
        async with get_broker().subscribe(str(topic)) as queue:
            await landed_frames.keep_and_tell(client.test_request_factory, ref, rows)
            while True:
                try:
                    heard.append(await asyncio.wait_for(queue.get(), 0.2))
                except TimeoutError:
                    return heard

    return client.portal.call(run)


def test_a_kept_result_says_which_call_came_back_and_whether_it_failed(client):
    topic = uuid.UUID(_room(client, _project(client, "OS lab"), "fork"))

    heard = _heard(
        client,
        topic,
        [
            _edit("e1"),
            _result("e1", "The file has been updated."),
            _edit("e2"),
            _result("e2", "old_string not found", failed=True),
        ],
    )

    landed = [frame for frame in heard if frame.get("type") == "landed"]
    assert [call for frame in landed for call in frame["calls"]] == [
        {"call_id": "e1", "failed": False},
        {"call_id": "e2", "failed": True},
    ]


def test_records_that_answer_no_call_say_nothing(client):
    topic = uuid.UUID(_room(client, _project(client, "DB lab"), "join"))

    heard = _heard(client, topic, [_edit("e1")])

    assert [frame for frame in heard if frame.get("type") == "landed"] == []
