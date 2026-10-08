"""What a teammate answering in a 支线 is waiting for reaches the channel's main
line: the line under the message says it is queued or retrying, not only that
it is answering. A record of the channel itself is not told twice."""

import uuid

from app.domain.agent.run_records import record_now
from tests.integration.conftest import in_thread, post_project, room_socket


def _until(ws, predicate) -> dict:
    while True:
        frame = ws.receive_json()
        if predicate(frame):
            return frame


def test_a_threads_queue_reaches_the_main_line(client):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    thread = in_thread(client, room, "alice")

    def keep(where: str, content: str):
        return client.portal.call(
            lambda: record_now(
                client.test_request_factory,
                conversation_id=uuid.UUID(where),
                content=content,
                meta={"event_type": "turn_queued", "seat": "cheese"},
                turn_id=uuid.uuid4(),
            )
        )

    with room_socket(client, room, "alice") as ws:
        keep(room, "主线自己的")
        keep(thread, "支线里的")
        said = _until(
            ws,
            lambda f: (
                f["type"] in ("thread_status", "run_record")
                and f["record"]["content"] == "支线里的"
            ),
        )
    assert said["type"] == "thread_status"
    assert said["thread_id"] == thread
