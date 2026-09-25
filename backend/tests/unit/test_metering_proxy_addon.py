"""Behavioural tests for the metering proxy addon (deploy/metering-proxy/
billing_addon.py).

The addon imports mitmproxy, which the backend venv does not carry, so these
load it by path behind a minimal mitmproxy stub and drive its hooks directly —
testing what leaves the proxy, not the source.

The proxy holds no model credential. A session's Authorization is its host's
own Claude login: a subscription turn carries it to Anthropic untouched, and a
turn admission places on the API-key pool leaves with the project's virtual key
instead — the session's credential must never reach the gateway. Attribution
comes from the scoped token the connection proved at CONNECT (or a scoped
Bearer), never from that credential.
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
    scoped_secret: str = "",
    allow_header_attr: str = "",
    credential: str | None = None,
):
    """Load a FRESH billing_addon module with env captured for this test.

    ``credential`` is the platform's Claude credential file's content; None
    leaves the platform logged out.

    The addon reads all of these at import time, so env must be set before the
    module is loaded — hence a fresh module per call.
    """
    global _ADDON_LOADS
    _install_mitmproxy_stub(monkeypatch)

    monkeypatch.setenv("CHEESE_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("CHEESE_SCOPED_SECRET", scoped_secret)
    monkeypatch.setenv("CHEESE_ALLOW_HEADER_ATTR", allow_header_attr)
    monkeypatch.setenv("CHEESE_ADMISSION_URL", "")
    monkeypatch.setenv("CHEESE_GATEWAY_BASE", "")
    monkeypatch.setenv("CHEESE_TOKEN_CAP", "0")
    credential_file = tmp_path / "claude-credential" / "credential"
    credential_file.parent.mkdir(parents=True, exist_ok=True)
    if credential is not None:
        credential_file.write_text(credential)
    monkeypatch.setenv("CHEESE_CLAUDE_CREDENTIAL", str(credential_file))

    _ADDON_LOADS += 1
    name = f"billing_addon_{_ADDON_LOADS}"
    spec = importlib.util.spec_from_file_location(name, ADDON)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# What a session's Authorization carries: its host's own Claude login. Distinct
# enough that a test can look for it in every header of an outgoing request.
SESSION_CREDENTIAL = "sk-ant-oat01-REAL-SESSION-CREDENTIAL"


def _make_flow(*, path="/v1/messages", caller_bearer=SESSION_CREDENTIAL):
    """A flow that clears the addon's host/TLS allowlist (Anthropic name over
    proxy-terminated TLS), carrying the caller's Bearer — by default the
    session's own Claude credential."""
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
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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


def _with_admission(mod, monkeypatch, **named):
    """Point the addon at a control plane that returns one verdict for everyone."""
    verdict = _verdict(**{"pool": "subscription", **named})
    monkeypatch.setattr(mod, "ADMISSION_URL", "http://control-plane.invalid/admission")
    monkeypatch.setattr(
        mod, "ADMISSION", SimpleNamespace(check=lambda project, topic, bearer: verdict)
    )
    return verdict


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
) -> str:
    """A token shaped exactly like the backend's mint_scoped_token. Signed for
    real: the addon verifies the HMAC, so a hand-written string would only ever
    exercise the reject path."""
    raw = json.dumps({"p": project, "t": "t1", "exp": time.time() + ttl_s})
    body = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(digest).decode().rstrip('=')}"


def _basic(password: str) -> str:
    return "Basic " + base64.b64encode(f"cheese:{password}".encode()).decode()


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
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret="test-secret")
    token = _scoped_token("test-secret")
    mod.http_connect(_make_connect_flow(_basic(token)))
    for path in (
        "/api/oauth/profile",
        "/api/claude_code/settings",
        "/api/claude_code/policy_limits",
    ):
        flow = _make_flow(path=path)
        # 归账那一侧允许 header 在已证明的项目内部挑房间；身份这一侧不允许 ——
        # 答出去的是这条会话被签在哪儿。
        flow.request.headers["x-cheese-attr"] = "p1/some-other-topic"
        asyncio.run(mod.requestheaders(flow))
        assert flow.response.status_code == (204 if path.endswith("settings") else 200)
        assert flow.request.stream is False
        # 请求没有出过这一跳。
        assert flow.server_conn.via is None
        if path.endswith("profile"):
            data = json.loads(flow.response.content)
            assert data["organization"]["uuid"] == "p1"
            assert data["account"]["uuid"] == "t1"
        elif path.endswith("settings"):
            assert flow.response.content == b""
        else:
            assert json.loads(flow.response.content) == {"restrictions": {}}


