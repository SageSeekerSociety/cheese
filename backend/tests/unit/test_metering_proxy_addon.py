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
import base64
import hashlib
import hmac
import importlib.util
import json
import subprocess
import sys
import time
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


def _load_addon(
    monkeypatch,
    tmp_path,
    *,
    inject: str | None,
    scoped_secret: str = "",
    allow_header_attr: str = "",
):
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
    monkeypatch.setenv("CHEESE_ALLOW_HEADER_ATTR", allow_header_attr)
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
        client_conn=SimpleNamespace(
            sni="api.anthropic.com", tls_established=True, id="client-1"
        ),
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


# --- carrying a machine's own ticket ----------------------------------------
# ccproxy scopes its fake→real ticket swap to the identity the upstream
# connection authenticated as: measured 2026-08-14, a ticket issued to m516 over
# an m161 connection returns 401 with no request_id, while the same ticket over
# m516's own connection reaches Anthropic. So "forward the caller's ticket" and
# "authenticate as that caller's machine" are one decision, and these tests pin
# that the two halves cannot drift apart.


def _with_admission(mod, monkeypatch, upstream: str | None):
    """Point the addon at a control plane that returns `upstream` for everyone."""
    verdict = SimpleNamespace(
        allow=True, reason="", pool="subscription", key=None, upstream=upstream
    )
    monkeypatch.setattr(mod, "ADMISSION_URL", "http://control-plane.invalid/admission")
    monkeypatch.setattr(
        mod, "ADMISSION", SimpleNamespace(check=lambda project, bearer: verdict)
    )
    return verdict


def test_a_machine_with_its_own_identity_keeps_its_own_ticket(monkeypatch, tmp_path):
    """The whole point of the pass-through path: the platform must not swap in a
    credential it holds, because it is not the one ccproxy will accept."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    _with_admission(mod, monkeypatch, "m516:pw516")
    flow = _make_flow(caller_bearer=_scoped_token(secret))
    ticket = flow.request.headers["authorization"]

    asyncio.run(mod.request(flow))

    assert flow.response is None
    assert flow.request.headers["authorization"] == ticket, "ticket was rewritten"
    assert "PLATFORM" not in flow.request.headers["authorization"]


def test_the_upstream_hop_authenticates_as_that_same_machine(monkeypatch, tmp_path):
    """A forwarded ticket over the wrong identity is a 401 several hops away, so
    the identity the request hook chose must be the one the CONNECT presents."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    _with_admission(mod, monkeypatch, "m516:pw516")
    monkeypatch.setattr(mod, "UPSTREAM_AUTH", "m161:pw161")
    asyncio.run(mod.request(_make_flow(caller_bearer=_scoped_token(secret))))

    connect = SimpleNamespace(
        request=SimpleNamespace(headers={}),
        client_conn=SimpleNamespace(id="client-1"),
    )
    mod.http_connect_upstream(connect)

    expected = base64.b64encode(b"m516:pw516").decode()
    assert connect.request.headers["Proxy-Authorization"] == f"Basic {expected}"


def test_traffic_the_control_plane_cannot_place_keeps_todays_behaviour(
    monkeypatch, tmp_path
):
    """A machine enrolled before identities were recorded, and the local
    container path: the platform's own credential goes out over the
    deployment-wide identity — the two halves still agree."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    _with_admission(mod, monkeypatch, None)
    monkeypatch.setattr(mod, "UPSTREAM_AUTH", "m161:pw161")
    flow = _make_flow(caller_bearer=_scoped_token(secret))

    asyncio.run(mod.request(flow))

    assert flow.request.headers["authorization"] == "Bearer sk-ant-oat01-PLATFORM"

    connect = SimpleNamespace(
        request=SimpleNamespace(headers={}),
        client_conn=SimpleNamespace(id="client-1"),
    )
    mod.http_connect_upstream(connect)
    expected = base64.b64encode(b"m161:pw161").decode()
    assert connect.request.headers["Proxy-Authorization"] == f"Basic {expected}"


def test_a_closed_connection_stops_pinning_an_identity(monkeypatch, tmp_path):
    """The proxy is long-lived; one entry per connection ever made is a leak, and
    a reused connection id must not inherit a previous caller's identity."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    _with_admission(mod, monkeypatch, "m516:pw516")
    asyncio.run(mod.request(_make_flow(caller_bearer=_scoped_token(secret))))
    assert mod._UPSTREAM_BY_CLIENT

    mod.client_disconnected(SimpleNamespace(id="client-1"))

    assert not mod._UPSTREAM_BY_CLIENT


