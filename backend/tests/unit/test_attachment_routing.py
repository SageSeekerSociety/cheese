"""Native image input is distinct from a file available for tool-based reading."""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.harness.prompt import attachment_prompt_line
from app.domain.agent.prompt import offered_attachments
from app.domain.block.models import BlockKind


@pytest.mark.parametrize(
    "mime,inline",
    [
        ("image/png", True),
        ("image/jpeg", True),
        ("image/gif", True),
        ("image/webp", True),
        ("image/svg+xml", False),
        ("image/tiff", False),
        ("application/zip", False),
        ("application/pdf", False),
        ("", False),
        (None, False),
    ],
)
def test_payload_and_prompt_agree_on_attachment_type(monkeypatch, mime, inline):
    # The library check is substituted here; HTTP/WS integration covers real files.
    monkeypatch.setattr(
        "app.domain.agent.prompt.library.attachment_exists", lambda *args: True
    )
    block = SimpleNamespace(
        id=uuid.uuid4(),
        kind=BlockKind.attachment,
        content="library/input",
        mime_type=mime,
    )
    images, gone = offered_attachments([block], uuid.uuid4(), uuid.uuid4())
    assert gone == set()
    assert images == ([{"path": block.content, "media_type": mime}] if inline else [])
    line = attachment_prompt_line(
        "user", block.content, embeds_images=True, mime=mime or ""
    )
    assert ("图片内容已附" in line) is inline
    assert ("发来一个文件" in line) is not inline


def test_unknown_type_defaults_to_a_file():
    line = attachment_prompt_line("user", "library/input.png", embeds_images=True)
    assert "发来一个文件" in line
    assert "图片" not in line
