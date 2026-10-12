"""The transcript sweep scrubs secrets exactly as the backend does.

`.claude/scripts/trace-sweep.py` runs with the standard library alone, so it
carries its own copy of `app.core.obs.scrub_secrets`. Its output is written into
a library document every member can read; a credential shape the backend learns
to hide must not stay visible there. When this fails, copy the change across.
"""

import importlib.util
from pathlib import Path

from app.core import obs

SCRIPT = Path(__file__).resolve().parents[3] / ".claude" / "scripts" / "trace-sweep.py"
PATTERNS = (
    "_OAUTH_CODE_RE",
    "_SECRET_KV_RE",
    "_URL_CRED_RE",
    "_AUTH_SCHEME_RE",
    "_SECRET_VALUE_RE",
)
SAMPLES = (
    "Authorization: Bearer abcdefghijklmnop",
    "Authorization: Basic dXNlcjpwdw==",
    '{"password": "hunter22", "status_code": 500}',
    "postgresql://app:pw123@db:5432/cheese",
    "slack xoxb-1234567890-abcdef and aws AKIAABCDEFGHIJKLMNOP",
    "/oauth/callback?code=abc123&state=1",
    "db_password='x' api_key=y anthropic_auth_token: z",
    "jwt eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl",
)


def _sweep():
    spec = importlib.util.spec_from_file_location("trace_sweep", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_sweep_uses_the_backends_patterns():
    sweep = _sweep()
    assert sweep._SECRET_KEY_TAILS == obs._SECRET_KEY_TAILS
    for name in PATTERNS:
        ours, theirs = getattr(sweep, name), getattr(obs, name)
        assert (ours.pattern, ours.flags) == (theirs.pattern, theirs.flags), name


def test_the_sweep_scrubs_what_the_backend_scrubs():
    sweep = _sweep()
    for sample in SAMPLES:
        assert sweep.scrub_secrets(sample) == obs.scrub_secrets(sample), sample
