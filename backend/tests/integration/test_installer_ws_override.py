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
    assert "cheesehost auth login" in body


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
    assert "cheesehost auth login $ORIGIN/connector" in body


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
