"""Recover only facts present in a complete retained execution interval.

The addressed work on a receipt is not execution ownership. The runner's
per-record work stamp is: a late steer's echo may belong to another work.
Missing pages, starts, session identity or receipts remain unresolved.
"""

import json
import uuid

from app.domain.agent.harness.claude_code.journal import Journal


class LegacyEvidenceIncomplete(RuntimeError):
    pass


def completion_inputs(
    path, *, work_id: str, session_id: str, recipient_handle: str, result: dict
) -> tuple[dict, ...]:
    journal = Journal(path)
    try:
        rows = journal.connection.execute(
            "SELECT sequence, record FROM records WHERE "
            "json_extract(record, '$.cheese.work_id') = ? ORDER BY sequence",
            (work_id,),
        ).fetchall()
    finally:
        journal.close()
    interval: list[tuple[int, dict]] = []
    for seq, raw in rows:
        record = json.loads(raw)
        if (record.get("cheese") or {}).get("turn_start"):
            interval = []
        interval.append((seq, record))
        if record == result:
            break
    if (
        not interval
        or interval[-1][1] != result
        or not (interval[0][1].get("cheese") or {}).get("turn_start")
        or result.get("type") != "result"
        or result.get("is_error")
        or (result.get("cheese") or {}).get("interrupted")
        or result.get("session_id") != session_id
        or (result.get("cheese") or {}).get("completion_session_id")
        not in (None, session_id)
        or any(b[0] != a[0] + 1 for a, b in zip(interval, interval[1:], strict=False))
    ):
        raise LegacyEvidenceIncomplete(
            "Retained native execution interval is incomplete"
        )
    inputs: list[uuid.UUID] = []
    echoes: list[dict] = []
    for _, record in interval:
        stamp = record.get("cheese") or {}
        if stamp.get("agent_handle") != recipient_handle:
            raise LegacyEvidenceIncomplete(
                "Retained execution has a different recipient"
            )
        if record.get("parent_tool_use_id") is not None:
            continue
        if record.get("type") == "user" and record.get("isReplay"):
            if (
                not stamp.get("receipt")
                or stamp.get("receipt_session_id") != session_id
                or record.get("session_id") != session_id
                or not stamp.get("receipt_work_id")
            ):
                raise LegacyEvidenceIncomplete(
                    "Retained input has no exact receipt identity"
                )
            uuid.UUID(stamp["receipt_work_id"])
            inputs.append(uuid.UUID(record["uuid"]))
            echoes.append(
                {**record, "cheese": {**stamp, "receipt_execution_work_id": work_id}}
            )
        elif record.get("type") == "result" and record != result:
            raise LegacyEvidenceIncomplete("Retained execution has another result")
    if len(set(inputs)) != len(inputs):
        raise LegacyEvidenceIncomplete("Retained execution repeats an input")
    if not inputs and not (result.get("cheese") or {}).get("unsolicited"):
        raise LegacyEvidenceIncomplete("Retained execution has no trusted inputs")
    return tuple(echoes)
