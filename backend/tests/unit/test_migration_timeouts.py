"""apply_migration_timeouts issues bounded lock/statement timeouts (#356).

A deploy's ``alembic upgrade head`` runs every migration on one connection. If a
migration's ``ALTER TABLE`` cannot grab its ACCESS EXCLUSIVE lock (a live backend
holds it in an open transaction), the ALTER waits — in #356 it waited out the
deploy's whole 30-minute budget and browned out the box. The fix sets a short
``lock_timeout`` on that connection so the migration fails fast and the deploy is
retryable. These tests pin the behaviour without a database.
"""

from unittest.mock import MagicMock

from app.core.config import settings
from app.core.db import apply_migration_timeouts


def _executed(connection: MagicMock) -> dict[str, str]:
    """Map each SET's target GUC → the value bound for it."""
    result: dict[str, str] = {}
    for call in connection.execute.call_args_list:
        clause = call.args[0]
        params = call.args[1] if len(call.args) > 1 else call.kwargs.get("parameters")
        sql = str(getattr(clause, "text", clause))
        # SELECT set_config('lock_timeout', :value, false) → key on the GUC name.
        for guc in ("lock_timeout", "statement_timeout"):
            if guc in sql:
                assert params is not None, f"{guc} SET carried no bind value"
                result[guc] = params["value"]
    return result


def test_applies_both_timeouts_from_settings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "migration_lock_timeout", "3s")
    monkeypatch.setattr(settings, "migration_statement_timeout", "90s")

    connection = MagicMock()
    apply_migration_timeouts(connection)

    executed = _executed(connection)
    assert executed == {"lock_timeout": "3s", "statement_timeout": "90s"}


def test_uses_set_config_not_string_interpolation(monkeypatch) -> None:
    """The value must travel as a bind param, never spliced into the SQL — a SET
    cannot be parameterized directly, so the code uses set_config(...) which can.
    A GUC value spliced into the statement text would be an injection surface."""
    # A value that would break out of a naive f-string SET if interpolated.
    monkeypatch.setattr(settings, "migration_lock_timeout", "10s'; DROP TABLE x; --")
    monkeypatch.setattr(settings, "migration_statement_timeout", "0")

    connection = MagicMock()
    apply_migration_timeouts(connection)

    for call in connection.execute.call_args_list:
        sql = str(getattr(call.args[0], "text", call.args[0]))
        assert "set_config" in sql
        assert "DROP TABLE" not in sql  # the value stayed in the bind, not the SQL


def test_defaults_are_bounded_lock_and_unlimited_statement() -> None:
    """The shipped defaults: a short lock wait (the actual fix) and an unlimited
    statement timeout (so a long, legitimate table rewrite is never mis-killed)."""
    assert settings.migration_lock_timeout == "10s"
    assert settings.migration_statement_timeout == "0"
