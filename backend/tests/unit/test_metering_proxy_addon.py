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
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADDON = REPO_ROOT / "deploy" / "metering-proxy" / "billing_addon.py"
CORE = REPO_ROOT / "deploy" / "metering-proxy" / "cheese_billing_core.py"
COMPOSE = REPO_ROOT / "deploy" / "metering-proxy" / "compose.yml"

_core_spec = importlib.util.spec_from_file_location("cheese_billing_core", CORE)
assert _core_spec and _core_spec.loader
core = importlib.util.module_from_spec(_core_spec)
_core_spec.loader.exec_module(core)


def _verdict(**named):
    """A REAL `Verdict`, not a SimpleNamespace shaped like one.

    A hand-rolled double stops carrying whatever the backend learned to answer
    next, and the addon reading that field fails as an AttributeError in a test
    whose subject is something else entirely.
    """
    return core.Verdict(**{"allow": True, "reason": "", **named})


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


def test_native_child_selection_is_admitted_and_rewritten(monkeypatch, tmp_path):
    mod = _load_addon(monkeypatch, tmp_path, inject="fixture", allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    calls = []

    def admit(project, topic, bearer, *, subagent=False, child_model=""):
        calls.append((subagent, child_model))
        return _verdict(model="claude-opus-5" if subagent else "claude-sonnet-5")

    mod.ADMISSION = SimpleNamespace(check=admit)
    flow = _make_flow()
    flow.request.headers.update(
        {
            "x-cheese-attr": "p/t",
            "x-claude-code-request-class": "subagent",
            "x-cheese-child-model": "claude-opus-5",
        }
    )
    asyncio.run(mod.requestheaders(flow))
    assert calls == [(True, "claude-opus-5")]
    assert "x-cheese-child-model" not in flow.request.headers
    assert flow.response is None
    body = flow.request.stream(b'{"model":"claude-haiku-4-5","messages":[]}')
    assert json.loads(body)["model"] == "claude-opus-5"


def test_native_child_header_selects_its_model_even_for_haiku(monkeypatch, tmp_path):
    """分身的 /v1/messages 推迟到 `request` 钩子才准入：主 agent 指定的模型
    在请求体里，头部时刻体还没到。准入拿到的指定随调用带上去，绑定用缓冲
    改写写回。指定的名字本身是 haiku 时的「不算指定」另有一条用例。"""
    mod = _load_addon(monkeypatch, tmp_path, inject="fixture", allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    calls = []

    def admit(project, topic, bearer, *, subagent=False, requested_model=""):
        calls.append((subagent, requested_model))
        return _verdict(model="claude-opus-5" if subagent else "claude-sonnet-5")

    mod.ADMISSION = SimpleNamespace(check=admit)
    flow = _make_flow()
    flow.request.headers.update(
        {
            "x-cheese-attr": "p/t",
            "x-claude-code-request-class": "subagent",
            "content-length": "34",
        }
    )
    asyncio.run(mod.requestheaders(flow))
    # 头部时刻不做准入、不开流式：等请求体到齐。
    assert calls == []
    assert flow.request.stream is False
    assert flow.response is None
    flow.request.content = b'{"model":"glm-4.6","messages":[]}'
    asyncio.run(mod.request(flow))
    assert calls == [(True, "glm-4.6")]
    assert flow.response is None
    assert json.loads(flow.request.content)["model"] == "claude-opus-5"


def test_the_main_conversation_keeps_streaming_its_body(monkeypatch, tmp_path):
    """缓冲只落在分身头上：主对话的 turn 是长会话反复重 POST 的大体（#654
    OOM 的教训），它必须还在头部时刻准入、流式改写。"""
    mod = _load_addon(monkeypatch, tmp_path, inject="fixture", allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    calls = []

    def admit(project, topic, bearer, *, subagent=False, requested_model=""):
        calls.append((subagent, requested_model))
        return _verdict(model="claude-sonnet-5")

    mod.ADMISSION = SimpleNamespace(check=admit)
    flow = _make_flow()
    flow.request.headers["x-cheese-attr"] = "p/t"
    asyncio.run(mod.requestheaders(flow))
    assert calls == [(False, "")]
    assert callable(flow.request.stream)
    # keep_haiku:主对话里 CLI 自己发给小快家族的请求留在 haiku。
    body = flow.request.stream(b'{"model":"claude-haiku-4-5","messages":[]}')
    assert json.loads(body)["model"] == "claude-haiku-4-5"


def test_inherited_and_explicit_child_models_do_not_share_admission():
    calls = []

    def admit(
        url, bearer, timeout, *, subagent=False, requested_model="", child_model=""
    ):
        calls.append((requested_model, child_model))
        return _verdict(model="claude-opus-5" if child_model else "claude-sonnet-5")

    admission = core.AdmissionGate("http://fixture/admission", post=admit)
    inherited = admission.check(
        "p", "t", "token", subagent=True, requested_model="claude-opus-5"
    )
    explicit = admission.check(
        "p", "t", "token", subagent=True, child_model="claude-opus-5"
    )
    assert inherited.model == "claude-sonnet-5"
    assert explicit.model == "claude-opus-5"
    assert calls == [("claude-opus-5", ""), ("", "claude-opus-5")]


def test_recorded_native_child_choices_replay_through_proxy_admission(
    monkeypatch, tmp_path
):
    fixture = json.loads(
        (
            REPO_ROOT
            / "backend/tests/fixtures/provider-recordings"
            / "claude-2.1.277-child-models.json"
        ).read_text()
    )
    records = fixture["requests"]
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == fixture["sha256"]
    mod = _load_addon(monkeypatch, tmp_path, inject="fixture", allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    admitted = []

    def admit(url, bearer, timeout, *, subagent=False, child_model=""):
        assert subagent
        admitted.append(child_model)
        return _verdict(model=child_model)

    mod.ADMISSION = core.AdmissionGate(mod.ADMISSION_URL, post=admit)
    for record in records:
        flow = _make_flow()
        flow.request.headers.update({**record["headers"], "x-cheese-attr": "p/t"})
        asyncio.run(mod.requestheaders(flow))
        assert flow.response is None
        encoded = json.dumps(record["body"]).encode()
        outgoing = flow.request.stream(encoded)
        assert json.loads(outgoing)["model"] == record["body"]["model"]
    assert set(admitted) == {"claude-sonnet-5", "claude-opus-5"}


def test_missing_injector_fails_closed_with_503(monkeypatch, tmp_path):
    """No credential on the host → a local 503 BEFORE forwarding. The caller's
    scoped bearer must NOT be rewritten (nothing is sent upstream)."""
    mod = _load_addon(monkeypatch, tmp_path, inject=None)
    flow = _make_flow(caller_bearer="scoped.caller.token")

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is not None, "must short-circuit, not forward"
    assert flow.response.status_code == 503
    # The scoped bearer was left untouched — it was never swapped for a real one.
    assert flow.request.headers["authorization"] == "Bearer scoped.caller.token"


def test_empty_injector_also_fails_closed(monkeypatch, tmp_path):
    """A present-but-empty token file is the same failure as an absent one."""
    mod = _load_addon(monkeypatch, tmp_path, inject="   \n")
    flow = _make_flow()

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is not None and flow.response.status_code == 503


def test_present_injector_is_swapped_in(monkeypatch, tmp_path):
    """With a real credential the caller's bearer is replaced by it and no error
    response is set (the request is allowed to forward)."""
    mod = _load_addon(monkeypatch, tmp_path, inject="sk-ant-oat01-REALTOKEN\n")
    flow = _make_flow(caller_bearer="scoped.caller.token")
    flow.request.headers["x-api-key"] = "stale-key"

    asyncio.run(mod.requestheaders(flow))

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


def _with_admission(mod, monkeypatch, upstream: str | None, **named):
    """Point the addon at a control plane that returns `upstream` for everyone."""
    verdict = _verdict(**{"pool": "subscription", "upstream": upstream, **named})
    monkeypatch.setattr(mod, "ADMISSION_URL", "http://control-plane.invalid/admission")
    monkeypatch.setattr(
        mod, "ADMISSION", SimpleNamespace(check=lambda project, topic, bearer: verdict)
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

    asyncio.run(mod.requestheaders(flow))

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
    asyncio.run(mod.requestheaders(_make_flow(caller_bearer=_scoped_token(secret))))

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

    asyncio.run(mod.requestheaders(flow))

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
    asyncio.run(mod.requestheaders(_make_flow(caller_bearer=_scoped_token(secret))))
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


def _scoped_token(
    secret: str,
    *,
    project: str = "p1",
    ttl_s: float = 3600.0,
    rc: bool = False,
) -> str:
    """A token shaped exactly like the backend's mint_scoped_token. Signed for
    real: the addon verifies the HMAC, so a hand-written string would only ever
    exercise the reject path."""
    raw = json.dumps(
        {"p": project, "t": "t1", "exp": time.time() + ttl_s, "rc": int(rc)}
    )
    body = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(digest).decode().rstrip('=')}"


def _basic(password: str) -> str:
    return "Basic " + base64.b64encode(f"cheese:{password}".encode()).decode()


def test_rc_bootstrap_routes_to_cheese_before_credential_injection(
    monkeypatch, tmp_path
):
    mod = _load_addon(
        monkeypatch, tmp_path, inject="provider-secret", scoped_secret="test-secret"
    )
    mod.RC_BASE = "https://backend.example/api"
    token = _scoped_token("test-secret", rc=True)
    mod.http_connect(_make_connect_flow(_basic(token)))
    flow = _make_flow(path="/v1/code/sessions", caller_bearer="machine-ticket")
    flow.request.headers["x-cheese-attr"] = "p1/some-other-topic"
    flow.request.headers["x-api-key"] = "stale-provider-key"
    asyncio.run(mod.requestheaders(flow))
    assert flow.request.host == "backend.example"
    assert flow.request.path == "/api/v1/code/sessions"
    assert flow.request.headers["x-cheese-token"] == token
    assert "authorization" not in flow.request.headers
    assert "x-api-key" not in flow.request.headers
    assert "x-cheese-attr" not in flow.request.headers
    assert flow.server_conn.via is None
    assert mod.verify_scoped_token(token, "test-secret")["t"] == "t1"


def test_the_control_endpoints_are_answered_by_cheese_whatever_the_pool_is(
    monkeypatch, tmp_path
):
    """一台机器只有一种启动环境（结论 46），所以身份这一问不取决于任何答复。

    让 `/api/oauth/profile` 原样上游，回包里是**平台自己那个订阅账号**的 uuid 和
    email —— 而问它的是一个在跑别人代码的沙箱。答复里换成这条活自己的地点：组织
    是项目，账号是房间。

    这里没有配任何准入：判据从前是「准入答出来的池」，而那是把开机放在一个
    fail-open 的调用后面 —— 答错一次就泄露一次，而泄露出去的身份收不回来。
    """
    mod = _load_addon(
        monkeypatch, tmp_path, inject="provider-secret", scoped_secret="test-secret"
    )
    token = _scoped_token("test-secret", rc=True)
    mod.http_connect(_make_connect_flow(_basic(token)))
    for path in (
        "/api/oauth/profile",
        "/api/claude_code/settings",
        "/api/claude_code/policy_limits",
    ):
        flow = _make_flow(path=path, caller_bearer="machine-ticket")
        # 归账那一侧允许 header 在已证明的项目内部挑房间；身份这一侧不允许 ——
        # 答出去的是这条 RC 会话被签在哪儿。
        flow.request.headers["x-cheese-attr"] = "p1/some-other-topic"
        asyncio.run(mod.requestheaders(flow))
        assert flow.response.status_code == (204 if path.endswith("settings") else 200)
        assert flow.request.stream is False
        assert flow.server_conn.via is None
        # 平台的订阅凭据没有被挂上去，请求也没有出过这一跳。
        assert flow.request.headers.get("authorization") != "Bearer provider-secret"
        if path.endswith("profile"):
            data = json.loads(flow.response.content)
            assert data["organization"]["uuid"] == "p1"
            assert data["account"]["uuid"] == "t1"
        elif path.endswith("settings"):
            assert flow.response.content == b""
        else:
            assert (
                json.loads(flow.response.content)["restrictions"][
                    "allow_remote_control"
                ]["allowed"]
                is True
            )


def _assert_answered_by_cheese(mod, flow) -> None:
    """这一跳没出去，回的是这条活自己的地点，平台的订阅凭据没挂上去。"""
    asyncio.run(mod.requestheaders(flow))
    assert flow.response is not None
    assert flow.response.status_code == 200
    assert flow.request.headers.get("authorization") != "Bearer provider-secret"
    data = json.loads(flow.response.content)
    assert data["organization"]["uuid"] == "p1"
    assert data["account"]["uuid"] == "t1"


def test_an_unconfigured_admission_does_not_echo_the_platform_account(
    monkeypatch, tmp_path
):
    """没设 `CHEESE_ADMISSION_URL` 的盒子上，`/api/oauth/profile` 仍然本地应答。

    这条路径从前问过准入，而准入答不出池的时候，`pool` 那一格是没人填过的默认值，
    读出来是订阅 —— 于是在最不知情的那一刻判成订阅会话：请求一路走到底挂上平台
    自己的订阅凭据，回包里是那个账号的 uuid 和 email，而收件人是一个在跑别人代码
    的沙箱。现在它不问了，所以这条测试盯的是那个状态下开机仍然成立。

    没设过这个变量不是假设 —— 它是 `billing_addon` 里那段注释记着的、烧掉一天的
    那次。这条会话还带着自己的 ccproxy 票：那一支从前会撞上「说不出用谁的身份发」
    的 503，RC 会话根本起不来。
    """
    mod = _load_addon(
        monkeypatch, tmp_path, inject="provider-secret", scoped_secret="test-secret"
    )
    mod.http_connect(_make_connect_flow(_basic(_scoped_token("test-secret", rc=True))))
    _assert_answered_by_cheese(
        mod, _make_flow(path="/api/oauth/profile", caller_bearer="machine-ticket")
    )


def test_an_unreachable_admission_does_not_echo_the_platform_account(
    monkeypatch, tmp_path
):
    """后端不可达时准入 fail-open 放行，而开机不等它。

    放行的是花钱，不是身份：一个连不上的控制面什么也说不出，而「谁代表一个
    Anthropic 账号回话」这一问，答案里本来就不该有它。
    """

    def unreachable(url, bearer, timeout_s):
        raise OSError("control plane is down")

    mod = _load_addon(
        monkeypatch, tmp_path, inject="provider-secret", scoped_secret="test-secret"
    )
    url = "http://control-plane.invalid/admission"
    monkeypatch.setattr(mod, "ADMISSION_URL", url)
    monkeypatch.setattr(mod, "ADMISSION", mod.AdmissionGate(url, post=unreachable))
    token = _scoped_token("test-secret", rc=True)
    _assert_answered_by_cheese(
        mod, _make_flow(path="/api/oauth/profile", caller_bearer=token)
    )


def test_rc_without_backend_never_falls_through_to_official_service(
    monkeypatch, tmp_path
):
    mod = _load_addon(
        monkeypatch, tmp_path, inject="provider-secret", scoped_secret="test-secret"
    )
    token = _scoped_token("test-secret", rc=True)
    flow = _make_flow(path="/v1/code/sessions", caller_bearer=token)
    asyncio.run(mod.requestheaders(flow))
    assert flow.response.status_code == 503
    assert flow.request.headers["authorization"] != "Bearer provider-secret"


def test_rc_telemetry_is_consumed_without_attaching_provider_credential(
    monkeypatch, tmp_path
):
    mod = _load_addon(
        monkeypatch, tmp_path, inject="provider-secret", scoped_secret="test-secret"
    )
    token = _scoped_token("test-secret", rc=True)
    mod.http_connect(_make_connect_flow(_basic(token)))
    flow = _make_flow(path="/api/event_logging/v2/batch", caller_bearer="")
    asyncio.run(mod.requestheaders(flow))
    assert flow.response.status_code == 200
    assert flow.request.stream is False
    assert flow.request.headers["authorization"] != "Bearer provider-secret"


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

    asyncio.run(mod.requestheaders(flow))

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

    asyncio.run(mod.requestheaders(flow))

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
    _with_admission(
        mod, monkeypatch, None, allow=False, reason="budget spent: 10.0000 of 10.0000"
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _machine_flow(conn="c1")

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is not None and flow.response.status_code == 429
    assert b"budget" in flow.response.content
    assert flow.request.headers["authorization"] == "Bearer sk-ant-oat01-machine-ticket"


def test_a_request_with_no_bearer_is_not_treated_as_carrying_its_own(
    monkeypatch, tmp_path
):
    """Claude Code calls some endpoints with no Authorization at all. "No
    credential" is not "someone else's credential": reading an absent bearer as
    a foreign one refused every CONNECT caller whose project owns no machine —
    seen on dev as a burst of 503s from a project that has none. Such a request
    takes the ordinary swap path.

    Shown on a turn rather than on the telemetry call that produced the
    incident: telemetry is answered from the table now, before `_attribution`
    is consulted at all, so that path can no longer demonstrate the rule it
    taught us."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _machine_flow(conn="c1")
    del flow.request.headers["authorization"]

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is None, "a bearer-less caller must not be refused"
    assert flow.request.headers["authorization"] == "Bearer sk-ant-oat01-PLATFORM"


def _make_response(status_code=200, content_type="text/event-stream"):
    from mitmproxy import http

    resp = http.Response.make(status_code=status_code, content=b"", headers={})
    resp.headers = {"content-type": content_type}
    return resp


def test_a_streamed_message_is_metered_through_the_response_tee(monkeypatch, tmp_path):
    """The response body is no longer buffered whole (that is what OOM-kills the
    proxy on long turns); it streams through a tee that scrapes usage from the
    SSE incrementally. Driving that tee must still land the turn on the meter."""
    mod = _load_addon(monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM")

    flow = _make_flow()
    flow.metadata["cheese_attr"] = ("proj-x", "topic-y")
    flow.response = _make_response()

    mod.responseheaders(flow)
    assert callable(flow.response.stream), "the SSE response must be streamed"

    body = b"\n".join(
        [
            b'data: {"type":"message_start","message":{"model":"claude-opus-5",'
            b'"usage":{"input_tokens":10,"output_tokens":1}}}',
            b'data: {"type":"message_delta","usage":{"output_tokens":90}}',
            b"",
        ]
    )
    # Chunks pass through unchanged, and the end-of-stream sentinel records.
    assert mod.METER.used() == 0
    for i in range(0, len(body), 5):
        chunk = body[i : i + 5]
        assert flow.response.stream(chunk) == chunk
    flow.response.stream(b"")  # sentinel

    assert mod.METER.used() == 100  # 10 input + 90 output


def test_a_non_message_response_streams_without_metering(monkeypatch, tmp_path):
    """Everything that is not a metered message turn still streams (so nothing
    is buffered) but must not touch the meter."""
    mod = _load_addon(monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM")

    flow = _make_flow(path="/api/oauth/profile")
    flow.response = _make_response(content_type="application/json")

    mod.responseheaders(flow)

    assert flow.response.stream is True
    assert mod.METER.used() == 0


def test_gateway_responses_do_not_charge_the_subscription(monkeypatch, tmp_path):
    mod = _load_addon(monkeypatch, tmp_path, inject=None)
    mod.GATEWAY_BASE = "http://gateway:4000"
    mod.ADMISSION_URL = "http://backend/llm/admission"
    mod.ALLOW_HEADER_ATTR = True
    mod.ADMISSION.check = lambda *args: _verdict(pool="gateway", key="project-key")
    for content_type in ("application/json", "text/event-stream"):
        flow = _make_flow()
        flow.request.headers["x-cheese-attr"] = "project/topic"
        asyncio.run(mod.requestheaders(flow))
        assert flow.response is None
        assert flow.request.host == "gateway"
        flow.response = _make_response(content_type=content_type)
        flow.response.raw_content = json.dumps(
            {"model": "glm-5.2", "usage": {"input_tokens": 10, "output_tokens": 20}}
        ).encode()
        mod.responseheaders(flow)
        if content_type == "text/event-stream":
            assert callable(flow.response.stream)
        else:
            assert flow.response.stream is True
        if callable(flow.response.stream):
            flow.response.stream(b'data: {"type":"message_stop"}\n\n')
            flow.response.stream(b"")
        mod.response(flow)
    assert mod.METER.used() == 0
    assert not mod.USAGE_LOG.exists()


def test_gateway_timing_preserves_request_boundary_and_omits_credentials(
    monkeypatch, tmp_path, caplog
):
    mod = _load_addon(monkeypatch, tmp_path, inject=None)
    mod.GATEWAY_BASE = "http://gateway:4000"
    mod.ADMISSION_URL = "http://backend/llm/admission"
    mod.ALLOW_HEADER_ATTR = True
    clock = [0.0]
    monkeypatch.setattr(mod.time, "perf_counter", lambda: clock[0])

    def admit(*args):
        clock[0] = 0.035
        return _verdict(pool="gateway", key="private-provider-key")

    mod.ADMISSION.check = admit
    flow = _make_flow(caller_bearer="private-caller-token")
    flow.request.headers["x-cheese-attr"] = "project/topic"
    flow.request.timestamp_start = 100.0
    flow.request.timestamp_end = 100.02
    flow.client_conn.timestamp_start = 99.0
    flow.client_conn.timestamp_tls_setup = 99.4
    flow.server_conn.id = "server-connection"
    flow.server_conn.timestamp_start = 100.04
    flow.server_conn.timestamp_tcp_setup = 100.05
    asyncio.run(mod.requestheaders(flow))
    flow.response = _make_response()
    flow.response.headers["x-litellm-call-id"] = "gateway-call"
    flow.response.headers["private-header"] = "private-value"
    flow.response.timestamp_start = 102.0
    flow.response.timestamp_end = 103.0
    flow.response.raw_content = b"private-response-body"
    with caplog.at_level("INFO", logger="cheese.metering"):
        mod.responseheaders(flow)
        flow.response.stream(b'data: {"type":"message_stop"}\n\n')
        mod.response(flow)
    assert callable(flow.response.stream)
    assert mod.METER.used() == 0
    messages = [
        r.message
        for r in caplog.records
        if r.message.startswith("gateway_request_timing ")
    ]
    assert len(messages) == 1
    event = json.loads(messages[0].split(" ", 1)[1])
    assert event["gateway_request_id"] == "gateway-call"
    assert event["request_start"] == 100.0
    assert event["request_end"] == 100.02
    assert event["client_connection"] == {
        "id": flow.client_conn.id,
        "start": 99.0,
        "tls_setup": 99.4,
    }
    assert event["server_connection"] == {
        "id": "server-connection",
        "start": 100.04,
        "tcp_setup": 100.05,
        "tls_setup": None,
    }
    assert event["response_start"] == 102.0
    assert event["response_end"] == 103.0
    assert round(event["admission_ms"]) == 35
    assert event["admission_phases_ms"] == {
        "thread_queue": 0,
        "check": 35,
        "loop_resume": 0,
    }
    assert event["route_ready"] is not None
    assert event["status"] == 200 and event["failed"] is False
    assert "private-" not in messages[0]


@pytest.mark.parametrize(
    "ending,expected",
    [
        (b'data: {"type":"message_stop"}\n\n', False),
        (
            b'data: {"type":"error","error":{"message":"private upstream text"}}\n\n',
            True,
        ),
        (b"", True),
    ],
)
async def test_gateway_stream_failure_is_reported_without_waiting_for_client_retries(
    monkeypatch, tmp_path, ending, expected
):
    mod = _load_addon(monkeypatch, tmp_path, inject=None, scoped_secret="test-secret")
    mod.ADMISSION_URL = "http://backend/llm/admission"
    flow = _make_flow()
    flow.metadata.update(cheese_pool="gateway", cheese_attr=("project", "topic"))
    flow.response = _make_response()
    flow.response.headers["x-litellm-call-id"] = "call-1"
    posted = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return b"{}"

    def post(request, timeout):
        posted.append((request.full_url, json.loads(request.data)))
        return Response()

    monkeypatch.setattr(mod, "urlopen", post)
    mod.responseheaders(flow)
    prefix = (
        b'data: {"type":"content_block_delta","delta":{"text":"private answer"}}\n\n'
    )
    # Arbitrary network chunk boundaries must preserve both bytes and detection.
    for chunk in (prefix[:9], prefix[9:], ending[:12], ending[12:], b""):
        assert flow.response.stream(chunk) == chunk
    mod.response(flow)
    await asyncio.gather(*mod._failure_reports)
    assert len(posted) == int(expected)
    if expected:
        url, body = posted[0]
        assert url == "http://backend/backend-errors"
        assert body["errors"][0]["exc_type"] == "GatewayStreamError"
        assert body["errors"][0]["request_id"] == "call-1"
        assert "private" not in json.dumps(body)
    assert mod.METER.used() == 0


async def test_client_disconnect_does_not_raise_a_model_failure(monkeypatch, tmp_path):
    mod = _load_addon(monkeypatch, tmp_path, inject=None, scoped_secret="test-secret")
    flow = _make_flow()
    flow.metadata.update(cheese_pool="gateway", cheese_attr=("project", "topic"))
    flow.response = _make_response()
    flow.error = SimpleNamespace(msg="client disconnected")
    mod.responseheaders(flow)
    mod.error(flow)
    assert not mod._failure_reports


def test_admission_timing_separates_executor_queue_from_check(
    monkeypatch, tmp_path, caplog
):
    mod = _load_addon(monkeypatch, tmp_path, inject=None)
    mod.GATEWAY_BASE = "http://gateway:4000"
    mod.ADMISSION_URL = "http://backend/llm/admission"
    mod.ALLOW_HEADER_ATTR = True
    clock = [0.0]
    monkeypatch.setattr(mod.time, "perf_counter", lambda: clock[0])

    def admit(*args):
        clock[0] += 0.035
        return _verdict(pool="gateway", key="private-key")

    async def queued_thread(function, *args, **kwargs):
        clock[0] += 1.8
        result = function(*args, **kwargs)
        clock[0] += 0.004
        return result

    mod.ADMISSION.check = admit
    monkeypatch.setattr(mod.asyncio, "to_thread", queued_thread)
    flow = _make_flow(caller_bearer="private-token")
    flow.request.headers["x-cheese-attr"] = "project/topic"
    asyncio.run(mod.requestheaders(flow))
    flow.response = _make_response()
    with caplog.at_level("INFO", logger="cheese.metering"):
        mod.response(flow)
    event = json.loads(caplog.records[-1].message.split(" ", 1)[1])
    assert round(event["admission_ms"]) == 1839
    assert {
        key: round(value) for key, value in event["admission_phases_ms"].items()
    } == {
        "thread_queue": 1800,
        "check": 35,
        "loop_resume": 4,
    }
    assert "private-" not in caplog.records[-1].message


def test_failed_gateway_request_keeps_timing_without_error_details(
    monkeypatch, tmp_path, caplog
):
    mod = _load_addon(monkeypatch, tmp_path, inject=None)
    flow = _make_flow()
    flow.metadata["cheese_pool"] = "gateway"
    flow.request.timestamp_start = 100.0
    flow.error = SimpleNamespace(msg="private-upstream-details")
    with caplog.at_level("INFO", logger="cheese.metering"):
        mod.error(flow)
    event = json.loads(caplog.records[-1].message.split(" ", 1)[1])
    assert event["request_start"] == 100.0
    assert event["failed"] is True and event["status"] is None
    assert event["response_start"] is None and event["response_end"] is None
    assert "private-" not in caplog.records[-1].message


_BOOT_PATHS = (
    "/api/oauth/profile",
    "/api/claude_code/settings",
    "/api/claude_code/policy_limits",
    "/api/eval/sdk-client",
    "/api/event_logging/v2/batch",
)


def test_the_boot_endpoints_are_answered_here_without_an_rc_claim(
    monkeypatch, tmp_path
):
    """一台机器只有一种启动环境（结论 46），所以开机不能取决于这个会话有没有 RC。

    The caller below proves no place at all — no scoped secret, no rc claim, the
    weakest caller the proxy ever serves. Every startup path still has to be
    answered from the table: the alternative is Anthropic answering "who am I"
    for a sandbox running someone else's code, with the platform subscription's
    own uuid and email in the reply.
    """
    mod = _load_addon(monkeypatch, tmp_path, inject="subscription-secret")
    mod.UPSTREAM_VIA = "subscription-proxy:3128"

    for path in _BOOT_PATHS:
        flow = _make_flow(path=path)
        asyncio.run(mod.requestheaders(flow))
        assert flow.response is not None, path
        assert flow.response.status_code in (200, 204), path
        # Nothing went out: no upstream hop, and no credential attached to one.
        assert flow.server_conn.via is None, path
        assert flow.request.headers["authorization"] != "Bearer subscription-secret"
        assert flow.request.stream is False, path


def test_the_identity_answered_here_is_cheeses_own(monkeypatch, tmp_path):
    mod = _load_addon(monkeypatch, tmp_path, inject="subscription-secret")
    flow = _make_flow(path="/api/oauth/profile")
    asyncio.run(mod.requestheaders(flow))
    body = json.loads(flow.response.content)
    assert "anthropic.com" not in body["account"]["email"]


def test_the_rc_bridge_is_announced_only_to_a_session_that_has_rc(
    monkeypatch, tmp_path
):
    """The flags are what make Claude Code open `/v1/code/…`. A session whose
    token carries no rc claim has no route for those, so announcing the bridge
    to it would send control traffic upstream on the platform's credential."""
    secret = "scoped-secret"
    mod = _load_addon(monkeypatch, tmp_path, inject="x", scoped_secret=secret)

    plain = _make_flow(path="/api/eval/sdk-client")
    asyncio.run(mod.requestheaders(plain))
    assert json.loads(plain.response.content)["features"] == {}

    rc_flow = _make_flow(
        path="/api/eval/sdk-client",
        caller_bearer=_scoped_token(secret, rc=True),
    )
    asyncio.run(mod.requestheaders(rc_flow))
    features = json.loads(rc_flow.response.content)["features"]
    assert features["tengu_ccr_bridge"] == {"defaultValue": True}
    assert rc_flow.server_conn.via is None


# --- a refusal has to REACH the caller --------------------------------------
# The proxy streams the request body straight through (#654: buffering a long
# turn's grown conversation is what OOM-killed it). Streaming and answering
# locally are mutually exclusive in mitmproxy, and a refusal that forgets to
# take the streaming decision back does not degrade — it kills the connection,
# so the caller waits out its own timeout and never learns why it was refused.
# Measured on the dev box over 48h: 68 refused message turns, zero refusals
# delivered, 350 crashes, and a client that retried into the same wall forever.


def _mitmproxy_takes_over(flow, *, request_has_body: bool = True) -> None:
    """What mitmproxy does with the flow once `requestheaders` returns.

    Transcribed from mitmproxy 12.1.2 (`HttpStream.state_wait_for_request_headers`
    → `start_request_stream`): a request that still has a body to come is handed
    to the streaming path whenever `flow.request.stream` is set, and that path
    raises outright if a response has already been produced.

    The body condition is the whole reason this hid for so long — `stream and
    not event.end_stream` — so a refused GET was always delivered and a refused
    `POST /v1/messages` never was.
    """
    if getattr(flow.request, "stream", False) and request_has_body:
        if flow.response is not None:
            raise NotImplementedError(
                "Can't set a response and enable streaming at the same time."
            )


def _refusals_of_a_message_turn(monkeypatch, tmp_path):
    """Every way `requestheaders` refuses a POST /v1/messages, each built the way
    a box actually produces it. Yields (what it is, addon module, flow)."""
    secret = "s3cr3t"

    # The one seen in production: a machine carrying its own ccproxy ticket that
    # the control plane could not place on an identity.
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    yield "a machine with no identity to send it as", mod, _machine_flow(conn="c1")

    # The same caller, refused by its project's balance instead.
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    _with_admission(
        mod, monkeypatch, None, allow=False, reason="budget spent: 10.0000 of 10.0000"
    )
    mod.http_connect(
        _connect_flow_on("c2", _basic(_scoped_token(secret, project="p9")))
    )
    yield "an exhausted project budget", mod, _machine_flow(conn="c2")

    # A caller that cannot prove which project to bill.
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    yield "an unattributable caller", mod, _make_flow(caller_bearer="not-a-token")

    # A box whose own subscription credential is missing. Its own directory:
    # `inject=None` means "write nothing", so sharing one with the cases above
    # would leave THEIR token file sitting there and this box would be fine.
    mod = _load_addon(
        monkeypatch, tmp_path / "bare-box", inject=None, scoped_secret=secret
    )
    yield (
        "no platform credential on the box",
        mod,
        _make_flow(caller_bearer=_scoped_token(secret)),
    )

    # A gateway project on a deployment with no pool to send it to.
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    _with_admission(mod, monkeypatch, None, pool="gateway", key=None)
    yield (
        "a gateway project with no route",
        mod,
        _make_flow(caller_bearer=_scoped_token(secret)),
    )


def test_every_refusal_of_a_message_turn_reaches_the_caller(monkeypatch, tmp_path):
    """The refusal is the product here: each of these exists to tell somebody
    exactly what went wrong (which budget, which missing credential, which piece
    of the box is unconfigured). A refusal that kills the connection instead
    reports none of it — the caller sees a hung platform, and the operator sees
    a crash log naming mitmproxy rather than the cause."""
    for what, mod, flow in _refusals_of_a_message_turn(monkeypatch, tmp_path):
        asyncio.run(mod.requestheaders(flow))

        assert flow.response is not None, f"{what}: nothing refused it"
        try:
            _mitmproxy_takes_over(flow)
        except NotImplementedError as exc:
            raise AssertionError(
                f"{what}: refused with {flow.response.status_code}, but the "
                f"refusal never reaches the caller — {exc}"
            ) from exc


def _forwards_of_a_message_turn(monkeypatch, tmp_path):
    """Every way `requestheaders` lets a POST /v1/messages through.
    Yields (what it is, addon module, flow)."""
    secret = "s3cr3t"

    # SWAP: a scoped caller gets the platform's credential.
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    yield "the swap path", mod, _make_flow(caller_bearer=_scoped_token(secret))

    # PASS THROUGH: an enrolled machine keeps its own ticket.
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    _with_admission(mod, monkeypatch, "m516:pw516")
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    yield "the pass-through path", mod, _machine_flow(conn="c1")

    # GATEWAY: the request is re-aimed at the API-key pool and forwarded there.
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    _with_admission(mod, monkeypatch, None, pool="gateway", key="sk-virtual-key")
    monkeypatch.setattr(mod, "GATEWAY_BASE", "http://litellm.invalid:4000")
    yield "the gateway path", mod, _make_flow(caller_bearer=_scoped_token(secret))


def test_every_forwarded_message_turn_still_streams_its_body(monkeypatch, tmp_path):
    """The other half of the same decision, and the reason the refusal fix must
    not be "stop streaming". A long agent turn re-POSTs its entire grown
    conversation every turn; buffering that whole is what OOM-kills this proxy
    (#654), and it comes back the moment ONE forwarding path stops streaming."""
    for what, mod, flow in _forwards_of_a_message_turn(monkeypatch, tmp_path):
        asyncio.run(mod.requestheaders(flow))

        assert flow.response is None, f"{what}: was refused, not forwarded"
        assert flow.request.stream is True, f"{what}: forwards a buffered body"


def test_a_turn_whose_body_never_names_a_model_is_refused_not_run_as_it_came(
    monkeypatch, tmp_path
):
    """I27's third exit, closed. The launch environment names no model, so the
    binding reaches the turn by being written into the request body — and when
    that write cannot happen, forwarding the body as it came runs the turn on
    whatever model the CLI picked for itself. On the subscription that is a
    silent change of brain; on the gateway it is a hard LiteLLM failure
    reported as the pool's own. So none of the body is forwarded, and the
    client is told why.

    The refusal is delivered on the way back rather than from `requestheaders`:
    mitmproxy fixes the streaming decision when that hook returns, and a flow
    already streaming its request body can no longer be given a response (see
    `_refuse`). Nothing was spent to reach that moment — the pool was sent an
    empty request.
    """
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, inject="sk-ant-oat01-PLATFORM", scoped_secret=secret
    )
    _with_admission(mod, monkeypatch, None, model="claude-opus-5")
    flow = _make_flow(caller_bearer=_scoped_token(secret))

    asyncio.run(mod.requestheaders(flow))
    assert callable(flow.request.stream), "the body must still stream"

    body = json.dumps({"messages": [{"role": "user", "content": "跑一下测试"}]})
    assert flow.request.stream(body.encode()) == b"", "no model member in the head"
    assert flow.request.stream(b"") == b"", "and none of it goes upstream"

    flow.response = _make_response(status_code=400, content_type="application/json")
    mod.responseheaders(flow)
    assert getattr(flow.response, "stream", False) is False, "stays buffered"
    mod.response(flow)

    assert flow.response.status_code == 400
    said = json.loads(flow.response.content)["error"]["message"]
    assert "claude-opus-5" in said, said
    assert "cheese" in said, said


def test_a_subagents_own_haiku_request_is_not_a_specification(monkeypatch, tmp_path):
    """分身发出的 haiku 类请求（CLI 自己的后台类）照旧：准入按未指定问，
    绑定改写盖成分身默认 —— 与改动前逐字节一致,不吃队友白名单。"""
    mod = _load_addon(monkeypatch, tmp_path, inject="fixture", allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    calls = []

    def admit(project, topic, bearer, *, subagent=False, requested_model=""):
        calls.append((subagent, requested_model))
        return _verdict(model="claude-sonnet-5")

    mod.ADMISSION = SimpleNamespace(check=admit)
    flow = _make_flow()
    flow.request.headers.update(
        {
            "x-cheese-attr": "p/t",
            "x-claude-code-request-class": "subagent",
            "content-length": "42",
        }
    )
    asyncio.run(mod.requestheaders(flow))
    flow.request.content = b'{"model":"claude-haiku-4-5","messages":[]}'
    asyncio.run(mod.request(flow))
    assert calls == [(True, "")]
    assert flow.response is None
    assert json.loads(flow.request.content)["model"] == "claude-sonnet-5"


def test_a_subagent_echoing_the_parents_model_reads_as_inherit(monkeypatch, tmp_path):
    """体里的模型 == 主对话被改写前的那个值 = CC 的「继承」长相,不是指定。

    device 启动环境不钉模型,CC 回显的是它自己的内建默认 —— 这个名字在准入
    的席位配置里不存在,送上去普通分身全被当成范围外指定(2026-09-23 事故)。
    代理自己观察主对话的改写,认出回显,按未指定走。"""
    mod = _load_addon(monkeypatch, tmp_path, inject="fixture", allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    calls = []

    def admit(project, topic, bearer, *, subagent=False, requested_model=""):
        calls.append((subagent, requested_model))
        return _verdict(model="kimi-k3")

    mod.ADMISSION = SimpleNamespace(check=admit)

    # 主对话:体里写着 CC 的内建默认,被改写成绑定模型 —— 改写前的值被记住。
    main = _make_flow()
    main.request.headers["x-cheese-attr"] = "p/t"
    asyncio.run(mod.requestheaders(main))
    assert callable(main.request.stream)
    out = main.request.stream(b'{"model":"claude-sonnet-5","messages":[]}')
    assert json.loads(out)["model"] == "kimi-k3", "main flow still gets bound"
    assert mod.PARENT_MODEL.get(("p", "t")) == "claude-sonnet-5"

    # 分身:体里回显同一个名字 —— 准入按「未指定」问,绑定照分身默认写回。
    fork = _make_flow()
    fork.request.headers.update(
        {
            "x-cheese-attr": "p/t",
            "x-claude-code-request-class": "subagent",
            "content-length": "42",
        }
    )
    asyncio.run(mod.requestheaders(fork))
    fork.request.content = b'{"model":"claude-sonnet-5","messages":[]}'
    asyncio.run(mod.request(fork))
    assert calls[-1] == (True, "")
    assert json.loads(fork.request.content)["model"] == "kimi-k3"


def test_the_parents_haiku_background_requests_do_not_overwrite_the_record(
    monkeypatch, tmp_path
):
    """CLI 自己的 haiku 后台请求（会话标题、路径建议）也走主对话路径；在
    gateway 池上它们照样被改写（不留 haiku），replaced=True —— 但它们不是
    父会话的工作模型。记进 PARENT_MODEL 就会把真回显盖掉,普通分身再次全灭。"""
    mod = _load_addon(monkeypatch, tmp_path, inject="fixture", allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    mod.GATEWAY_BASE = "http://gateway:4000"

    def admit(project, topic, bearer, *, subagent=False, requested_model=""):
        return _verdict(pool="gateway", key="k", model="kimi-k3")

    mod.ADMISSION = SimpleNamespace(check=admit)

    main = _make_flow()
    main.request.headers["x-cheese-attr"] = "p/t"
    asyncio.run(mod.requestheaders(main))
    main.request.stream(b'{"model":"claude-sonnet-5","messages":[]}')
    assert mod.PARENT_MODEL.get(("p", "t")) == "claude-sonnet-5"

    title = _make_flow()
    title.request.headers["x-cheese-attr"] = "p/t"
    asyncio.run(mod.requestheaders(title))
    out = title.request.stream(b'{"model":"claude-haiku-4-5","messages":[]}')
    assert json.loads(out)["model"] == "kimi-k3", "haiku 请求照样被绑定改写"
    assert mod.PARENT_MODEL.get(("p", "t")) == "claude-sonnet-5", "但不污染记录"


def test_a_subagent_naming_a_different_model_is_still_a_specification(
    monkeypatch, tmp_path
):
    """体里的模型 ≠ 主对话回显值:这才是主 agent 的指定,原样送上准入。"""
    mod = _load_addon(monkeypatch, tmp_path, inject="fixture", allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    calls = []

    def admit(project, topic, bearer, *, subagent=False, requested_model=""):
        calls.append((subagent, requested_model))
        return _verdict(model=requested_model or "kimi-k3")

    mod.ADMISSION = SimpleNamespace(check=admit)

    main = _make_flow()
    main.request.headers["x-cheese-attr"] = "p/t"
    asyncio.run(mod.requestheaders(main))
    main.request.stream(b'{"model":"claude-sonnet-5","messages":[]}')

    named = _make_flow()
    named.request.headers.update(
        {
            "x-cheese-attr": "p/t",
            "x-claude-code-request-class": "subagent",
            "content-length": "40",
        }
    )
    asyncio.run(mod.requestheaders(named))
    named.request.content = b'{"model":"mimo-v2.6-pro","messages":[]}'
    asyncio.run(mod.request(named))
    assert calls[-1] == (True, "mimo-v2.6-pro")


def test_an_oversize_subagent_body_keeps_the_old_streamed_road(monkeypatch, tmp_path):
    """缓冲有上限：超过上限(或没说多大)的分身请求退回旧路 —— 头部时刻准入、
    流式放行、绑分身默认。上限是发现层的成本,不是资格,更不是拒绝的理由。"""
    mod = _load_addon(monkeypatch, tmp_path, inject="fixture", allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    calls = []

    def admit(project, topic, bearer, *, subagent=False, requested_model=""):
        calls.append((subagent, requested_model))
        return _verdict(model="claude-sonnet-5")

    mod.ADMISSION = SimpleNamespace(check=admit)
    flow = _make_flow()
    flow.request.headers.update(
        {
            "x-cheese-attr": "p/t",
            "x-claude-code-request-class": "subagent",
            "content-length": str(mod.DEFER_BODY_LIMIT + 1),
        }
    )
    asyncio.run(mod.requestheaders(flow))
    assert calls == [(True, "")]
    assert callable(flow.request.stream)
    body = flow.request.stream(b'{"model":"glm-4.6","messages":[]}')
    assert json.loads(body)["model"] == "claude-sonnet-5"
