"""Image pixels cross the journal mirror without inflating the raw record."""

import base64
import hashlib

import pytest

from app.domain.agent.harness.claude_code.journal import Journal as ClaudeJournal


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
