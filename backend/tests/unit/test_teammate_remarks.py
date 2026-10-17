"""Unit tests for personal teammate remark validation and helpers."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.domain.user.services import (
    REMARK_MAX_LENGTH,
    remark_for_handle,
    remarks_by_handles,
    set_teammate_remark,
)


async def test_set_teammate_remark_rejects_self() -> None:
    with pytest.raises(ValidationError):
        await set_teammate_remark(
            None,  # type: ignore[arg-type]
            viewer_handle="alice",
            target_handle="alice",
            remark="我自己",
        )


async def test_set_teammate_remark_rejects_too_long() -> None:
    too_long = "备" * (REMARK_MAX_LENGTH + 1)
    with pytest.raises(ValidationError):
        await set_teammate_remark(
            None,  # type: ignore[arg-type]
            viewer_handle="alice",
            target_handle="bob",
            remark=too_long,
        )


async def test_set_teammate_remark_rejects_control_chars() -> None:
    with pytest.raises(ValidationError):
        await set_teammate_remark(
            None,  # type: ignore[arg-type]
            viewer_handle="alice",
            target_handle="bob",
            remark="第一行\n第二行",
        )


async def test_remark_helpers_short_circuit_without_db() -> None:
    assert await remarks_by_handles(None, None, ["bob"]) == {}  # type: ignore[arg-type]
    assert await remarks_by_handles(None, "alice", ["alice"]) == {}  # type: ignore[arg-type]
    assert await remark_for_handle(None, "alice", "alice") is None  # type: ignore[arg-type]
