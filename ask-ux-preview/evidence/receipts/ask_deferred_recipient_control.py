"""Restore the deferred-scan-only recipient check without editing product files."""

import pytest


@pytest.fixture(autouse=True)
def unpin_deferred_recipient(monkeypatch):
    from app.api.deps import get_work_runner

    runner = get_work_runner()
    receive = runner._receive_message

    def unpinned(chat, topic, turn, **message):
        # Ordinary receive still carries its stored conversation handle. Remove
        # only the new instance pin on recovery's final scheduling boundary.
        message.pop("recipient_instance_id", None)
        receive(chat, topic, turn, **message)

    monkeypatch.setattr(runner, "_receive_message", unpinned)
