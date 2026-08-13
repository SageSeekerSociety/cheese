"""The route a machine's Claude Code actually takes.

Claude Code applies the `env` block of the login user's own
~/.claude/settings.json over the process environment, and reads that file from
the login home rather than the isolated one the launcher exports. A provisioned
machine ships such a file, so an injected gateway route is discarded and the
turn bills the image's endpoint instead — with our books showing nothing.

These run the reconcile program exactly as the launcher ships it, against a
settings file shaped like a real machine's.
"""

import json
import subprocess
import sys
from pathlib import Path

from app.domain.agent import device_launch

# What a provisioned MicroCloud machine actually has on disk (values redacted).
IMAGE_SETTINGS = {
    "env": {
        "ANTHROPIC_BASE_URL": "http://10.0.0.9:80/newapi",
        "ANTHROPIC_AUTH_TOKEN": "image-token",
        "HTTPS_PROXY": "http://user:pw@ccproxy.example:3128",
        "HTTP_PROXY": "http://user:pw@ccproxy.example:3128",
        "NODE_EXTRA_CA_CERTS": "/home/cheese/.claude/ccproxy-ca.crt",
    }
}


def _reconcile(tmp_path: Path, settings: dict | None, env: dict) -> tuple[str, dict]:
    """Run the shipped program over a settings file; return (stdout, file)."""
    program = tmp_path / "reconcile.py"
    program.write_text(device_launch.CHEESE_SETTINGS_RECONCILE)
    target = tmp_path / "settings.json"
    if settings is not None:
        target.write_text(json.dumps(settings))
    result = subprocess.run(
        [sys.executable, str(program), str(target)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **env},
    )
    on_disk = json.loads(target.read_text()) if target.exists() else {}
    return result.stdout.strip(), on_disk


def test_injected_gateway_route_reaches_the_settings_file(tmp_path: Path):
    """Without this the image's endpoint wins and the turn is billed elsewhere."""
    out, on_disk = _reconcile(
        tmp_path,
        IMAGE_SETTINGS,
        {
            "ANTHROPIC_BASE_URL": "https://cheese.example/api/llm",
            "ANTHROPIC_AUTH_TOKEN": "scoped-project-token",
        },
    )
    env = on_disk["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://cheese.example/api/llm"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "scoped-project-token"
    # Our own gateway is reached directly, not through the image's proxy.
    assert env["NO_PROXY"] == "cheese.example"
    assert out.startswith("ok ")


def test_the_image_supply_route_survives(tmp_path: Path):
    """Overwriting the file instead of merging would take ccproxy down with it,
    and with it the subscription path — one silent failure traded for another."""
    _, on_disk = _reconcile(
        tmp_path,
        IMAGE_SETTINGS,
        {"ANTHROPIC_BASE_URL": "https://cheese.example/api/llm"},
    )
    env = on_disk["env"]
    assert env["HTTPS_PROXY"] == IMAGE_SETTINGS["env"]["HTTPS_PROXY"]
    assert env["NODE_EXTRA_CA_CERTS"] == IMAGE_SETTINGS["env"]["NODE_EXTRA_CA_CERTS"]
    assert (tmp_path / "settings.json.cheese-orig").exists()


def test_subscription_mode_removes_the_image_base_url(tmp_path: Path):
    """No base URL of ours means the official endpoint through the proxy. Leaving
    the image's in place would quietly send the subscription to newapi."""
    out, on_disk = _reconcile(tmp_path, IMAGE_SETTINGS, {})
    env = on_disk["env"]
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["HTTPS_PROXY"] == IMAGE_SETTINGS["env"]["HTTPS_PROXY"]
    assert out.startswith("ok ")


def test_our_subscription_overrides_the_image_supply_route(tmp_path: Path):
    """The platform's OWN subscription (marked by the injected
    CLAUDE_CODE_OAUTH_TOKEN): the image's proxy/CA entries in the file would win
    over the process environment key by key and send the session through the
    image's channel instead of our meter — so ours are asserted INTO the file."""
    ours = {
        "CLAUDE_CODE_OAUTH_TOKEN": "scoped.token",
        "HTTPS_PROXY": "http://cheese:scoped.token@proxy.cheese.example:8444",
        "NO_PROXY": "cheese.example,localhost,127.0.0.1,::1",
        "no_proxy": "cheese.example,localhost,127.0.0.1,::1",
        "NODE_EXTRA_CA_CERTS": "/home/cheese/.cheese/home/p/.claude/proxy-ca.pem",
    }
    out, on_disk = _reconcile(tmp_path, IMAGE_SETTINGS, ours)
    env = on_disk["env"]
    for key, value in ours.items():
        assert env[key] == value, key
    # The image's gateway route and plain-http proxy are gone with it.
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "HTTP_PROXY" not in env
    assert out.startswith("ok ")
    # The original is still backed up beside the file.
    assert (tmp_path / "settings.json.cheese-orig").exists()


def test_a_machine_without_image_settings_is_left_alone(tmp_path: Path):
    """Nothing overrides us there, so inventing a file would only add a second
    place for the route to disagree with itself."""
    out, on_disk = _reconcile(
        tmp_path, None, {"ANTHROPIC_BASE_URL": "https://cheese.example/api/llm"}
    )
    assert out == "absent"
    assert on_disk == {}


def test_a_write_that_did_not_take_is_reported_not_assumed(tmp_path: Path):
    """The verification re-reads from disk. Reporting the intent instead would
    recreate exactly the failure this step exists to catch."""
    out, _ = _reconcile(
        tmp_path,
        {"env": {"ANTHROPIC_BASE_URL": "http://10.0.0.9:80/newapi"}},
        {"ANTHROPIC_BASE_URL": "https://cheese.example/api/llm"},
    )
    assert out.startswith("ok https://cheese.example/api/llm")
    assert "mismatch" in device_launch.CHEESE_SETTINGS_RECONCILE


def test_the_launcher_actually_runs_the_reconcile():
    """A mechanism nothing calls has shipped from this file before: the redaction
    filter was added to a module no caller imported, and every test passed
    because they exercised the class directly."""
    script = device_launch.build_launch_script()
    assert "cheese-settings-reconcile.py" in script
    assert '"$REAL_HOME/.claude/settings.json"' in script
    # It has to run BEFORE claude is started, or the process is already up with
    # the image's route.
    assert script.index("cheese-settings-reconcile.py") < script.index("exec $CLAUDE")
