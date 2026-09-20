"""The route migration preserves device identity and refuses unsafe restarts."""

import json
import runpy
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy/migrate-device-routes.py"


@pytest.fixture
def migration(tmp_path, monkeypatch):
    scope = runpy.run_path(str(SCRIPT))
    scope = scope["remote"].__globals__
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / ".config/cheese/config.json"
    path.parent.mkdir(parents=True)
    original = {
        "device_id": "device-a",
        "token": "secret",
        "base": "https://example/api",
        "ws": scope["OLD"],
        "extra": {"keep": True},
    }
    path.write_text(json.dumps(original))
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        return "process\n" if "--property=KillMode" in argv else ""

    monkeypatch.setitem(scope, "command", command)
    monkeypatch.setitem(scope, "verify_route", lambda: None)
    monkeypatch.setattr(
        scope["socket"], "create_connection", lambda *a, **kw: MagicMock()
    )
    return scope, path, original, calls


def test_route_changes_only_ws_and_keeps_full_backup(migration):
    scope, path, original, calls = migration
    result = scope["remote"]("device-a")
    assert json.loads(path.read_text()) == {**original, "ws": scope["NEW"]}
    assert json.loads(Path(result["backup"]).read_text()) == original
    assert Path(result["backup"]).stat().st_mode & 0o777 == 0o600
    assert ["systemctl", "--user", "restart", "cheese.service"] in calls
    calls.clear()
    assert scope["remote"]("device-a")["status"] == "already_direct"
    assert not calls


def test_wrong_device_is_not_modified(migration):
    scope, path, original, calls = migration
    with pytest.raises(RuntimeError, match="identity changed"):
        scope["remote"]("another-device")
    assert json.loads(path.read_text()) == original
    assert not calls


def test_cgroup_restart_is_refused_before_writing(migration, monkeypatch):
    scope, path, original, calls = migration
    monkeypatch.setitem(scope, "command", lambda *a, **kw: "control-group")
    with pytest.raises(RuntimeError, match="signal session"):
        scope["remote"]("device-a")
    assert json.loads(path.read_text()) == original


def test_custom_route_is_not_replaced(migration):
    scope, path, original, calls = migration
    original["ws"] = "wss://custom.example/connector/agent"
    path.write_text(json.dumps(original))
    assert scope["remote"]("device-a")["status"] == "unmanaged_route"
    assert json.loads(path.read_text()) == original
    assert not calls