def test_the_compose_no_longer_stamps_one_identity_on_every_connection():
    """mitmdump's --upstream-auth applies a single value to every upstream
    connection, which is precisely what cannot be true any more; the addon must
    be the only writer of that header."""
    text = COMPOSE.read_text()
    # Comments are excluded deliberately: the header explains at length why the
    # flag is gone, and a test that reads prose would fail on the explanation.
    directives = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--upstream-auth" not in directives
    assert "CHEESE_UPSTREAM_AUTH: ${CCPROXY_UPSTREAM_AUTH:-}" in directives


# --- the CONNECT listener's gate -------------------------------------------
# The listener's bind address became a per-box setting so a MicroCloud machine
# can reach it (it has no root and no /etc/hosts, so HTTPS_PROXY is its only
# route to the meter). That makes the gate load-bearing: these pin the rule that
# widening the bind cannot silently produce an open relay.


def _scoped_token(secret: str, *, project: str = "p1", ttl_s: float = 3600.0) -> str:
    """A token shaped exactly like the backend's mint_scoped_token. Signed for
    real: the addon verifies the HMAC, so a hand-written string would only ever
    exercise the reject path."""
    raw = json.dumps({"p": project, "t": "t1", "exp": time.time() + ttl_s})
    body = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(digest).decode().rstrip('=')}"


def _basic(password: str) -> str:
    return "Basic " + base64.b64encode(f"cheese:{password}".encode()).decode()


def _make_connect_flow(proxy_auth: str | None = None, *, conn: str = "client-1"):
    """A CONNECT as the regular-mode listener sees it: the caller's scoped token
    rides as the Basic password of its HTTPS_PROXY URL.

    It carries a client connection because a real one always does, and because
    the token proved here has to be findable later from the requests that arrive
    on this same connection — the connection is the only thing the CONNECT hook
    and the request hook share."""
    headers: dict[str, str] = {}
    if proxy_auth is not None:
        headers["proxy-authorization"] = proxy_auth
    return SimpleNamespace(
        request=SimpleNamespace(headers=headers),
        client_conn=SimpleNamespace(id=conn),
        response=None,
    )


def test_connect_without_a_configured_secret_refuses_rather_than_relaying(
    monkeypatch, tmp_path
):
    """The failure this guards is silent: a box that widens CONNECT_BIND_HOST but
    forgets CHEESE_SCOPED_SECRET has no error to notice, so an unset secret must
    refuse every tunnel instead of falling back to trusting the network."""
    mod = _load_addon(monkeypatch, tmp_path, inject="sk-ant-oat01-X", scoped_secret="")
    flow = _make_connect_flow(_basic("anything"))

    mod.http_connect(flow)

    assert flow.response is not None and flow.response.status_code == 407


def test_connect_with_a_valid_scoped_token_is_relayed(monkeypatch, tmp_path):
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-X", scoped_secret=secret
    )
    flow = _make_connect_flow(_basic(_scoped_token(secret)))

    mod.http_connect(flow)

    assert flow.response is None, "a caller that proved its project must get a tunnel"


