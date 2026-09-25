"""The metering proxy's pure logic (deploy/metering-proxy/cheese_billing_core.py),
loaded by path — the addon file itself needs mitmproxy, the logic must not.

Covers the proxy half of #198 (scoped-token verify → attribution the sandbox
cannot forge) and the #218 admission gate (fail-open, cached)."""

import base64
import hashlib
import hmac
import importlib.util
import io
import json
import time
from pathlib import Path
from unittest import mock

CORE = (
    Path(__file__).resolve().parents[3]
    / "deploy"
    / "metering-proxy"
    / "cheese_billing_core.py"
)
spec = importlib.util.spec_from_file_location("cheese_billing_core", CORE)
assert spec and spec.loader
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

SECRET = "test-sandbox-token"


def test_native_child_admission_does_not_reuse_its_parents_cached_supply():
    calls = []

    def admit(url, bearer, timeout, *, subagent=False, requested_model=""):
        calls.append(subagent)
        return core.Verdict(
            True,
            "",
            pool="gateway" if subagent else "subscription",
            model="child" if subagent else "main",
        )

    gate = core.AdmissionGate("http://fixture/admission", post=admit)
    assert gate.check("p", "t", "token").model == "main"
    child = gate.check("p", "t", "token", subagent=True)
    assert child.model == "child"
    assert child.pool == "gateway"
    assert gate.check("p", "t", "token").model == "main"
    assert gate.check("p", "t", "token", subagent=True).model == "child"
    assert calls == [False, True]


def test_two_child_model_choices_do_not_share_an_admission_cache_entry():
    calls = []

    def admit(url, bearer, timeout, *, subagent=False, child_model=""):
        calls.append(child_model)
        return core.Verdict(True, "", model=child_model)

    gate = core.AdmissionGate("http://fixture/admission", post=admit)
    for model in ("claude-opus-5", "claude-sonnet-5", "claude-opus-5"):
        assert (
            gate.check("p", "t", "token", subagent=True, child_model=model).model
            == model
        )
    assert calls == ["claude-opus-5", "claude-sonnet-5"]


def test_child_selection_is_sent_to_backend_admission():
    response = io.BytesIO(b'{"data":{"allow":true,"supply":{"model":"claude-opus-5"}}}')
    with mock.patch.object(
        core.urllib.request, "urlopen", return_value=response
    ) as opened:
        verdict = core._post_admission(
            "http://backend/llm/admission",
            "fixture-token",
            3.0,
            subagent=True,
            child_model="claude-opus-5",
        )
    request = opened.call_args.args[0]
    assert request.get_header("X-cheese-subagent") == "1"
    assert request.get_header("X-cheese-child-model") == "claude-opus-5"
    assert verdict.model == "claude-opus-5"