def _assert_answered_by_cheese(mod, flow) -> None:
    """这一跳没出去，回的是这条活自己的地点。"""
    asyncio.run(mod.requestheaders(flow))
    assert flow.response is not None
    assert flow.response.status_code == 200
    assert flow.request.stream is False
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
    那次。
    """
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret="test-secret")
    mod.http_connect(_make_connect_flow(_basic(_scoped_token("test-secret"))))
    _assert_answered_by_cheese(mod, _make_flow(path="/api/oauth/profile"))


def test_an_unreachable_admission_does_not_echo_the_platform_account(
    monkeypatch, tmp_path
):
    """后端不可达时准入 fail-open 放行，而开机不等它。

    放行的是花钱，不是身份：一个连不上的控制面什么也说不出，而「谁代表一个
    Anthropic 账号回话」这一问，答案里本来就不该有它。
    """

    def unreachable(url, bearer, timeout_s):
        raise OSError("control plane is down")

    mod = _load_addon(monkeypatch, tmp_path, scoped_secret="test-secret")
    url = "http://control-plane.invalid/admission"
    monkeypatch.setattr(mod, "ADMISSION_URL", url)
    monkeypatch.setattr(mod, "ADMISSION", mod.AdmissionGate(url, post=unreachable))
    token = _scoped_token("test-secret")
    _assert_answered_by_cheese(
        mod, _make_flow(path="/api/oauth/profile", caller_bearer=token)
    )


def test_telemetry_is_consumed_without_attaching_provider_credential(
    monkeypatch, tmp_path
):
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret="test-secret")
    token = _scoped_token("test-secret")
    mod.http_connect(_make_connect_flow(_basic(token)))
    flow = _make_flow(path="/api/event_logging/v2/batch", caller_bearer="")
    asyncio.run(mod.requestheaders(flow))
    assert flow.response.status_code == 200
    assert flow.request.stream is False


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
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret="")
    flow = _make_connect_flow(_basic("anything"))

    mod.http_connect(flow)

    assert flow.response is not None and flow.response.status_code == 407


def test_connect_with_a_valid_scoped_token_is_relayed(monkeypatch, tmp_path):
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    flow = _make_connect_flow(_basic(_scoped_token(secret)))

    mod.http_connect(flow)

    assert flow.response is None, "a caller that proved its project must get a tunnel"


def test_connect_with_a_foreign_or_missing_token_is_refused(monkeypatch, tmp_path):
    """Signed by someone else, expired, or absent entirely — all the same 407."""
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
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
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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


# --- attribution: what the connection proved at CONNECT ---------------------
# A session's Bearer is its host's own Claude credential, not a scoped cheese
# token, so the project cannot be read from it. The scoped token rides on the
# CONNECT instead, as the proxy password, and that is where attribution — and
# the token that authenticates the admission call — come from.


def _session_flow(*, bearer=SESSION_CREDENTIAL, attr=None, conn="client-1"):
    """A turn as a session on the central host makes it: the Bearer is its own
    Claude credential (not a scoped token, not verifiable here), on a connection
    that proved its project at CONNECT time."""
    flow = _make_flow(caller_bearer=bearer)
    flow.client_conn.id = conn
    if attr is not None:
        flow.request.headers["x-cheese-attr"] = attr
    return flow


def _connect_flow_on(conn: str, proxy_auth: str | None):
    return _make_connect_flow(proxy_auth, conn=conn)


def _recording_admission(mod, monkeypatch, verdict=None):
    """A control plane that answers `verdict` and records who asked it:
    (project, topic, the bearer the admission call authenticates with)."""
    asked: list[tuple[str, str, str]] = []

    def check(project, topic, bearer, **_):
        asked.append((project, topic, bearer))
        return verdict or _verdict(pool="subscription")

    monkeypatch.setattr(mod, "ADMISSION_URL", "http://control-plane.invalid/admission")
    monkeypatch.setattr(mod, "ADMISSION", SimpleNamespace(check=check))
    return asked


def _turn_on_proven_connection(monkeypatch, tmp_path, *, attr=None):
    """A session whose connection proved project p9 / topic t1 at CONNECT."""
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    token = _scoped_token(secret, project="p9")
    mod.http_connect(_connect_flow_on("c1", _basic(token)))
    asked = _recording_admission(mod, monkeypatch)
    flow = _session_flow(conn="c1", attr=attr)
    asyncio.run(mod.requestheaders(flow))
    return mod, flow, asked, token


def test_a_sessions_turn_is_attributed_from_what_it_proved_at_connect(
    monkeypatch, tmp_path
):
    """The Bearer is NOT a scoped token, so the project has to come from the
    CONNECT that opened this connection."""
    _, flow, asked, _ = _turn_on_proven_connection(monkeypatch, tmp_path)

    assert flow.response is None
    assert [(project, topic) for project, topic, _ in asked] == [("p9", "t1")]


def test_the_admission_call_uses_the_token_proven_at_connect(monkeypatch, tmp_path):
    """Admission authenticates with a scoped cheese token. Handing it the
    session's Claude credential instead would 401 — and the proxy fails OPEN on
    admission errors, so the budget brake would quietly stop braking. It would
    also put the session's credential in the control plane's reach."""
    _, _, asked, token = _turn_on_proven_connection(monkeypatch, tmp_path)

    assert [bearer for _, _, bearer in asked] == [token]
    assert SESSION_CREDENTIAL not in json.dumps(asked)


def test_a_deferred_subagent_admission_uses_the_token_proven_at_connect(
    monkeypatch, tmp_path
):
    """The subagent path asks admission later, from the `request` hook, once the
    body is whole. It must carry the same proven token, not the Bearer."""
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    token = _scoped_token(secret, project="p9")
    mod.http_connect(_connect_flow_on("c1", _basic(token)))
    asked = _recording_admission(mod, monkeypatch)
    flow = _session_flow(conn="c1")
    flow.request.headers.update(
        {"x-claude-code-request-class": "subagent", "content-length": "34"}
    )

    asyncio.run(mod.requestheaders(flow))
    assert asked == [], "the subagent's admission waits for its body"
    flow.request.content = b'{"model":"glm-4.6","messages":[]}'
    asyncio.run(mod.request(flow))

    assert asked == [("p9", "t1", token)]


def test_a_connection_that_proved_nothing_is_still_unattributable(
    monkeypatch, tmp_path
):
    """The security property must not regress: reading the CONNECT is a new
    SOURCE of verified claims, not a new way to skip proving one. A turn on a
    connection that proved nothing is refused and never reaches admission."""
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    asked = _recording_admission(mod, monkeypatch)
    flow = _session_flow(conn="never-connected")

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is not None and flow.response.status_code == 401
    assert asked == []


def test_the_session_header_refines_the_topic_inside_the_proven_project(
    monkeypatch, tmp_path
):
    """One machine hosts several topics of a project but shares ONE tunnel
    helper, so the CONNECT token names whichever session launched last. The
    per-request header is the only per-topic signal, and it is safe to honour
    for the topic alone: the project it is checked against was proven."""
    _, _, asked, _ = _turn_on_proven_connection(
        monkeypatch, tmp_path, attr="p9/other-topic"
    )

    assert [(project, topic) for project, topic, _ in asked] == [("p9", "other-topic")]


def test_the_session_header_cannot_move_billing_to_another_project(
    monkeypatch, tmp_path
):
    """The header is unverified. It may pick a topic, never a payer."""
    _, _, asked, _ = _turn_on_proven_connection(
        monkeypatch, tmp_path, attr="someone-elses-project/their-topic"
    )

    assert [(project, topic) for project, topic, _ in asked] == [("p9", "t1")], (
        "a foreign project is ignored entirely"
    )


def test_a_closed_connection_stops_pinning_a_proven_project(monkeypatch, tmp_path):
    """A long-lived proxy must not accumulate one entry per connection ever
    made, and a recycled connection id must not inherit the previous caller's
    project."""
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    asked = _recording_admission(mod, monkeypatch)

    mod.client_disconnected(SimpleNamespace(id="c1"))
    flow = _session_flow(conn="c1")
    asyncio.run(mod.requestheaders(flow))

    assert flow.response is not None and flow.response.status_code == 401
    assert asked == []


def test_an_exhausted_budget_says_budget(monkeypatch, tmp_path):
    """A refused project must be told which balance ran out, not something that
    sends whoever reads it to the wrong file. Nothing is forwarded, and the
    session's credential is left where it was."""
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    _with_admission(
        mod, monkeypatch, allow=False, reason="budget spent: 10.0000 of 10.0000"
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _session_flow(conn="c1")

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is not None and flow.response.status_code == 429
    assert b"budget" in flow.response.content
    assert flow.request.headers["authorization"] == f"Bearer {SESSION_CREDENTIAL}"


# --- where the session's credential goes -------------------------------------
# The proxy holds no model credential. A subscription turn carries the session's
# own Claude login to Anthropic as it came; a turn admission places on the
# API-key pool goes to LiteLLM on the project's virtual key, and the session's
# credential must not ride along — it would sit in another service's reach and
# logs.


def _admitted_as(mod, monkeypatch, admission: str) -> None:
    """Three ways a box ends up serving a turn from the subscription."""
    if admission == "no admission configured":
        return
    if admission == "admission says subscription":
        _with_admission(mod, monkeypatch, pool="subscription")
        return

    def unreachable(url, bearer, timeout_s, **_):
        raise OSError("control plane is down")

    url = "http://control-plane.invalid/admission"
    monkeypatch.setattr(mod, "ADMISSION_URL", url)
    monkeypatch.setattr(mod, "ADMISSION", mod.AdmissionGate(url, post=unreachable))


@pytest.mark.parametrize(
    "admission",
    [
        "admission says subscription",
        "no admission configured",
        "admission unreachable (fail-open)",
    ],
)
def test_a_subscription_turn_forwards_the_sessions_own_credential_untouched(
    monkeypatch, tmp_path, admission
):
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    _admitted_as(mod, monkeypatch, admission)
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _session_flow(conn="c1")
    # A stale x-api-key would override the session's bearer at Anthropic.
    flow.request.headers["x-api-key"] = "stale-key"

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is None, "a served turn must not be refused"
    assert flow.request.headers["authorization"] == f"Bearer {SESSION_CREDENTIAL}"
    assert "x-api-key" not in flow.request.headers
    assert flow.request.host == "api.anthropic.com"
    # Straight to Anthropic: no upstream proxy hop on the server connection.
    assert flow.server_conn.via is None


def _gateway_turn(monkeypatch, tmp_path, *, deferred: bool):
    """A turn admission places on the API-key pool, carrying the session's real
    credential in both headers a client can put one in."""
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    monkeypatch.setattr(mod, "GATEWAY_BASE", "http://litellm.invalid:4000")
    asked = _recording_admission(
        mod,
        monkeypatch,
        _verdict(pool="gateway", key="sk-virtual-project-key", model="glm-4.6"),
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _session_flow(conn="c1")
    flow.request.headers["x-api-key"] = SESSION_CREDENTIAL
    if deferred:
        flow.request.headers.update(
            {"x-claude-code-request-class": "subagent", "content-length": "34"}
        )
    asyncio.run(mod.requestheaders(flow))
    if deferred:
        flow.request.content = b'{"model":"glm-4.6","messages":[]}'
        asyncio.run(mod.request(flow))
    return flow, asked


@pytest.mark.parametrize(
    "deferred", [False, True], ids=["header-time turn", "deferred subagent turn"]
)
def test_a_gateway_turn_never_carries_the_sessions_credential(
    monkeypatch, tmp_path, deferred
):
    """THE property of routing a turn to LiteLLM: the session's own Claude
    credential appears in no header of what leaves for the gateway — not in
    `authorization`, not in `x-api-key`, not anywhere — and the gateway is
    authenticated with the project's virtual key instead."""
    flow, asked = _gateway_turn(monkeypatch, tmp_path, deferred=deferred)

    assert asked, "admission was never asked, so nothing was routed"
    assert flow.response is None, "a routed turn must not be refused"
    assert flow.request.host == "litellm.invalid"
    assert flow.request.port == 4000
    assert flow.request.headers["authorization"] == "Bearer sk-virtual-project-key"
    leaked = {
        name: value
        for name, value in flow.request.headers.items()
        if "REAL-SESSION-CREDENTIAL" in value
    }
    assert leaked == {}, f"the session's credential reached the gateway: {leaked}"


def _no_login_turn(
    monkeypatch, tmp_path, pool: str, *, answered=True, path="/v1/messages"
):
    """A placeholder session's request; `answered` says whether admission
    actually answered (False = the gate failed open)."""
    secret = "s3cr3t"
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    monkeypatch.setattr(mod, "GATEWAY_BASE", "http://litellm.invalid:4000")
    _with_admission(
        mod,
        monkeypatch,
        pool=pool,
        key="sk-virtual-project-key",
        fail_open=not answered,
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _session_flow(bearer=core.NO_LOGIN_PLACEHOLDER, conn="c1")
    flow.request.path = path
    asyncio.run(mod.requestheaders(flow))
    return flow


def test_a_host_without_a_claude_login_cannot_send_a_subscription_turn(
    monkeypatch, tmp_path
):
    """The placeholder a login-less host boots on authenticates nothing; sending
    it to Anthropic would come back as an opaque 401 that reads like a broken
    account. The caller is told the host has no login instead — as a refusal
    the client does not retry: a 5xx here had Claude Code retry silently for
    about three minutes before the turn said anything."""
    flow = _no_login_turn(monkeypatch, tmp_path, "subscription")

    assert flow.response is not None and flow.response.status_code == 400
    assert b"no Claude login" in flow.response.content


def test_a_login_less_turn_the_control_plane_could_not_place_waits_for_it(
    monkeypatch, tmp_path
):
    """Admission unreachable: only the gateway could serve a login-less turn,
    and its key comes from admission, so the turn is told to retry."""
    flow = _no_login_turn(monkeypatch, tmp_path, "subscription", answered=False)

    assert flow.response is not None and flow.response.status_code == 503
    assert b"retry" in flow.response.content


@pytest.mark.parametrize(
    "path",
    [
        "/api/claude_cli/bootstrap?entrypoint=sdk-cli&model=claude-sonnet-5",
        "/api/claude_code_penguin_mode",
        "/api/oauth/validate",
    ],
)
def test_a_login_less_session_boots_without_refusals(monkeypatch, tmp_path, path):
    """The boot calls only a real account can answer are answered here for a
    placeholder session, never sent to Anthropic on a credential that
    authenticates nothing."""
    flow = _no_login_turn(monkeypatch, tmp_path, "gateway", path=path)

    assert flow.response is not None and flow.response.status_code == 200
    assert json.loads(flow.response.content) in ({}, {"enabled": False})
    assert flow.request.host == "api.anthropic.com"


def _platform_turn(
    monkeypatch,
    tmp_path,
    *,
    credential,
    pool="subscription",
    path="/v1/messages",
):
    """A placeholder session's request while the platform holds `credential`."""
    secret = "s3cr3t"
    mod = _load_addon(
        monkeypatch, tmp_path, scoped_secret=secret, credential=credential
    )
    monkeypatch.setattr(mod, "GATEWAY_BASE", "http://litellm.invalid:4000")
    _with_admission(
        mod, monkeypatch, pool=pool, key="sk-virtual-project-key", fail_open=False
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    flow = _session_flow(bearer=core.NO_LOGIN_PLACEHOLDER, conn="c1")
    flow.request.path = path
    return mod, flow


def _pair(expires_in_s: float, refresh="sk-ant-ort01-PLATFORM") -> str:
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "sk-ant-oat01-PLATFORM-OLD",
                "refreshToken": refresh,
                "expiresAt": int((time.time() + expires_in_s) * 1000),
            }
        }
    )


def test_a_subscription_turn_goes_out_on_the_platforms_credential(
    monkeypatch, tmp_path
):
    mod, flow = _platform_turn(
        monkeypatch, tmp_path, credential="sk-ant-oat01-PLATFORM-SETUP\n"
    )

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is None
    assert flow.request.host == "api.anthropic.com"
    assert flow.request.headers["authorization"] == (
        "Bearer sk-ant-oat01-PLATFORM-SETUP"
    )


def test_a_logged_in_platform_asks_anthropic_for_the_sessions_boot_data(
    monkeypatch, tmp_path
):
    mod, flow = _platform_turn(
        monkeypatch,
        tmp_path,
        credential="sk-ant-oat01-PLATFORM-SETUP",
        path="/api/claude_cli/bootstrap",
    )

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is None, "a real login's boot data comes from Anthropic"
    assert flow.request.headers["authorization"] == (
        "Bearer sk-ant-oat01-PLATFORM-SETUP"
    )


def test_a_login_about_to_expire_is_renewed_before_it_goes_out(monkeypatch, tmp_path):
    mod, flow = _platform_turn(monkeypatch, tmp_path, credential=_pair(60))
    refreshed = []

    def grant(url, body, timeout):
        refreshed.append(body["refresh_token"])
        return 200, {"access_token": "sk-ant-oat01-PLATFORM-NEW", "expires_in": 28800}

    mod.CREDENTIAL._post = grant

    asyncio.run(mod.requestheaders(flow))

    assert refreshed == ["sk-ant-ort01-PLATFORM"]
    assert flow.request.headers["authorization"] == "Bearer sk-ant-oat01-PLATFORM-NEW"


def test_logging_out_takes_effect_at_the_next_request(monkeypatch, tmp_path):
    """No session holds the credential, so removing the file is logging every
    running session out at once."""
    mod, first = _platform_turn(
        monkeypatch, tmp_path, credential="sk-ant-oat01-PLATFORM-SETUP"
    )
    asyncio.run(mod.requestheaders(first))
    assert first.response is None

    mod.CREDENTIAL.path.unlink()
    second = _session_flow(bearer=core.NO_LOGIN_PLACEHOLDER, conn="c1")
    asyncio.run(mod.requestheaders(second))

    assert second.response is not None and second.response.status_code == 400
    assert b"no Claude login" in second.response.content


def test_a_refused_login_is_reported_as_one_to_renew_and_not_retried(
    monkeypatch, tmp_path
):
    mod, flow = _platform_turn(monkeypatch, tmp_path, credential=_pair(60))
    mod.CREDENTIAL._post = lambda url, body, timeout: (400, {"error": "invalid_grant"})

    asyncio.run(mod.requestheaders(flow))

    assert flow.response is not None and flow.response.status_code == 400
    assert b"has to be renewed" in flow.response.content


def test_the_credentials_requests_leave_through_its_egress(monkeypatch, tmp_path):
    mod, flow = _platform_turn(
        monkeypatch, tmp_path, credential="sk-ant-oat01-PLATFORM-SETUP"
    )
    mod.CREDENTIAL.egress_path.write_text("http://me:pw@egress.example:3128\n")

    asyncio.run(mod.requestheaders(flow))
    connect = SimpleNamespace(
        request=SimpleNamespace(headers={}), client_conn=flow.client_conn
    )
    mod.http_connect_upstream(connect)

    assert flow.response is None
    assert flow.server_conn.via == ("http", ("egress.example", 3128))
    assert connect.request.headers["Proxy-Authorization"] == (
        "Basic " + base64.b64encode(b"me:pw").decode()
    )


def test_an_egress_that_refused_the_proxy_is_reported_until_it_changes(
    monkeypatch, tmp_path
):
    """A 502 would be retried silently for minutes; a wrong proxy password
    does not heal by waiting."""
    mod, first = _platform_turn(
        monkeypatch, tmp_path, credential="sk-ant-oat01-PLATFORM-SETUP"
    )
    mod.CREDENTIAL.egress_path.write_text("http://me:wrong@egress.example:3128\n")
    asyncio.run(mod.requestheaders(first))
    first.error = (
        "Upstream proxy egress.example:3128 refused HTTP CONNECT request: "
        "407 Proxy Authentication Required"
    )
    mod.error(first)

    second = _session_flow(bearer=core.NO_LOGIN_PLACEHOLDER, conn="c1")
    asyncio.run(mod.requestheaders(second))
    assert second.response is not None and second.response.status_code == 400
    assert b"egress.example:3128 refused" in second.response.content
    # A refusal that still streams the body is never delivered: mitmproxy
    # drops the connection and the client waits out its timeout.
    assert second.request.stream is False

    mod.CREDENTIAL.egress_path.write_text("http://me:right@egress.example:3128\n")
    third = _session_flow(bearer=core.NO_LOGIN_PLACEHOLDER, conn="c1")
    asyncio.run(mod.requestheaders(third))
    assert third.response is None
    assert third.server_conn.via == ("http", ("egress.example", 3128))


def test_only_the_credentials_requests_take_the_egress(monkeypatch, tmp_path):
    """A turn admission sends to the gateway carries no platform credential,
    and keeps its own route."""
    mod, flow = _platform_turn(
        monkeypatch,
        tmp_path,
        credential="sk-ant-oat01-PLATFORM-SETUP",
        pool="gateway",
    )
    mod.CREDENTIAL.egress_path.write_text("http://egress.example:3128\n")

    asyncio.run(mod.requestheaders(flow))

    assert flow.request.host == "litellm.invalid"
    assert flow.server_conn.via is None


def test_without_an_egress_the_credentials_requests_go_direct(monkeypatch, tmp_path):
    mod, flow = _platform_turn(
        monkeypatch, tmp_path, credential="sk-ant-oat01-PLATFORM-SETUP"
    )

    asyncio.run(mod.requestheaders(flow))

    assert flow.server_conn.via is None


def test_the_platforms_credential_never_reaches_the_gateway(monkeypatch, tmp_path):
    mod, flow = _platform_turn(
        monkeypatch,
        tmp_path,
        credential="sk-ant-oat01-PLATFORM-SETUP",
        pool="gateway",
    )

    asyncio.run(mod.requestheaders(flow))

    assert flow.request.host == "litellm.invalid"
    assert flow.request.headers["authorization"] == "Bearer sk-virtual-project-key"
    assert not [v for v in flow.request.headers.values() if "PLATFORM" in v]


def test_a_host_without_a_claude_login_still_serves_the_gateway(monkeypatch, tmp_path):
    flow = _no_login_turn(monkeypatch, tmp_path, "gateway")

    assert flow.response is None
    assert flow.request.host == "litellm.invalid"
    assert flow.request.headers["authorization"] == "Bearer sk-virtual-project-key"


def _make_response(status_code=200, content_type="text/event-stream"):
    from mitmproxy import http

    resp = http.Response.make(status_code=status_code, content=b"", headers={})
    resp.headers = {"content-type": content_type}
    return resp


def test_a_streamed_message_is_metered_through_the_response_tee(monkeypatch, tmp_path):
    """The response body is no longer buffered whole (that is what OOM-kills the
    proxy on long turns); it streams through a tee that scrapes usage from the
    SSE incrementally. Driving that tee must still land the turn on the meter."""
    mod = _load_addon(monkeypatch, tmp_path)

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
    mod = _load_addon(monkeypatch, tmp_path)

    flow = _make_flow(path="/api/oauth/profile")
    flow.response = _make_response(content_type="application/json")

    mod.responseheaders(flow)

    assert flow.response.stream is True
    assert mod.METER.used() == 0


def test_gateway_responses_do_not_charge_the_subscription(monkeypatch, tmp_path):
    mod = _load_addon(monkeypatch, tmp_path)
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
    mod = _load_addon(monkeypatch, tmp_path)
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
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret="test-secret")
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
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret="test-secret")
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
    mod = _load_addon(monkeypatch, tmp_path)
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
    mod = _load_addon(monkeypatch, tmp_path)
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


