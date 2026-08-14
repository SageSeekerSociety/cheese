"""Behavioural tests for the metering proxy addon (deploy/metering-proxy/
billing_addon.py) and the credential-hardening it enforces.

The addon imports mitmproxy, which the backend venv does not carry, so these
load it by path behind a minimal mitmproxy stub and drive its `request` hook
directly — testing the actual fail-closed / inject behaviour, not the source.

Covers the metering-proxy hardening (CODEX findings / #388):
  - an absent/empty injector fails closed with a local 503 and forwards nothing;
  - a present injector is swapped in as the upstream Bearer;
  - the injector path is read from the (now directory-based) mount;
  - the compose mounts the secrets DIRECTORY, not a single file;
  - the CI guard's self-test passes and the tree is clean.
"""

import asyncio
import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]
ADDON = REPO_ROOT / "deploy" / "metering-proxy" / "billing_addon.py"
COMPOSE = REPO_ROOT / "deploy" / "metering-proxy" / "compose.yml"
GUARD = REPO_ROOT / ".claude" / "scripts" / "check-metering-proxy.sh"

_ADDON_LOADS = 0


def _install_mitmproxy_stub(monkeypatch) -> None:
    """A stub just rich enough for billing_addon to import and to build the
    error responses it returns from the `request` hook."""

    class Response:
        def __init__(self, status_code=200, content=b"", headers=None):
            self.status_code = status_code
            self.content = content
            self.headers = headers or {}

        @classmethod
        def make(cls, status_code=200, content=b"", headers=None):
            return cls(status_code, content, headers or {})

    http_mod = types.ModuleType("mitmproxy.http")
    http_mod.Response = Response
    http_mod.HTTPFlow = type("HTTPFlow", (), {})
    tls_mod = types.ModuleType("mitmproxy.tls")
    tls_mod.ClientHelloData = type("ClientHelloData", (), {})
    root = types.ModuleType("mitmproxy")
    root.http = http_mod
    root.tls = tls_mod
    monkeypatch.setitem(sys.modules, "mitmproxy", root)
    monkeypatch.setitem(sys.modules, "mitmproxy.http", http_mod)
    monkeypatch.setitem(sys.modules, "mitmproxy.tls", tls_mod)


def _load_addon(monkeypatch, tmp_path, *, inject: str | None, scoped_secret: str = ""):
    """Load a FRESH billing_addon module with env captured for this test.

    inject=None leaves the injector file absent (empty credential); a string
    writes it. The addon reads all of these at import time, so env must be set
    before the module is loaded — hence a fresh module per call.
    """
    global _ADDON_LOADS
    _install_mitmproxy_stub(monkeypatch)

    token_file = tmp_path / "secrets" / "inject.token"
    if inject is not None:
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(inject)

    monkeypatch.setenv("CHEESE_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("CHEESE_INJECT_TOKEN", str(token_file))
    monkeypatch.setenv("CHEESE_SCOPED_SECRET", scoped_secret)
    monkeypatch.setenv("CHEESE_ALLOW_HEADER_ATTR", "")
    monkeypatch.setenv("CHEESE_ADMISSION_URL", "")
    monkeypatch.setenv("CHEESE_UPSTREAM_VIA", "")
    monkeypatch.setenv("CHEESE_TOKEN_CAP", "0")

    _ADDON_LOADS += 1
    name = f"billing_addon_{_ADDON_LOADS}"
    spec = importlib.util.spec_from_file_location(name, ADDON)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_flow(*, path="/v1/messages", caller_bearer="scoped.caller.token"):
    """A flow that clears the addon's host/TLS allowlist (Anthropic name over
    proxy-terminated TLS), carrying the caller's own scoped Bearer."""
    request = SimpleNamespace(
        path=path,
        host="api.anthropic.com",
        headers={"authorization": f"Bearer {caller_bearer}"},
    )
    return SimpleNamespace(
        request=request,
        client_conn=SimpleNamespace(sni="api.anthropic.com", tls_established=True),
        server_conn=SimpleNamespace(via=None),
        metadata={},
        response=None,
    )


def test_missing_injector_fails_closed_with_503(monkeypatch, tmp_path):
    """No credential on the host → a local 503 BEFORE forwarding. The caller's
    scoped bearer must NOT be rewritten (nothing is sent upstream)."""
    mod = _load_addon(monkeypatch, tmp_path, inject=None)
    flow = _make_flow(caller_bearer="scoped.caller.token")

    asyncio.run(mod.request(flow))

    assert flow.response is not None, "must short-circuit, not forward"
    assert flow.response.status_code == 503
    # The scoped bearer was left untouched — it was never swapped for a real one.
    assert flow.request.headers["authorization"] == "Bearer scoped.caller.token"


def test_empty_injector_also_fails_closed(monkeypatch, tmp_path):
    """A present-but-empty token file is the same failure as an absent one."""
    mod = _load_addon(monkeypatch, tmp_path, inject="   \n")
    flow = _make_flow()

    asyncio.run(mod.request(flow))

    assert flow.response is not None and flow.response.status_code == 503


def test_present_injector_is_swapped_in(monkeypatch, tmp_path):
    """With a real credential the caller's bearer is replaced by it and no error
    response is set (the request is allowed to forward)."""
    mod = _load_addon(monkeypatch, tmp_path, inject="sk-ant-oat01-REALTOKEN\n")
    flow = _make_flow(caller_bearer="scoped.caller.token")
    flow.request.headers["x-api-key"] = "stale-key"

    asyncio.run(mod.request(flow))

    assert flow.response is None, "a served request must not be refused"
    assert flow.request.headers["authorization"] == "Bearer sk-ant-oat01-REALTOKEN"
    # A stale x-api-key would override the injected bearer upstream — dropped.
    assert "x-api-key" not in flow.request.headers


def test_injector_read_from_configured_directory_path(monkeypatch, tmp_path):
    """The token is read from CHEESE_INJECT_TOKEN, i.e. the file inside the
    mounted secrets DIRECTORY — proving the directory-based path resolves."""
    mod = _load_addon(monkeypatch, tmp_path, inject="sk-ant-oat01-FROMDIR\n")

    assert str(mod.INJECT_TOKEN_FILE) == str(tmp_path / "secrets" / "inject.token")
    assert mod._real_token() == "sk-ant-oat01-FROMDIR"


def test_compose_mounts_secrets_directory_not_single_file():
    """The compose must inject via a read-only DIRECTORY mount, so an atomic
    rename-in-place rotation is seen without a restart (no inode trap)."""
    text = COMPOSE.read_text()
    assert "CHEESE_INJECT_TOKEN: /etc/cheese/secrets/inject.token" in text
    assert ":/etc/cheese/secrets:ro" in text
    # The old single-file bind mount (the inode trap) must be gone.
    assert "/etc/cheese/inject.token:ro" not in text


def test_hardening_guard_selftest_and_clean_tree():
    """The CI guard proves itself, and the real deploy tree passes it."""
    st = subprocess.run(
        ["bash", str(GUARD), "--self-test"], capture_output=True, text=True
    )
    assert st.returncode == 0, st.stdout + st.stderr

    clean = subprocess.run(
        ["bash", str(GUARD), str(REPO_ROOT)], capture_output=True, text=True
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