def test_connect_with_a_foreign_or_missing_token_is_refused(monkeypatch, tmp_path):
    """Signed by someone else, expired, or absent entirely — all the same 407."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-X", scoped_secret=secret
    )
    for auth in (
        None,
        _basic("not-a-token"),
        _basic(_scoped_token("a-different-secret")),
        _basic(_scoped_token(secret, ttl_s=-1)),
    ):
        flow = _make_connect_flow(auth)
        mod.http_connect(flow)
        assert flow.response is not None and flow.response.status_code == 407


def test_connect_legacy_bridge_only_posture_stays_explicit(monkeypatch, tmp_path):
    """The one way to run an ungated listener is the documented opt-in, whose
    name already says the box trusts whoever can reach it."""
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-X", allow_header_attr="1"
    )
    flow = _make_connect_flow(None)

    mod.http_connect(flow)

    assert flow.response is None


def test_compose_lets_a_box_publish_connect_where_machines_can_reach_it():
    """A remote machine can route to the box but not to the docker bridge, so a
    hardcoded bridge bind is what kept remote subscription turns from working.
    The reverse listener stays bridge-only — only sandboxes on this box use it."""
    text = COMPOSE.read_text()
    assert '"${CONNECT_BIND_HOST:-172.17.0.1}:8444:8444"' in text
    assert '"172.17.0.1:443:8443"' in text


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


# --- attribution on the pass-through path ----------------------------------
# The two halves of the machine design contradicted each other in production:
# pass-through requires the caller's Bearer to be the MACHINE's ccproxy ticket,
# while attribution read the project out of that same Bearer expecting a scoped
# cheese token. So an enrolled machine could never be attributed, never got an
# admission verdict, never got its identity — and its turns were refused by the
# meter itself ("a valid scoped token is required", measured on machine 474,
# 2026-08-15). The token was never missing: it rode on the CONNECT, one layer
# down, where nothing looked for it.


def _machine_flow(*, ticket="sk-ant-oat01-machine-ticket", attr=None, conn="client-1"):
    """A request as an ENROLLED MACHINE makes it: the Bearer is its own ccproxy
    ticket (77 chars, no dot — not a scoped token and not verifiable here), on a
    connection that proved its project at CONNECT time."""
    headers = {"authorization": f"Bearer {ticket}"}
    if attr is not None:
        headers["x-cheese-attr"] = attr
    request = SimpleNamespace(
        path="/v1/messages", host="api.anthropic.com", headers=headers
    )
    return SimpleNamespace(
        request=request,
        client_conn=SimpleNamespace(
            sni="api.anthropic.com", tls_established=True, id=conn
        ),
        server_conn=SimpleNamespace(via=None),
        metadata={},
        response=None,
    )


def _connect_flow_on(conn: str, proxy_auth: str | None):
    return _make_connect_flow(proxy_auth, conn=conn)


def test_a_machines_ticket_is_attributed_from_what_it_proved_at_connect(
    monkeypatch, tmp_path
):
    """The whole point of pass-through: the Bearer is NOT a scoped token, so the
    project has to come from the CONNECT that opened this connection."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-X", scoped_secret=secret
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )

    project, topic, bearer, _ = mod._attribution(_machine_flow(conn="c1"))

    assert project == "p9", "an enrolled machine must still be attributable"
    assert topic == "t1"


def test_the_admission_call_uses_the_token_the_caller_actually_proved(
    monkeypatch, tmp_path
):
    """Admission authenticates with a scoped cheese token. Handing it the
    machine's ccproxy ticket instead would 401 — and the proxy fails OPEN on
    admission errors, so the budget brake would quietly stop braking."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-X", scoped_secret=secret
    )
    token = _scoped_token(secret, project="p9")
    mod.http_connect(_connect_flow_on("c1", _basic(token)))

    _, _, bearer, _ = mod._attribution(_machine_flow(conn="c1"))

    assert bearer == token


def test_a_connection_that_proved_nothing_is_still_unattributable(
    monkeypatch, tmp_path
):
    """The security property must not regress: reading the CONNECT is a new
    SOURCE of verified claims, not a new way to skip proving one."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-X", scoped_secret=secret
    )

    project, _, _, _ = mod._attribution(_machine_flow(conn="never-connected"))

    assert project == ""