def test_the_boot_endpoints_are_answered_here_for_a_caller_that_proves_nothing(
    monkeypatch, tmp_path
):
    """一台机器只有一种启动环境（结论 46），所以开机不能取决于这个会话证明了什么。

    The caller below proves no place at all — no scoped secret, the
    weakest caller the proxy ever serves. Every startup path still has to be
    answered from the table: the alternative is Anthropic answering "who am I"
    for a sandbox running someone else's code, with the platform subscription's
    own uuid and email in the reply.
    """
    mod = _load_addon(monkeypatch, tmp_path)

    for path in _BOOT_PATHS:
        flow = _make_flow(path=path)
        asyncio.run(mod.requestheaders(flow))
        assert flow.response is not None, path
        assert flow.response.status_code in (200, 204), path
        # Nothing went out: answered here, with no upstream hop.
        assert flow.server_conn.via is None, path
        assert flow.request.stream is False, path


def test_the_identity_answered_here_is_cheeses_own(monkeypatch, tmp_path):
    mod = _load_addon(monkeypatch, tmp_path)
    flow = _make_flow(path="/api/oauth/profile")
    asyncio.run(mod.requestheaders(flow))
    body = json.loads(flow.response.content)
    assert "anthropic.com" not in body["account"]["email"]


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

    # A session on a proven connection, refused by its project's balance.
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    _with_admission(
        mod, monkeypatch, allow=False, reason="budget spent: 10.0000 of 10.0000"
    )
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    yield "an exhausted project budget", mod, _session_flow(conn="c1")

    # The same session, whose card is bound to a model the project cannot serve.
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    _with_admission(
        mod,
        monkeypatch,
        allow=False,
        reason="model not in this project's catalogue",
        reason_kind=core.BINDING,
    )
    mod.http_connect(
        _connect_flow_on("c2", _basic(_scoped_token(secret, project="p9")))
    )
    yield "a binding the project cannot serve", mod, _session_flow(conn="c2")

    # A caller that cannot prove which project to bill.
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    yield "an unattributable caller", mod, _make_flow(caller_bearer="not-a-token")

    # A gateway project on a deployment with no pool to send it to.
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    _with_admission(mod, monkeypatch, pool="gateway", key=None)
    yield (
        "a gateway project with no route",
        mod,
        _make_flow(caller_bearer=_scoped_token(secret)),
    )


