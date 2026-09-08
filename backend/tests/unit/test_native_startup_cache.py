import json

import pytest

from app.domain.agent.harness.claude_code import startup_cache


@pytest.fixture
def initialized(tmp_path, monkeypatch):
    owner = tmp_path / "machine"
    settings_dir = owner / ".claude"
    settings_dir.mkdir(parents=True)
    settings = {
        "env": {"CLAUDE_CODE_OAUTH_TOKEN": "machine-a", "HTTPS_PROXY": "http://proxy"}
    }
    (settings_dir / "settings.json").write_text(json.dumps(settings))
    binary = owner / ".local/share/claude/versions/test-version"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:] == ['--init-only']\n"
        "config = Path(os.environ['CLAUDE_CONFIG_DIR'])\n"
        "assert config.parent != Path(" + repr(str(owner)) + ")\n"
        "assert 'hooks' not in json.loads((config / 'settings.json').read_text())\n"
        "remote = {'forceRemoteSettingsRefresh': True}\n"
        "policy = {'restrictions': {'deny': ['example']}}\n"
        "(config / 'remote-settings.json').write_text(json.dumps(remote))\n"
        "(config / 'policy-limits.json').write_text(json.dumps(policy))\n"
    )
    binary.chmod(0o700)
    startup_cache.prepare(owner, "test-version")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "machine-a")
    destination = tmp_path / "room"
    destination.mkdir()
    return owner, destination


def test_fresh_room_inherits_policy_without_dropping_refresh_requirement(initialized):
    owner, destination = initialized
    assert startup_cache.restore(owner, destination, "test-version")
    assert json.loads((destination / "remote-settings.json").read_text()) == {
        "forceRemoteSettingsRefresh": True
    }
    assert json.loads((destination / "policy-limits.json").read_text()) == {
        "restrictions": {"deny": ["example"]}
    }


@pytest.mark.parametrize(
    "change", ["owner_credential", "runtime_credential", "version", "expired"]
)
def test_changed_identity_or_stale_cache_is_not_applied(
    initialized, monkeypatch, change
):
    owner, destination = initialized
    version = "test-version"
    if change == "owner_credential":
        settings_path = owner / ".claude/settings.json"
        settings = json.loads(settings_path.read_text())
        settings["env"]["CLAUDE_CODE_OAUTH_TOKEN"] = "machine-b"
        settings_path.write_text(json.dumps(settings))
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "machine-b")
    elif change == "runtime_credential":
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "different-route")
    elif change == "version":
        version = "new-version"
    else:
        now = startup_cache.time.time()
        monkeypatch.setattr(startup_cache.time, "time", lambda: now + 3601)
    assert not startup_cache.restore(owner, destination, version)
    assert list(destination.iterdir()) == []


def test_native_refreshed_policy_is_never_overwritten(initialized):
    owner, destination = initialized
    policy = destination / "remote-settings.json"
    policy.write_text('{"newPolicy":true}')
    assert startup_cache.restore(owner, destination, "test-version")
    assert policy.read_text() == '{"newPolicy":true}'


def test_machine_without_preinitialization_uses_native_fetch(tmp_path):
    assert not startup_cache.restore(tmp_path, tmp_path, "test-version")
