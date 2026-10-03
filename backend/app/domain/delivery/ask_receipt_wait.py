"""Wake an undispatched Ask correction when its exact native receipt lands."""

from app.core.errors import ValidationError

ASK_RECEIPT_WAIT = "awaiting_ask_native_receipt"


class AskReceiptPending(ValidationError):
    """Admission rolled back before I/O; the same group's echo can unblock it."""

    def __init__(self, identity, *, delivery_id, attempt_id, group_id):
        self.identity = identity
        self.delivery_id = delivery_id
        self.attempt_id = attempt_id
        self.group_id = group_id
        super().__init__(
            "Input batch is already held by another native input; "
            "waiting for this group's native receipt"
        )

    def marker(self):
        return {
            "project_id": str(self.identity.project_id),
            "harness": self.identity.harness,
            "native_session_id": self.identity.native_session_id,
            "work_id": str(self.identity.work_id),
            "attempt_id": str(self.attempt_id),
            "ask_group": self.group_id,
        }


def _same_group(previous, delivery):
    return (
        previous.topic_id == delivery.topic_id
        and previous.recipient_handle == delivery.recipient_handle
        and previous.payload.get("ask_origin") == delivery.payload.get("ask_origin")
        and previous.payload.get("ask_group") == delivery.payload.get("ask_group")
        and previous.payload.get("block_ids") == delivery.payload.get("block_ids")
        and previous.payload.get("answer_to") in delivery.payload.get("block_ids", [])
    )


def _has_group_receipt(identity, delivery, inputs, wakes, deliveries):
    members = set(delivery.payload.get("block_ids", []))
    if not members:
        return False
    for row in inputs:
        held = set(row.held_block_ids) - set(row.released_block_ids)
        if (
            row.harness != identity.harness
            or row.native_session_id != identity.native_session_id
            or row.work_id != identity.work_id
            or row.execution_work_id != identity.work_id
            or row.echoed_at is None
            or row.settled_at is None
            or row.completed_at is not None
            or not members <= held
        ):
            continue
        for wake in wakes:
            meta = wake.meta or {}
            previous = deliveries.get(meta.get("delivery_event_id"))
            if (
                str(wake.id) in held
                and meta.get("answer_group") == delivery.payload.get("ask_group")
                and meta.get("answer_to") in members
                and previous is not None
                and _same_group(previous, delivery)
                and (
                    row.delivery_id is None
                    or (
                        row.delivery_id == previous.id
                        and row.event_id == previous.event_id
                    )
                )
            ):
                return True
    return False