def _mint(claims: dict, secret: str = SECRET) -> str:
    """The backend's exact minting algorithm (sandbox_auth.mint_scoped_token):
    urlsafe-b64 JSON body, HMAC-SHA256 sig, both unpadded."""
    raw = json.dumps(claims, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"{body}.{sig}"


def test_valid_token_roundtrips_its_claims():
    token = _mint({"p": "proj-1", "t": "topic-1", "exp": int(time.time()) + 60})

    claims = core.verify_scoped_token(token, SECRET)

    assert claims and claims["p"] == "proj-1" and claims["t"] == "topic-1"


def test_wrong_secret_expired_and_garbage_are_all_rejected():
    good = {"p": "proj-1", "t": None, "exp": int(time.time()) + 60}
    assert core.verify_scoped_token(_mint(good), "other-secret") is None
    expired = _mint({**good, "exp": int(time.time()) - 1})
    assert core.verify_scoped_token(expired, SECRET) is None
    assert core.verify_scoped_token("not-a-token", SECRET) is None
    assert core.verify_scoped_token("", SECRET) is None
    # A resigned body with a different secret must not pass either.
    body = _mint(good).split(".", 1)[0]
    forged_sig = _mint(good, "other-secret").split(".", 1)[1]
    assert core.verify_scoped_token(f"{body}.{forged_sig}", SECRET) is None


def test_proxy_basic_password_extracts_the_connect_credential():
    """The CONNECT listener authenticates a device by the scoped token its
    HTTPS_PROXY URL carries as the password — Claude Code sends it as Basic
    (measured on 2.1.229). Everything malformed yields "" on attacker bytes."""
    header = "Basic " + base64.b64encode(b"cheese:scoped.tok").decode()
    assert core.proxy_basic_password(header) == "scoped.tok"
    # A password containing ':' survives (only the first colon splits).
    header = "Basic " + base64.b64encode(b"cheese:a:b").decode()
    assert core.proxy_basic_password(header) == "a:b"
    assert core.proxy_basic_password("") == ""
    assert core.proxy_basic_password("Bearer xyz") == ""
    assert core.proxy_basic_password("Basic not-base64!!") == ""
    assert (
        core.proxy_basic_password("Basic " + base64.b64encode(b"nocolon").decode())
        == ""
    )


def test_the_served_hosts_are_exactly_the_anthropic_names():
    """The proxy relays each session's own Claude credential, so the allowlist
    is what keeps an attacker-chosen SNI from receiving it."""
    assert core.ANTHROPIC_HOSTS == {
        "api.anthropic.com",
        "console.anthropic.com",
        "platform.claude.com",
    }


def test_sse_usage_merges_start_and_delta():
    """input tokens ride message_start, output rides message_delta — neither
    alone is the turn's cost."""
    body = b"\n".join(
        [
            b'data: {"type":"message_start","message":{"model":"claude-opus-5",'
            b'"usage":{"input_tokens":7,"cache_read_input_tokens":100}}}',
            b'data: {"type":"message_delta","usage":{"output_tokens":42}}',
            b"data: [DONE]",
        ]
    )

    usage, model = core.usage_from_sse(body)

    assert model == "claude-opus-5"
    assert usage["input_tokens"] == 7
    assert usage["cache_read_input_tokens"] == 100
    assert usage["output_tokens"] == 42


def test_streaming_extractor_matches_the_buffered_parse_across_chunk_splits():
    """The live proxy scrapes usage incrementally as chunks pass through, so
    feeding a body in arbitrary fragments must yield exactly what parsing the
    whole body would — including when a split lands mid-line."""
    body = b"\n".join(
        [
            b'data: {"type":"message_start","message":{"model":"claude-opus-5",'
            b'"usage":{"input_tokens":7,"cache_read_input_tokens":100,'
            b'"cache_creation_input_tokens":3,"output_tokens":1}}}',
            b'data: {"type":"content_block_delta"}',
            b'data: {"type":"message_delta","usage":{"output_tokens":42}}',
            b"data: [DONE]",
        ]
    )
    want_usage, want_model = core.usage_from_sse(body)

    for step in (1, 7, 13, 500):  # byte-at-a-time up to whole-body
        ex = core.StreamingUsageExtractor()
        for i in range(0, len(body), step):
            ex.feed(body[i : i + step])
        ex.close()
        assert (ex.usage, ex.model) == (want_usage, want_model), f"step={step}"
    assert want_usage["output_tokens"] == 42 and want_usage["input_tokens"] == 7


def test_streaming_extractor_stays_bounded_on_newlineless_input():
    """A pathological stream with no line breaks must not grow the buffer without
    limit — the O(one line) memory bound is what keeps this proxy from OOMing."""
    ex = core.StreamingUsageExtractor()
    for _ in range(64):
        ex.feed(b"x" * (1 << 20))  # 64 MiB total, no newline ever
    assert len(ex._buf) <= (1 << 20)


def test_meter_records_and_caps_over_the_window(tmp_path):
    meter = core.Meter(tmp_path / "usage.jsonl", cap_window_s=3600)
    meter.record("p1", "t1", {"input_tokens": 60, "output_tokens": 40}, "m")

    assert meter.used() == 100
    assert not meter.would_exceed(101)
    assert meter.would_exceed(100)
    assert meter.would_exceed(99)

    # The trail is the ingestible JSONL the backend reads.
    rec = json.loads((tmp_path / "usage.jsonl").read_text().strip())
    assert rec["project_id"] == "p1" and rec["total_tokens"] == 100

    # A restarted meter restores the window from the trail (crash ≠ reset cap).
    again = core.Meter(tmp_path / "usage.jsonl", cap_window_s=3600)
    assert again.used() == 100


def test_admission_verdict_carries_the_supply_decision():
    """The same answer says both 'may it run' and 'where does it go' (#243)."""

    def gateway_post(url, bearer, timeout_s):
        return core.Verdict(True, "ok", pool=core.GATEWAY, key="sk-virt-1")

    gate = core.AdmissionGate("http://backend/llm/admission", post=gateway_post)
    v = gate.check("p1", "t1", "tok")
    assert v.allow and v.pool == core.GATEWAY and v.key == "sk-virt-1"


def test_unknown_or_absent_supply_falls_back_to_the_subscription():
    """A backend older than this proxy sends no `supply`; a typo sends a name
    we don't know. Both must keep the destination this proxy has always had —
    never guess a gateway it holds no key for."""

    def answer(payload: dict) -> core.Verdict:
        captured = json.dumps(payload).encode()

        class _Resp:
            def __enter__(self):
                return io.BytesIO(captured)

            def __exit__(self, *a):
                return False

        with mock.patch.object(core.urllib.request, "urlopen", return_value=_Resp()):
            return core._post_admission("http://backend/llm/admission", "tok", 3.0)

    assert answer({"data": {"allow": True, "reason": "r"}}).pool == core.SUBSCRIPTION
    assert (
        answer({"data": {"allow": True, "reason": "r", "supply": {"pool": "wat"}}}).pool
        == core.SUBSCRIPTION
    )
    ok = answer(
        {
            "data": {
                "allow": True,
                "reason": "r",
                "supply": {"pool": "gateway", "key": "sk-virt-9"},
            }
        }
    )
    assert ok.pool == core.GATEWAY and ok.key == "sk-virt-9"


def test_admission_gate_caches_and_fails_open():
    calls: list[str] = []

    def fake_post(url, bearer, timeout_s):
        calls.append(bearer)
        return core.Verdict(False, "budget spent: 5.0000 of 5.0000")

    gate = core.AdmissionGate("http://backend/llm/admission", post=fake_post)
    first = gate.check("p1", "t1", "tok")
    assert first.allow is False and "5.0000" in first.reason
    assert gate.check("p1", "t1", "tok").allow is False
    assert len(calls) == 1  # second answer came from the cache

    def broken_post(url, bearer, timeout_s):
        raise OSError("backend down")

    open_gate = core.AdmissionGate("http://backend/llm/admission", post=broken_post)
    v = open_gate.check("p2", "t1", "tok")
    assert v.allow is True and "fail-open" in v.reason
    # Fail-open has a direction: never guess a gateway we have no key for.
    assert v.pool == core.SUBSCRIPTION

    # Not configured → always allow, no calls.
    assert core.AdmissionGate("", post=fake_post).check("p3", "t1", "tok").allow is True


def test_relaunched_agent_does_not_reuse_its_previous_model_supply():
    def post(url, bearer, timeout_s):
        return core.Verdict(
            True,
            "ok",
            pool=core.GATEWAY if bearer == "new-session" else core.SUBSCRIPTION,
        )

    gate = core.AdmissionGate("http://backend/llm/admission", post=post)
    assert gate.check("project", "room", "old-session").pool == core.SUBSCRIPTION
    assert gate.check("project", "room", "new-session").pool == core.GATEWAY


def test_one_topics_bound_model_is_never_served_to_another():
    """Two topics of ONE project, asking with the same token — the shape of two
    sessions sharing one tunnel helper, whose CONNECT token names whichever
    launched last.

    The budget half of an admission answer is the project's, but the model it
    binds comes from the topic's card. A verdict cached per project would hand
    the second topic the first one's model for the rest of the window.
    """
    answers = iter(["claude-opus-5", "glm-4.6"])
    asked: list[str] = []

    def post(url, bearer, timeout_s):
        asked.append(bearer)
        return core.Verdict(True, "ok", model=next(answers))

    gate = core.AdmissionGate("http://backend/llm/admission", post=post)

    assert gate.check("p1", "t-alpha", "tok").model == "claude-opus-5"
    assert gate.check("p1", "t-beta", "tok").model == "glm-4.6"
    assert len(asked) == 2

    # Still cached — per topic, which is the point. Neither answer moved.
    assert gate.check("p1", "t-alpha", "tok").model == "claude-opus-5"
    assert gate.check("p1", "t-beta", "tok").model == "glm-4.6"
    assert len(asked) == 2


def test_the_verdict_cache_does_not_grow_for_every_topic_ever_served():
    """One entry per topic ever served would be a slow leak in a proxy that runs
    for weeks and has already been OOM-killed once. An entry past its window is
    no longer an answer to anything, so it goes."""
    gate = core.AdmissionGate(
        "http://backend/llm/admission",
        cache_s=0.01,
        post=lambda url, bearer, timeout_s: core.Verdict(True, "ok"),
    )
    for i in range(50):
        gate.check("p1", f"t{i}", "tok")
        time.sleep(0.001)

    time.sleep(0.05)
    gate.check("p1", "t-last", "tok")

    assert len(gate._cache) == 1, "expired verdicts were kept"


def test_a_requested_subagent_model_rides_the_admission_call():
    """主 agent 开分身指定的模型随 admission 带上去 —— 准入拿它决定绑它还是
    拒绝并列出可选。主对话不带：它的模型从来由绑定决定，请求体不是输入。"""
    seen = {}

    class _Resp:
        def __enter__(self):
            return io.BytesIO(b'{"data": {"allow": true, "reason": "r"}}')

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        seen.update({k.lower(): v for k, v in req.headers.items()})
        return _Resp()

    with mock.patch.object(core.urllib.request, "urlopen", side_effect=fake_urlopen):
        core._post_admission(
            "http://backend/llm/admission",
            "tok",
            3.0,
            subagent=True,
            requested_model="glm-4.6",
        )
    assert seen.get("x-cheese-subagent") == "1"
    assert seen.get("x-cheese-requested-model") == "glm-4.6"

    seen.clear()
    with mock.patch.object(core.urllib.request, "urlopen", side_effect=fake_urlopen):
        core._post_admission("http://backend/llm/admission", "tok", 3.0)
    assert "x-cheese-requested-model" not in seen


def test_two_requested_models_do_not_share_a_cached_verdict():
    """缓存键里有 requested model：一个房间里先后起 glm 分身和 kimi 分身，
    谁也不许拿到上一个的绑定。"""
    calls = []

    def admit(url, bearer, timeout, *, subagent=False, requested_model=""):
        calls.append(requested_model)
        return core.Verdict(True, "", model=requested_model)

    gate = core.AdmissionGate("http://fixture/admission", post=admit)
    first = gate.check("p", "t", "token", subagent=True, requested_model="glm-4.6")
    second = gate.check("p", "t", "token", subagent=True, requested_model="kimi-k3")
    again = gate.check("p", "t", "token", subagent=True, requested_model="glm-4.6")
    assert first.model == "glm-4.6"
    assert second.model == "kimi-k3"
    assert again.model == "glm-4.6"
    assert calls == ["glm-4.6", "kimi-k3"]


def test_requested_model_of_reads_the_top_level_member():
    """读的是 CC 写进请求体的指定，不是改写后要绑的那个；正文里出现的
    "model" 字样、缺失成员、非字符串成员都不算。"""
    body = (
        b'{"model":"glm-4.6","messages":[{"role":"user",'
        b'"content":"call it \\"model\\" if you like"}]}'
    )
    assert core.requested_model_of(body) == "glm-4.6"
    assert core.requested_model_of(b'{"messages":[]}') == ""
    assert core.requested_model_of(b"") == ""
    assert core.requested_model_of(b'{"model":123}') == ""


def test_requested_model_of_refuses_names_that_would_break_the_admission_header():
    """体里的原文要进准入门:控制字符/非 Latin-1 会让 putheader 抛错,被当成
    传输故障 fail-open。不合格的按未指定处理 —— 准入照常绑定,改写盖回去。"""
    nasty = b'{"model":"glm-4.6\x0aevil: 1","messages":[]}'
    assert core.requested_model_of(nasty) == ""
    assert core.requested_model_of('{"model":"模型","messages":[]}'.encode()) == ""
    assert core.requested_model_of(b'{"model":"openai/gpt-5","messages":[]}') == (
        "openai/gpt-5"
    )


# --- the platform's Claude credential ----------------------------------------

import threading as _threading  # noqa: E402


class _Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _pair_file(tmp_path, *, expires_in_s, now, **extra):
    path = tmp_path / "credential"
    oauth = {
        "accessToken": "sk-ant-oat01-OLD",
        "refreshToken": "sk-ant-ort01-OLD",
        "expiresAt": int((now + expires_in_s) * 1000),
        "refreshTokenExpiresAt": int((now + 20 * 86400) * 1000),
        "scopes": ["user:inference", "user:profile"],
        "subscriptionType": "max",
        **extra,
    }
    path.write_text(json.dumps({"claudeAiOauth": oauth, "mcpOAuth": {"kept": 1}}))
    return path


def _granting(calls, **answer):
    def post(url, body, timeout):
        calls.append((url, body))
        return 200, {
            "access_token": "sk-ant-oat01-NEW",
            "refresh_token": "sk-ant-ort01-NEW",
            "expires_in": 28800,
            "scope": "user:inference user:profile",
            **answer,
        }

    return post


def test_a_setup_token_is_used_as_it_is_and_never_refreshed(tmp_path):
    path = tmp_path / "credential"
    path.write_text("sk-ant-oat01-SETUP\n")
    calls = []
    cred = core.PlatformCredential(path, post=_granting(calls))

    assert cred.token() == ("sk-ant-oat01-SETUP", "")
    assert cred.refresh_due() is False
    cred.refresh_if_due()
    assert calls == []
    assert path.read_text() == "sk-ant-oat01-SETUP\n"


def test_a_fresh_pair_serves_its_access_token_without_asking_anyone(tmp_path):
    clock = _Clock(1_000_000.0)
    path = _pair_file(tmp_path, expires_in_s=3600, now=clock.t)
    calls = []
    cred = core.PlatformCredential(path, post=_granting(calls), now=clock)

    assert cred.token() == ("sk-ant-oat01-OLD", "")
    assert cred.refresh_due() is False
    cred.refresh_if_due()
    assert calls == []


def test_a_pair_near_expiry_is_refreshed_the_way_claude_code_does_it(tmp_path):
    clock = _Clock(1_000_000.0)
    path = _pair_file(tmp_path, expires_in_s=120, now=clock.t)
    calls = []
    cred = core.PlatformCredential(path, post=_granting(calls), now=clock)

    assert cred.refresh_due() is True
    cred.refresh_if_due()

    assert calls == [
        (
            "https://platform.claude.com/v1/oauth/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": "sk-ant-ort01-OLD",
                "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
                "scope": "user:profile user:inference user:sessions:claude_code "
                "user:mcp_servers user:file_upload user:plugins",
            },
        )
    ]
    assert cred.token() == ("sk-ant-oat01-NEW", "")
    written = json.loads(path.read_text())
    oauth = written["claudeAiOauth"]
    assert oauth["refreshToken"] == "sk-ant-ort01-NEW"
    assert oauth["expiresAt"] == int(clock.t * 1000) + 28800 * 1000
    # The server did not move the login deadline, so it stays where it was.
    assert oauth["refreshTokenExpiresAt"] == int((clock.t + 20 * 86400) * 1000)
    assert oauth["subscriptionType"] == "max"
    assert written["mcpOAuth"] == {"kept": 1}
    assert (path.stat().st_mode & 0o777) == 0o600
    assert cred.refresh_due() is False