def test_the_session_header_refines_the_topic_inside_the_proven_project(
    monkeypatch, tmp_path
):
    """One machine hosts several topics of a project but shares ONE tunnel
    helper, so the CONNECT token names whichever session launched last. The
    per-request header is the only per-topic signal, and it is safe to honour
    for the topic alone: the project it is checked against was proven."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-X", scoped_secret=secret
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )

    project, topic, _, _ = mod._attribution(
        _machine_flow(conn="c1", attr="p9/other-topic")
    )

    assert (project, topic) == ("p9", "other-topic")


def test_the_session_header_cannot_move_billing_to_another_project(
    monkeypatch, tmp_path
):
    """The header is unverified. It may pick a topic, never a payer."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-X", scoped_secret=secret
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )

    project, topic, _, _ = mod._attribution(
        _machine_flow(conn="c1", attr="someone-elses-project/their-topic")
    )

    assert (project, topic) == ("p9", "t1"), "a foreign project is ignored entirely"


def test_a_closed_connection_stops_pinning_a_proven_project(monkeypatch, tmp_path):
    """Same lifetime rule as the identity map: a long-lived proxy must not
    accumulate one entry per connection ever made, and a recycled connection id
    must not inherit the previous caller's project."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-X", scoped_secret=secret
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )

    mod.client_disconnected(SimpleNamespace(id="c1"))

    assert mod._attribution(_machine_flow(conn="c1"))[0] == ""


def test_an_unplaceable_machine_is_refused_rather_than_billed_to_the_platform(
    monkeypatch, tmp_path
):
    """The day-costing failure, pinned. A box with no CHEESE_ADMISSION_URL gives
    no verdict, so no machine is ever placed on its own identity — and the swap
    below would put the PLATFORM's credential on a caller that brought its own.
    Upstream then answers about a token the machine never held, which is how
    this read as "the platform's subscription is revoked" for a day.

    Refusing keeps two properties the swap would break: the wrong account is not
    spent, and the error names the missing piece instead of impersonating an
    auth failure."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _machine_flow(conn="c1")

    asyncio.run(mod.request(flow))

    assert flow.response is not None and flow.response.status_code == 503
    assert flow.request.headers["authorization"] == "Bearer sk-ant-oat01-machine-ticket"


def test_a_scoped_caller_with_no_admission_still_gets_the_platform_credential(
    monkeypatch, tmp_path
):
    """The refusal above must be narrow. A caller whose Bearer IS a scoped token
    holds nothing spendable of its own, so swapping in the platform credential
    is the whole point of its path — unchanged by any of this."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    flow = _make_flow(caller_bearer=_scoped_token(secret))

    asyncio.run(mod.request(flow))

    assert flow.response is None
    assert flow.request.headers["authorization"] == "Bearer sk-ant-oat01-PLATFORM"


def test_an_exhausted_budget_says_budget_not_misconfiguration(monkeypatch, tmp_path):
    """Admission resolves an identity only for a turn it is ALLOWING, so a
    refused project also arrives here with no identity — and would be reported
    as the box being misconfigured, sending whoever reads it to the wrong file.
    The refusal has to name the balance."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    verdict = _with_admission(mod, monkeypatch, None)
    verdict.allow = False
    verdict.reason = "budget spent: 10.0000 of 10.0000"
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _machine_flow(conn="c1")

    asyncio.run(mod.request(flow))

    assert flow.response is not None and flow.response.status_code == 429
    assert b"budget" in flow.response.content
    assert flow.request.headers["authorization"] == "Bearer sk-ant-oat01-machine-ticket"


def test_a_request_with_no_bearer_is_not_treated_as_carrying_its_own(
    monkeypatch, tmp_path
):
    """Claude Code calls some endpoints with no Authorization at all
    (`/api/event_logging/v2/batch` among them). "No credential" is not "someone
    else's credential": refusing those broke telemetry for every CONNECT caller
    whose project owns no machine — seen on dev as a burst of 503s from a
    project that has none. Such a request takes the ordinary swap path."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _machine_flow(conn="c1")
    del flow.request.headers["authorization"]
    flow.request.path = "/api/event_logging/v2/batch"

    asyncio.run(mod.request(flow))

    assert flow.response is None, "telemetry must not be refused"
    assert flow.request.headers["authorization"] == "Bearer sk-ant-oat01-PLATFORM"
