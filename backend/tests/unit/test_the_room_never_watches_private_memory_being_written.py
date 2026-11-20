"""The room watches what the agent is writing, a tool call's arguments included,
as they stream in: one frame, sent to everyone in the room (`live_frames`).
A call that writes a person's private memory shows its tool and a placeholder,
never what is being written, at any point of the stream and whatever order its
arguments come in; a call on the shared memory streams as it always did.
"""

import json

import pytest

from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.memory.files import prompt_path

pytestmark = pytest.mark.anyio

SECRET = "alice-private-body-live-4985"


def _streamed(event: dict) -> dict:
    return {"type": "stream_event", "parent_tool_use_id": None, "event": event}


async def _frames(tmp_path, name: str, pieces: list[str]) -> list[dict]:
    """Every live frame the room is sent while a ``name`` call streams in as
    ``pieces`` of its JSON arguments."""
    runner = Runner(tmp_path / "state")
    runner.outbox.register(1)
    seen = runner.live_mark()
    frames = []

    async def read() -> None:
        nonlocal seen
        answer = await runner.dispatch(
            "read", {"epoch": 1, "ack": 0, "wait": 0, "live": seen}
        )
        if "live" in answer:
            seen = answer["live"]["mark"]
            frames.append(answer["live"])

    runner.stream(
        _streamed(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": name,
                    "input": {},
                },
            }
        )
    )
    await read()
    for piece in pieces:
        runner.stream(
            _streamed(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": piece},
                }
            )
        )
        await read()
    return frames


def _pieces(arguments: dict) -> list[str]:
    whole = json.dumps(arguments, ensure_ascii=False)
    return [whole[at : at + 7] for at in range(0, len(whole), 7)]


@pytest.mark.parametrize("path_first", [True, False], ids=["path-first", "body-first"])
async def test_a_private_memory_being_written_shows_only_its_tool(tmp_path, path_first):
    path = prompt_path("private/alice/no-coffee.md")
    arguments = (
        {"file_path": path, "content": SECRET}
        if path_first
        else {"content": SECRET, "file_path": path}
    )

    frames = await _frames(tmp_path, "Write", _pieces(arguments))

    assert frames
    for frame in frames:
        assert SECRET not in json.dumps(frame, ensure_ascii=False)
    (block,) = frames[-1]["blocks"]
    assert block["name"] == "Write"
    assert "alice" in block["arguments"]
    assert "no-coffee" not in block["arguments"]


async def test_the_shared_memory_being_written_streams_as_it_is_written(tmp_path):
    arguments = {"file_path": prompt_path("team/answer-first.md"), "content": SECRET}

    frames = await _frames(tmp_path, "Write", _pieces(arguments))

    assert json.loads(frames[-1]["blocks"][0]["arguments"]) == arguments