def test_concurrent_requests_that_find_it_due_refresh_it_once(tmp_path):
    clock = _Clock(1_000_000.0)
    path = _pair_file(tmp_path, expires_in_s=60, now=clock.t)
    calls = []
    granted = _granting(calls)
    gate = _threading.Event()

    def slow_post(url, body, timeout):
        gate.wait(5)
        return granted(url, body, timeout)

    cred = core.PlatformCredential(path, post=slow_post, now=clock)
    workers = [_threading.Thread(target=cred.refresh_if_due) for _ in range(8)]
    for w in workers:
        w.start()
    gate.set()
    for w in workers:
        w.join(5)

    assert len(calls) == 1
    assert cred.token() == ("sk-ant-oat01-NEW", "")


def test_a_refused_refresh_token_asks_for_a_new_login_and_stops_trying(tmp_path):
    clock = _Clock(1_000_000.0)
    path = _pair_file(tmp_path, expires_in_s=60, now=clock.t)
    calls = []

    def refuse(url, body, timeout):
        calls.append(body)
        return 400, {"error": "invalid_grant"}

    cred = core.PlatformCredential(path, post=refuse, now=clock)
    cred.refresh_if_due()
    clock.t += 3600
    cred.refresh_if_due()

    assert len(calls) == 1
    assert cred.token() == ("", "login_required")


