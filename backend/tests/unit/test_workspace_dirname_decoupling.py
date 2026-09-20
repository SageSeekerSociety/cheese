"""Session container paths retain the historical room directory names."""

import uuid

import pytest

from app.domain.workspace import service as ws

FROZEN = [
    ("733747a3-0000-4000-8000-000000000001", "topic_733747a3"),
    ("bdf6626e-d3be-400a-b352-ac598b91959b", "topic_bdf6626e"),
    ("00000000-0000-4000-8000-000000000000", "topic_00000000"),
    ("ffffffff-ffff-4fff-bfff-ffffffffffff", "topic_ffffffff"),
]


@pytest.mark.parametrize(("topic", "dirname"), FROZEN)
def test_the_container_workdir_is_byte_for_byte_what_it_always_was(
    topic: str, dirname: str
):
    assert ws.sandbox_topic_workdir(uuid.UUID(topic)) == (
        f"{ws.SANDBOX_TOPICS_ROOT}/{dirname}"
    )
