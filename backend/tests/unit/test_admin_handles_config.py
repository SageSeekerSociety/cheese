"""``settings.admin_handles`` is the platform's admin roster — the value an
operator types into the environment must survive the trip.

A bare ``list[str]`` field is JSON-decoded by pydantic-settings, so the obvious
``ADMIN_HANDLES=alice,bob`` raises SettingsError and the backend refuses to boot.
This field carries a splitter for exactly that; these tests pin both accepted
spellings and the empty default that keeps admin routes fail-closed.
"""

import pytest

from app.core.config import Settings


def _handles(monkeypatch: pytest.MonkeyPatch, raw: str | None) -> list[str]:
    if raw is None:
        monkeypatch.delenv("ADMIN_HANDLES", raising=False)
    else:
        monkeypatch.setenv("ADMIN_HANDLES", raw)
    # _env_file=None: a developer's own backend/.env must not leak into the test.
    return Settings(_env_file=None).admin_handles  # type: ignore[call-arg]


def test_comma_separated_handles_are_split(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _handles(monkeypatch, "alice,bob") == ["alice", "bob"]


def test_surrounding_whitespace_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _handles(monkeypatch, " alice , bob ") == ["alice", "bob"]


def test_json_list_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _handles(monkeypatch, '["alice", "bob"]') == ["alice", "bob"]


def test_unset_and_empty_mean_no_admins(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _handles(monkeypatch, None) == []
    assert _handles(monkeypatch, "") == []
    assert _handles(monkeypatch, " , ") == []