def test_a_new_login_replaces_a_refused_one_without_a_restart(tmp_path):
    clock = _Clock(1_000_000.0)
    path = _pair_file(tmp_path, expires_in_s=60, now=clock.t)
    cred = core.PlatformCredential(
        path,
        post=lambda url, body, timeout: (400, {"error": "invalid_grant"}),
        now=clock,
    )
    cred.refresh_if_due()
    assert cred.token() == ("", "login_required")

    _pair_file(tmp_path, expires_in_s=3600, now=clock.t, refreshToken="sk-ant-ort01-2")

    assert cred.token() == ("sk-ant-oat01-OLD", "")


def test_a_transient_failure_keeps_serving_the_valid_token_and_backs_off(tmp_path):
    clock = _Clock(1_000_000.0)
    path = _pair_file(tmp_path, expires_in_s=200, now=clock.t)
    calls = []

    def unreachable(url, body, timeout):
        calls.append(body)
        raise OSError("connection reset")

    cred = core.PlatformCredential(path, post=unreachable, now=clock)
    cred.refresh_if_due()
    clock.t += 10
    cred.refresh_if_due()

    assert len(calls) == 1
    assert cred.token() == ("sk-ant-oat01-OLD", "")

    clock.t += 60
    cred.refresh_if_due()
    assert len(calls) == 2


def test_an_access_token_past_expiry_is_not_used(tmp_path):
    clock = _Clock(1_000_000.0)
    path = _pair_file(tmp_path, expires_in_s=-1, now=clock.t)

    def unreachable(url, body, timeout):
        raise OSError("down")

    cred = core.PlatformCredential(path, post=unreachable, now=clock)
    cred.refresh_if_due()

    assert cred.token() == ("", "expired")


def test_no_file_or_an_unreadable_one_is_being_logged_out(tmp_path):
    assert core.PlatformCredential(tmp_path / "nope").token() == ("", "none")
    broken = tmp_path / "broken"
    broken.write_text("{not json")
    assert core.PlatformCredential(broken).token() == ("", "none")
