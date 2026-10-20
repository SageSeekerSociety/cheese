"""Self-describing, time-ordered sealed objects remain discoverable after row expiry."""

import base64
import json
import uuid
from datetime import datetime

from app.domain.session_record.models import SessionRecord

INDEX_PREFIX = "session-record-index"


def index_key(key: str) -> str:
    return key.replace("session-records/", f"{INDEX_PREFIX}/", 1)


def object_key(row: SessionRecord, last_at: datetime) -> str:
    identity = (
        base64.urlsafe_b64encode(
            json.dumps(
                [
                    str(row.turn_id) if row.turn_id else None,
                    row.agent_handle,
                    row.harness,
                    row.first_sequence,
                ]
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return (
        f"session-records/{row.conversation_id}/{row.first_at.isoformat()}~"
        f"{last_at.isoformat()}~{identity}~{uuid.uuid4().hex}.jsonl.gz"
    )


def describe(key: str) -> dict:
    """Read a published run's index without downloading its body."""
    key = key.replace(f"{INDEX_PREFIX}/", "session-records/", 1)
    first, last, encoded, _ = key.rsplit("/", 1)[-1].split("~")
    turn, agent, harness, sequence = json.loads(
        base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    )
    return {
        "first_at": datetime.fromisoformat(first),
        "last_at": datetime.fromisoformat(last),
        "turn_id": turn,
        "agent_handle": agent,
        "harness": harness,
        "first_sequence": sequence,
        "object_key": key,
    }
