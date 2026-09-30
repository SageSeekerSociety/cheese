"""Negative control: restore NativeInput-before-Delivery receipt locking.

Run from backend with PYTHONPATH=.; product source is never rewritten.
"""

import sys

import pytest
from sqlalchemy import select

from app.domain.delivery import receipts
from app.domain.delivery.models import NativeInput

original = receipts.record_receipt


async def input_before_delivery(session, receipt):
    identity = receipt.identity
    await session.scalar(
        select(NativeInput)
        .where(
            NativeInput.harness == identity.harness,
            NativeInput.native_session_id == identity.native_session_id,
            NativeInput.input_id == identity.input_id,
        )
        .with_for_update()
    )
    return await original(session, receipt)


receipts.record_receipt = input_before_delivery
sys.exit(
    pytest.main(
        [
            "tests/integration/test_native_receipt_identity.py",
            "-q",
            "-p",
            "no:randomly",
            "--tb=short",
            "-k",
            "registration_retry_and_echo_can_overlap",
        ]
    )
)
