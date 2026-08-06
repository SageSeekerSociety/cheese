import pytest

from app.api.routes.users import _normalize_registration_invite_code
from app.core.errors import UnprocessableEntityError


def test_invite_only_registration_rejects_missing_code() -> None:
    with pytest.raises(UnprocessableEntityError, match="Invite code is required"):
        _normalize_registration_invite_code("  ", required=True)


def test_registration_normalizes_present_invite_code() -> None:
    assert (
        _normalize_registration_invite_code("  cheese-2026  ", required=True)
        == "cheese-2026"
    )


def test_open_registration_ignores_invite_code() -> None:
    assert _normalize_registration_invite_code("stale-code", required=False) is None
