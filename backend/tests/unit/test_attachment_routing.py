"""Native image input is distinct from a file available for tool-based reading."""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.harness.prompt import attachment_prompt_line
from app.domain.agent.prompt import offered_attachments
from app.domain.agent.session_host.contract import Image
from app.domain.block.models import BlockKind
from tests.support.pictures import BROKEN_PNG, picture


def _library(monkeypatch, data: bytes) -> None:
    # The library is substituted here; HTTP/WS integration covers real files.
    # The turn asks for a whole message at once, so the fakes answer per batch.
    async def present(_session, _project_id, _room_id, paths, **_):
        return set(paths)

    async def stored(_session, _project_id, _room_id, paths, *, max_bytes, **_):
        if len(data) > max_bytes:
            return {}, set(paths)
        return {path: data for path in paths}, set()

    monkeypatch.setattr(
        "app.domain.agent.prompt.library_records.attachments_present", present
    )
    monkeypatch.setattr(
        "app.domain.agent.prompt.library_records.read_attachments", stored
    )


@pytest.mark.parametrize(
    "mime,data,inline",
    [
        ("image/png", picture("PNG"), True),
        ("image/jpeg", picture("JPEG"), True),
        ("image/gif", picture("GIF"), True),
        ("image/webp", picture("WEBP"), True),
        ("image/svg+xml", b"<svg/>", False),
        ("image/tiff", picture("TIFF"), False),
        ("application/zip", b"PK\x03\x04", False),
        ("application/pdf", b"%PDF-1.7", False),
        ("", picture("PNG"), False),
        (None, picture("PNG"), False),
    ],
)
async def test_payload_and_prompt_agree_on_attachment_type(
    monkeypatch, mime, data, inline
):
    _library(monkeypatch, data)
    block = SimpleNamespace(
        id=uuid.uuid4(),
        kind=BlockKind.attachment,
        content="library/input",
        mime_type=mime,
    )
    images, gone, undecodable = await offered_attachments(
        None, [block], uuid.uuid4(), uuid.uuid4()
    )
    assert gone == set() and undecodable == set()
    assert images == ([Image(mime, data)] if inline else [])
    line = attachment_prompt_line(
        "user", block.content, embeds_images=True, mime=mime or ""
    )
    assert ("图片内容已附" in line) is inline
    assert ("发来一个文件" in line) is not inline


@pytest.mark.parametrize("mime", ["image/png", "application/octet-stream", ""])
async def test_a_picture_that_does_not_decode_never_reaches_the_model_as_one(
    monkeypatch, mime
):
    """Called a PNG by its message, or only by its name: either way its bytes do
    not decode, so it is not handed over as a picture, and the session is told
    so instead of being pointed at a file it would open as one."""
    _library(monkeypatch, BROKEN_PNG)
    block = SimpleNamespace(
        id=uuid.uuid4(),
        kind=BlockKind.attachment,
        content="uploads/abc/粘贴的图片.png",
        mime_type=mime,
    )
    images, _, undecodable = await offered_attachments(
        None, [block], uuid.uuid4(), uuid.uuid4()
    )
    assert images == []
    assert undecodable == {block.id}
    line = attachment_prompt_line(
        "user", block.content, embeds_images=True, mime=mime, undecodable=True
    )
    assert "图片内容已附" not in line
    assert "发来一个文件" in line and "Read" in line


async def test_a_picture_is_handed_over_as_what_its_bytes_are(monkeypatch):
    """A JPEG its message calls a PNG goes as a JPEG: a model's API refuses an
    image whose declared type is not the one its bytes have."""
    data = picture("JPEG")
    _library(monkeypatch, data)
    block = SimpleNamespace(
        id=uuid.uuid4(),
        kind=BlockKind.attachment,
        content="library/input.png",
        mime_type="image/png",
    )
    images, _, _ = await offered_attachments(None, [block], uuid.uuid4(), uuid.uuid4())
    assert images == [Image("image/jpeg", data)]


def test_unknown_type_defaults_to_a_file():
    line = attachment_prompt_line("user", "library/input.png", embeds_images=True)
    assert "发来一个文件" in line
    assert "图片" not in line
