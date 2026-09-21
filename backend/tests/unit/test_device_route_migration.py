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


def test_legacy_route_requires_explicit_migration(migration):
    scope, path, original, calls = migration
    original.pop("ws")
    original["base"] = scope["LEGACY_BASE"]
    path.write_text(json.dumps(original))
    assert scope["remote"]("device-a")["status"] == "unmanaged_route"
    inspected = scope["remote"]("device-a", legacy=True, inspect_only=True)
    assert inspected["status"] == "eligible"
    assert json.loads(path.read_text()) == original
    assert not list(path.parent.glob("config.pre-direct-*"))
    assert not any("restart" in call for call in calls)
    result = scope["remote"]("device-a", legacy=True)
    assert result["status"] == "changed"
    assert json.loads(Path(result["backup"]).read_text()) == original
    assert json.loads(path.read_text()) == {**original, "ws": scope["NEW"]}


def test_legacy_mode_preserves_custom_public_routes(migration):
    scope, path, original, calls = migration
    original.pop("ws")
    path.write_text(json.dumps(original))
    assert scope["remote"]("device-a", legacy=True)["status"] == "unmanaged_route"
    assert json.loads(path.read_text()) == original
    assert not calls


def test_verified_host_key_does_not_replace_a_conflicting_pin(migration, monkeypatch):
    from types import SimpleNamespace

    scope, path, original, calls = migration
    pins = Path.home() / ".local/state/cheese-cloud-control/known_hosts"
    pins.parent.mkdir(parents=True)
    pins.write_text("original pins\n")
    monkeypatch.setattr(
        scope["subprocess"],
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout="cheese-cloud-1-device-a ssh-ed25519 ZGlmZmVyZW50\n"
        ),
    )
    with pytest.raises(RuntimeError, match="pin differs"):
        scope["pin_host"]((1, "device-a", "192.0.2.1", "cheese"), "ssh-ed25519 a2V5")
    assert pins.read_text() == "original pins\n"


def test_new_host_key_preserves_full_pin_backup(migration, monkeypatch):
    from types import SimpleNamespace

    scope, path, original, calls = migration
    pins = Path.home() / ".local/state/cheese-cloud-control/known_hosts"
    pins.parent.mkdir(parents=True)
    pins.write_text("original pins\n")
    monkeypatch.setattr(
        scope["subprocess"], "run", lambda *a, **kw: SimpleNamespace(returncode=1)
    )
    scope["pin_host"]((1, "device-a", "192.0.2.1", "cheese"), "ssh-ed25519 a2V5")
    backups = list(pins.parent.glob("known_hosts.pre-legacy-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "original pins\n"
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert pins.read_text().endswith("cheese-cloud-1-device-a ssh-ed25519 a2V5\n")


def test_unknown_legacy_service_is_never_stopped(migration):
    scope, path, original, calls = migration
    unit = Path.home() / ".config/systemd/user/cheese-control-1.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/usr/bin/another-service\n")
    with pytest.raises(RuntimeError, match="differs from the verified"):
        scope["retire_legacy_tunnel"]((1, "device-a", "192.0.2.1", "cheese"))
    assert not calls
    assert unit.exists()


def test_known_single_device_tunnel_is_backed_up_before_stop(migration, monkeypatch):
    scope, path, original, calls = migration
    unit = Path.home() / ".config/systemd/user/cheese-control-1.service"
    unit.parent.mkdir(parents=True)
    state = Path.home() / ".local/state/cheese-cloud-control"
    state.mkdir(parents=True)
    content = (
        "[Service]\nExecStart=/usr/bin/ssh -NT -o IdentityAgent=none -o BatchMode=yes "
        "-o ExitOnForwardFailure=yes -o ServerAliveInterval=5 -o ServerAliveCountMax=2 "
        "-o StrictHostKeyChecking=yes "
        f"-o UserKnownHostsFile={Path.home()}"
        "/ops/cloud-warm-20260908/known_hosts-pipeline "
        "-R 127.0.0.1:18080:127.0.0.1:8081 cheese@192.0.2.1\n"
    )
    unit.write_text(content)

    def command(argv, **kwargs):
        calls.append(argv)
        if "cat" in argv:
            return f"# {unit}\n{content}"
        backups = list(state.glob("*.pre-direct-*"))
        assert len(backups) == 1
        assert backups[0].read_text() == content
        assert backups[0].stat().st_mode & 0o777 == 0o600
        return ""

    monkeypatch.setitem(scope, "command", command)
    result = scope["retire_legacy_tunnel"]((1, "device-a", "192.0.2.1", "cheese"))
    assert Path(result["backup"]).read_text() == content
    assert calls[-1] == ["systemctl", "--user", "disable", "--now", unit.name]


@pytest.mark.parametrize("stale", [False, True])
def test_legacy_migration_validates_before_pinning_or_activating(
    migration, monkeypatch, tmp_path, stale
):
    scope, path, original, calls = migration
    row = {
        "machine_id": 1,
        "device_id": "device-a",
        "ip": "192.0.2.1",
        "login_user": "cheese",
        "host_key": "ssh-ed25519 a2V5",
    }
    monkeypatch.setenv("LEGACY_DEVICE_MANIFEST", json.dumps([row]))
    monkeypatch.setattr(
        scope["runpy"],
        "run_path",
        lambda path: {"identity": lambda row: (1, "device-a", "192.0.2.1", "cheese")},
    )
    generation = iter([5, 6])
    monkeypatch.setitem(
        scope,
        "snapshot",
        lambda: {"device-a": {"online": True, "generation": next(generation)}},
    )
    events = []

    def register(key, operation):
        events.append(operation)
        if stale:
            raise RuntimeError("stale manifest")
        return {"private_before": False, "operation": operation}

    def command(argv, **kwargs):
        inspecting = "--inspect-legacy" in argv
        events.append("inspect" if inspecting else "migrate")
        return json.dumps({"status": "eligible" if inspecting else "changed"})

    monkeypatch.setitem(scope, "register_legacy", register)
    monkeypatch.setitem(scope, "pin_host", lambda *args: events.append("pin"))
    monkeypatch.setitem(scope, "command", command)
    if stale:
        with pytest.raises(RuntimeError, match="stale manifest"):
            scope["migrate"](tmp_path / "logs", 1, legacy=True)
        assert events == ["validate"]
    else:
        scope["migrate"](tmp_path / "logs", 1, legacy=True)
        assert events == ["validate", "pin", "inspect", "activate", "migrate"]
