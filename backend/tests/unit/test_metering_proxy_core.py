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
    """The proxy injects the REAL credential per request, so the allowlist is
    what keeps an attacker-chosen SNI from receiving the subscription token."""
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
    v = gate.check("p1", "tok")
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


def test_a_half_upstream_identity_is_read_as_none(monkeypatch):
    """`user:password` is what the ccproxy hop authenticates with. Half of one
    authenticates as nobody, and the failure would surface as an upstream 407
    several hops from the control plane that sent it — so it is rejected at the
    parse, where the source is still obvious. None then means what it always
    means: fall back to the deployment-wide identity."""

    def answer(supply: dict) -> core.Verdict:
        captured = json.dumps({"data": {"allow": True, "supply": supply}}).encode()

        class _Resp:
            def __enter__(self):
                return io.BytesIO(captured)

            def __exit__(self, *a):
                return False

        with mock.patch.object(core.urllib.request, "urlopen", return_value=_Resp()):
            return core._post_admission("http://backend/llm/admission", "tok", 3.0)

    assert answer({"pool": "subscription", "upstream": "m516:pw"}).upstream == "m516:pw"
    for junk in ("m516:", ":pw", "m516", "", None, 5, ["m516:pw"]):
        assert answer({"pool": "subscription", "upstream": junk}).upstream is None
    assert answer({"pool": "subscription"}).upstream is None


def test_admission_gate_caches_and_fails_open():
    calls: list[str] = []

    def fake_post(url, bearer, timeout_s):
        calls.append(bearer)
        return core.Verdict(False, "budget spent: 5.0000 of 5.0000")

    gate = core.AdmissionGate("http://backend/llm/admission", post=fake_post)
    first = gate.check("p1", "tok")
    assert first.allow is False and "5.0000" in first.reason
    assert gate.check("p1", "tok").allow is False
    assert len(calls) == 1  # second answer came from the cache

    def broken_post(url, bearer, timeout_s):
        raise OSError("backend down")

    open_gate = core.AdmissionGate("http://backend/llm/admission", post=broken_post)
    v = open_gate.check("p2", "tok")
    assert v.allow is True and "fail-open" in v.reason
    # Fail-open has a direction: never guess a gateway we have no key for.
    assert v.pool == core.SUBSCRIPTION

    # Not configured → always allow, no calls.
    assert core.AdmissionGate("", post=fake_post).check("p3", "tok").allow is True
