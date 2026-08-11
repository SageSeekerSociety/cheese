"""The metering proxy's pure logic (deploy/metering-proxy/cheese_billing_core.py),
loaded by path — the addon file itself needs mitmproxy, the logic must not.

Covers the proxy half of #198 (scoped-token verify → attribution the sandbox
cannot forge) and the #218 admission gate (fail-open, cached)."""

import base64
import hashlib
import hmac
import importlib.util
import json
import time
from pathlib import Path

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


def test_admission_gate_caches_and_fails_open():
    calls: list[str] = []

    def fake_post(url, bearer, timeout_s):
        calls.append(bearer)
        return False, "budget spent: 5.0000 of 5.0000"

    gate = core.AdmissionGate("http://backend/llm/admission", post=fake_post)
    assert gate.check("p1", "tok") == (False, "budget spent: 5.0000 of 5.0000")
    assert gate.check("p1", "tok")[0] is False
    assert len(calls) == 1  # second answer came from the cache

    def broken_post(url, bearer, timeout_s):
        raise OSError("backend down")

    open_gate = core.AdmissionGate("http://backend/llm/admission", post=broken_post)
    allow, reason = open_gate.check("p2", "tok")
    assert allow is True and "fail-open" in reason

    # Not configured → always allow, no calls.
    assert core.AdmissionGate("", post=fake_post).check("p3", "tok")[0] is True
