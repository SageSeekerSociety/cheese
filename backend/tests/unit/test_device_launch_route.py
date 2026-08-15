"""The route a machine's Claude Code actually takes — against a REAL machine's
settings shape.

History, so nobody resurrects the write: Claude Code used to apply the `env`
block of the login user's own ~/.claude/settings.json over the process
environment, so the launcher had to REWRITE that file for an injected route to
take effect at all — hijacking every claude the machine's owner started by
hand. CLAUDE_CONFIG_DIR removed the premise (claude no longer reads the
owner's file, verified 2026-08-15), and the reconcile became a read-only
ticket extractor. What is left to pin here is exactly that: run the shipped
program against a settings file shaped like a real provisioned machine's and
prove it (a) extracts the right ticket and (b) leaves the owner's file
byte-identical, whatever the supply shape.

test_device_launch.py covers the extractor's precedence matrix on synthetic
shapes; this file keeps the real-machine shape as the fixture.
"""

import json
import subprocess
import sys
from pathlib import Path

from app.domain.agent import device_launch

# What a provisioned MicroCloud machine actually has on disk (values redacted).
# The env block carries the image's own supply route AND the machine's ccproxy
# ticket — a dot-less sk-ant value, seeded by MicroCloud.
IMAGE_SETTINGS = {
    "env": {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-machineticket",
        "HTTPS_PROXY": "http://user:pw@ccproxy.example:3128",
        "HTTP_PROXY": "http://user:pw@ccproxy.example:3128",
        "NODE_EXTRA_CA_CERTS": "/home/cheese/.claude/ccproxy-ca.crt",
    }
}


def _reconcile(
    tmp_path: Path, settings: dict | None, env: dict
) -> tuple[str, str | None, str | None]:
    """Run the shipped program; return (stdout, file_bytes_after, handoff)."""
    program = tmp_path / "reconcile.py"
    program.write_text(device_launch.CHEESE_SETTINGS_RECONCILE)
    target = tmp_path / "settings.json"
    if settings is not None:
        target.write_text(json.dumps(settings))
    handoff = tmp_path / "handoff.token"
    result = subprocess.run(
        [sys.executable, str(program), str(target), str(handoff)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", **env},
    )
    after = target.read_text() if target.exists() else None
    ticket = handoff.read_text() if handoff.exists() else None
    return result.stdout.strip(), after, ticket


def test_a_real_machines_ticket_is_extracted_and_its_file_left_alone(
    tmp_path: Path,
):
    """The machine-ticket path against the real shape: the seeded ticket comes
    out through the handoff, and the owner's file is byte-identical — the write
    this file used to assert is now the regression it guards against."""
    before = json.dumps(IMAGE_SETTINGS)
    out, after, ticket = _reconcile(
        tmp_path,
        IMAGE_SETTINGS,
        {
            "CLAUDE_CODE_OAUTH_TOKEN": "our.scoped.token",
            "CHEESE_TUNNEL_URL": "wss://gw/api/llm/tunnel",
        },
    )
    assert out == "ok (ticket extracted)"
    assert ticket == "sk-ant-oat01-machineticket"
    assert after == before


def test_off_the_machine_ticket_path_the_real_file_is_not_even_opened_for_write(
    tmp_path: Path,
):
    """Gateway/swap shapes are fully described by the process environment now;
    the program is a declared no-op and the image's own supply route survives
    untouched for whatever the machine itself runs."""
    before = json.dumps(IMAGE_SETTINGS)
    out, after, ticket = _reconcile(
        tmp_path,
        IMAGE_SETTINGS,
        {"ANTHROPIC_BASE_URL": "http://cheese.test/llm"},
    )
    assert out == "ok (env only)"
    assert ticket is None
    assert after == before


def test_a_machine_without_image_settings_is_still_fine(tmp_path: Path):
    """No settings file at all (a bare box): nothing to extract from settings,
    nothing to crash on."""
    out, after, ticket = _reconcile(
        tmp_path,
        None,
        {
            "CLAUDE_CODE_OAUTH_TOKEN": "our.scoped.token",
            "CHEESE_TUNNEL_URL": "wss://gw/api/llm/tunnel",
        },
    )
    assert out == "no-machine-ticket"
    assert ticket is None
    assert after is None
