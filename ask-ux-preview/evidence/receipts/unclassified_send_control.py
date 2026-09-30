"""Negative control: reproduce the old generic post-send error classification."""

import pytest

from app.domain.agent.harness.driven.runtime import DrivenRuntime
from app.domain.delivery.input_identity import InputReceipt


async def generic_post_send_error(self, handle, identity, method, params):
    await self.channel.call(handle, method, params)
    await self.receipts(InputReceipt(identity, "accepted"))


DrivenRuntime._submit_registered = generic_post_send_error
raise SystemExit(
    pytest.main(
        ["-q", "tests/integration/test_input_send_faults.py", "-k", "accepted"]
    )
)
