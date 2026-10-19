"""Image pixels cross the journal mirror without inflating the raw record."""

import asyncio
import base64
import hashlib
import uuid

import pytest

from app.domain.agent.harness.claude_code.journal import Journal as ClaudeJournal
from app.domain.session_record import images


def test_images_round_trip_through_the_journal_sidecar(tmp_path):
    pixels = b"sample image bytes"
    digest = hashlib.sha256(pixels).hexdigest()
    journal = ClaudeJournal(tmp_path / "source.sqlite")
    journal.append(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(pixels).decode(),
                        },
                    }
                ]
            },
        }
    )
    rows = journal.read()
    source = rows[0]["record"]["message"]["content"][0]["source"]
    assert source["sha256"] == digest
    assert "data" not in source
    assert base64.b64decode(rows[0]["images"][digest]) == pixels
    journal.close()


@pytest.mark.parametrize("size", [10 * 1024 * 1024, 10 * 1024 * 1024 + 1])
def test_only_images_over_ten_megabytes_are_omitted(tmp_path, size):
    journal = ClaudeJournal(tmp_path / "source.sqlite")
    pixels = b"a" * size
    journal.append(
        {
            "message": {
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(pixels).decode(),
                        },
                    }
                ]
            }
        }
    )
    row = journal.read()[0]
    source = row["record"]["message"]["content"][0]["source"]
    assert source["bytes"] == size
    assert (source["type"] == "omitted") == (size > 10 * 1024 * 1024)
    journal.close()


class _CorruptingBucket:
    """A bucket that accepts an upload and hands back other bytes."""

    def __init__(self):
        self.kept: dict[str, bytes] = {}

    async def exists(self, key):
        return key in self.kept

    async def upload(self, body, key, content_type):
        self.kept[key] = body.read()[:-1] + b"?"

    async def download(self, key):
        return self.kept.get(key)


def test_an_image_the_bucket_does_not_give_back_is_not_kept():
    """Records that refer to an image land only once the image is in the
    bucket as sent: a bucket that returns different bytes stops the landing."""
    pixels = b"sample image bytes"
    digest = hashlib.sha256(pixels).hexdigest()
    rows = [{"images": {digest: base64.b64encode(pixels).decode()}}]

    with pytest.raises(RuntimeError):
        asyncio.run(images.keep(rows, uuid.uuid4(), _CorruptingBucket()))
