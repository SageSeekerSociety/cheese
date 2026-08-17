from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.invite.services import InviteCodeService


@pytest.mark.anyio
async def test_consume_code_uses_single_conditional_update() -> None:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = 42
    session.execute = AsyncMock(return_value=result)
    service = InviteCodeService(session)

    await service.consume_code("invite-once")

    assert session.execute.await_count == 1
    statement = str(session.execute.await_args.args[0])
    assert statement.startswith("UPDATE invite_code")
    assert "invite_code.use_count < invite_code.max_uses" in statement
    assert "RETURNING invite_code.id" in statement


@pytest.mark.anyio
async def test_consume_code_reports_exhaustion_after_losing_race() -> None:
    session = MagicMock()
    update_result = MagicMock()
    update_result.scalar_one_or_none.return_value = None
    exhausted_invite = MagicMock()
    exhausted_invite.is_active = True
    exhausted_invite.expires_at = None
    exhausted_invite.use_count = 1
    exhausted_invite.max_uses = 1
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = exhausted_invite
    session.execute = AsyncMock(side_effect=[update_result, select_result])
    service = InviteCodeService(session)

    with pytest.raises(ValueError, match="CODE_EXHAUSTED"):
        await service.consume_code("invite-once")

    assert session.execute.await_count == 2