def test_every_refusal_of_a_message_turn_reaches_the_caller(monkeypatch, tmp_path):
    """The refusal is the product here: each of these exists to tell somebody
    exactly what went wrong (which budget, which binding, which piece of the box
    is unconfigured). A refusal that kills the connection instead
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

    # SUBSCRIPTION: the session's own credential goes to Anthropic as it came.
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    _with_admission(mod, monkeypatch, pool="subscription")
    mod.http_connect(
        _connect_flow_on("c1", _basic(_scoped_token(secret, project="p9")))
    )
    yield "the subscription path", mod, _session_flow(conn="c1")

    # GATEWAY: the request is re-aimed at the API-key pool and forwarded there.
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    _with_admission(mod, monkeypatch, pool="gateway", key="sk-virtual-key")
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
    mod = _load_addon(monkeypatch, tmp_path, scoped_secret=secret)
    _with_admission(mod, monkeypatch, model="claude-opus-5")
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
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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


def test_a_subagent_hint_header_echoing_the_parent_also_reads_as_inherit(
    monkeypatch, tmp_path
):
    """hint 头（x-cheese-child-model）回显父模型 = 继承，不是显式指定。

    CC 2.1.277 的 GATEWAY_HINT_HEADERS 对每个分身都打上它解析出的分身模
    型：没指定时就是父会话（被改写前的）模型。快路若把它当显式送准入，
    gateway 项目的普通分身全灭 —— 2026-09-23 事故换了个头卷土重来（当日
    实测：review 分身被线上闸门 400 打死）。判据与推迟路的体回显一致：
    == 主对话改写前的原模型即未指定。"""
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
    mod.ADMISSION_URL = "http://fixture/admission"
    calls = []

    def admit(
        project, topic, bearer, *, subagent=False, requested_model="", child_model=""
    ):
        calls.append((subagent, requested_model, child_model))
        return _verdict(model="kimi-k3")

    mod.ADMISSION = SimpleNamespace(check=admit)

    # 主对话：体里写着 CC 的内建默认，被改写成绑定模型 —— 改写前的值被记住。
    main = _make_flow()
    main.request.headers["x-cheese-attr"] = "p/t"
    asyncio.run(mod.requestheaders(main))
    out = main.request.stream(b'{"model":"claude-sonnet-5","messages":[]}')
    assert json.loads(out)["model"] == "kimi-k3"
    assert mod.PARENT_MODEL.get(("p", "t")) == "claude-sonnet-5"

    # 分身：hint 头回显同一个名字 —— 不当显式送准入；照未指定的老路推迟，
    # 体回显在 request 钩子里同样被认掉，准入按「未指定」问。
    fork = _make_flow()
    fork.request.headers.update(
        {
            "x-cheese-attr": "p/t",
            "x-claude-code-request-class": "subagent",
            "x-cheese-child-model": "claude-sonnet-5",
            "content-length": "42",
        }
    )
    calls.clear()
    asyncio.run(mod.requestheaders(fork))
    assert calls == [], "回显不该在头部时刻就触发准入"
    assert "x-cheese-child-model" not in fork.request.headers
    fork.request.content = b'{"model":"claude-sonnet-5","messages":[]}'
    asyncio.run(mod.request(fork))
    assert calls[-1] == (True, "", "")
    assert json.loads(fork.request.content)["model"] == "kimi-k3"


def test_the_parents_haiku_background_requests_do_not_overwrite_the_record(
    monkeypatch, tmp_path
):
    """CLI 自己的 haiku 后台请求（会话标题、路径建议）也走主对话路径；在
    gateway 池上它们照样被改写（不留 haiku），replaced=True —— 但它们不是
    父会话的工作模型。记进 PARENT_MODEL 就会把真回显盖掉,普通分身再次全灭。"""
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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
    mod = _load_addon(monkeypatch, tmp_path, allow_header_attr="1")
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
