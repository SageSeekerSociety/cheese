"""WS-stripping-edge override for the connector install flow.

Ported from design/cheese-agent-layer (ce30e62 + 6327c7e): when the friendly
public origin sits behind an edge that strips the WebSocket Upgrade, the
installer bakes a WS-capable control-channel URL into the cli config's "ws"
key. Default (no override) must stay byte-identical in behaviour.
"""

import pytest

from app.core.config import settings


def test_install_script_default_has_no_ws_override(client):
    body = client.get("/connector/install.sh").text
    # Inert without configuration: the env fallback is empty, nothing wss://.
    assert 'WS_URL="${CHEESE_WS_URL:-}"' in body
    assert "wss://" not in body
    assert "cheesehost link connect" in body


def test_install_script_bakes_ws_url_for_matching_origin(
    client, monkeypatch: pytest.MonkeyPatch
):
    origin = client.get("/connector/install.sh").text.split('ORIGIN="')[1].split('"')[0]
    monkeypatch.setattr(
        settings, "connector_ws_overrides", {origin: "https://ws-capable.example"}
    )
    body = client.get("/connector/install.sh").text
    # Plain https origin → derived wss control URL at the cli's /connector/agent.
    assert 'WS_URL="${CHEESE_WS_URL:-wss://ws-capable.example/connector/agent}"' in body
    # The friendly origin still owns install + login (only the WS is rerouted).
    assert f'ORIGIN="{origin}"' in body
    assert "cheesehost link connect $ORIGIN/connector" in body


def test_install_script_override_ignores_other_origins(
    client, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        settings,
        "connector_ws_overrides",
        {"https://somewhere-else.example": "https://ws-capable.example"},
    )
    body = client.get("/connector/install.sh").text
    assert "ws-capable.example" not in body
    assert 'WS_URL="${CHEESE_WS_URL:-}"' in body


@pytest.mark.parametrize(
    ("uname_s", "config_dir"),
    [("Darwin", "Library/Application Support/cheese"), ("Linux", ".config/cheese")],
)
def test_install_script_writes_ws_url_where_cheesehost_reads_its_config(
    client, monkeypatch: pytest.MonkeyPatch, tmp_path, uname_s: str, config_dir: str
):
    """Run the served script with a stand-in uname and curl: the pinned control
    URL has to land in the file cheesehost opens (Go's os.UserConfigDir), or the
    override silently does nothing on that platform."""
    import os
    import stat
    import subprocess

    origin = client.get("/connector/install.sh").text.split('ORIGIN="')[1].split('"')[0]
    monkeypatch.setattr(
        settings, "connector_ws_overrides", {origin: "https://ws-capable.example"}
    )
    script = tmp_path / "install.sh"
    script.write_text(client.get("/connector/install.sh").text)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fakes = {
        "uname": f'[ "$1" = -m ] && echo arm64 || echo {uname_s}\n',
        # Every download lands as an empty file wherever -o points.
        "curl": 'while [ $# -gt 0 ]; do [ "$1" = -o ] && : > "$2"; shift; done\n',
    }
    for name, body in fakes.items():
        fake = bin_dir / name
        fake.write_text("#!/bin/sh\n" + body)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(home)}

    subprocess.run(["sh", str(script)], env=env, check=True, capture_output=True)

    config = home / config_dir / "config.json"
    assert "wss://ws-capable.example/connector/agent" in config.read_text()
