"""An agent reading the room is told what the people in it see: a message the
author's own 芝士 wrote for them reads as theirs, written by their 芝士
(docs/agent-principles.md 十九)."""

from types import SimpleNamespace

from app.domain.agent.harness.prompt import live_input_lines, prompt_line
from app.domain.block.models import BlockKind


def _block(kind=BlockKind.message, content="周五组会改到下午三点", **meta):
    return SimpleNamespace(
        kind=kind, author="alice", content=content, meta=meta or None, mime_type=None
    )


def test_a_message_her_cheese_wrote_reads_as_hers_written_by_it():
    line = prompt_line(_block(by_assistant=True), embeds_images=False)

    assert line == "[alice（由本人的个人芝士代发）]: 周五组会改到下午三点"


def test_a_file_her_cheese_sent_reads_the_same_way():
    line = prompt_line(
        _block(BlockKind.attachment, "notes.txt", by_assistant=True),
        embeds_images=False,
    )

    assert line.startswith("[alice（由本人的个人芝士代发）] 发来一个文件：notes.txt")


def test_a_message_said_to_a_running_session_carries_it_too():
    stored = _block(by_assistant=True)

    lines = live_input_lines(
        "alice",
        stored.content,
        [{"path": "notes.txt", "mime": "text/plain"}],
        stored=stored,
    )

    assert all(line.startswith("[alice（由本人的个人芝士代发）]") for line in lines)


def test_what_she_wrote_herself_reads_as_before():
    assert prompt_line(_block(), embeds_images=False) == "[alice]: 周五组会改到下午三点"
