"""Failed-job log signatures that identify a main push's red as infrastructure flake.

rerun-failed.py, when a `rerun-failed` dispatch names it, reruns a failed push to
main, because there is no pull request left to push a commit to. That is only
safe when the log says the failure came from somewhere outside the commit: a
connection the peer reset, a TLS handshake, a rate-limited API, a runner that
vanished, a tool download that timed out. Anything else is left for a person to read — a retry that cures
nothing costs the failed job set again and delays the real diagnosis.

`pattern` is a regular expression searched case-insensitively in one line of a
failed job's log (`GET /actions/jobs/{id}/logs`). rerun-failed.py decides which
lines count: a step that ran tests, and the text of a failure report, are not
evidence. Keep a signature specific to an infrastructure failure — one that
also matches an ordinary assertion reruns a real failure and nothing says so.
`validate()` rejects a signature this file cannot use, and #1797 owns the list.
"""

import re

SIGNATURES = (
    {
        "id": "econnreset",
        "title": "连接被对端重置（ECONNRESET）",
        # The Node/undici form, not the bare token: a pytest failure report
        # reprints test text, and `(ECONNRESET)` inside an expected message is
        # not what this signature is for. A test that only mentions the token
        # stays a real failure.
        "pattern": r"\b(?:read|write|connect) ECONNRESET\b",
        "action": "连接被重置，重跑失败 job",
    },
    {
        "id": "tls-connect-error",
        "title": "TLS 连接失败",
        "pattern": r"TLS connect error",
        "action": "TLS 握手失败，重跑失败 job",
    },
    {
        "id": "http-403-rate-limit",
        "title": "HTTP 403 限流",
        # The tool's own error line, not the API's response body: backend tests
        # quote `{"message": "API rate limit exceeded ..."}` in their source,
        # and a failure there is a real failure. `HTTP 403` (or the `gh:`
        # prefix) is what makes the line an observed rate limit, not a string
        # under test.
        "pattern": (
            r"(?:HTTP 403[^\n]*rate limit|gh:[^\n]*rate limit"
            r"|rate limit[^\n]*HTTP 403)"
        ),
        "action": "接口限流，重跑失败 job",
    },
    {
        "id": "runner-lost",
        "title": "runner 掉线",
        "pattern": (
            r"(?:runner lost|lost communication with the server"
            r"|runner has received a shutdown signal)"
        ),
        "action": "托管机掉线，重跑失败 job",
    },
    {
        "id": "setup-uv-timeout",
        "title": "setup-uv 下载超时",
        # The timeout is part of the signature: a 404, a checksum mismatch or
        # a permission error while downloading uv will not cure on a retry.
        # setup-uv's tool-cache client words it either way round: a socket
        # timeout is `Request timeout: /astral-sh/uv/...`, a connect timeout is
        # `connect ETIMEDOUT`.
        "pattern": (
            r"(?:\buv\b[^\n]*(?:timed out|timeout|ETIMEDOUT)"
            r"|(?:Request timeout|connect ETIMEDOUT)[^\n]*\buv\b)"
        ),
        "action": "uv 下载超时，重跑失败 job",
    },
)

REQUIRED_KEYS = ("id", "title", "pattern", "action")


def validate(signatures=SIGNATURES) -> None:
    """Raise ValueError on a signature rerun-failed.py could not use."""
    seen = set()
    for signature in signatures:
        if not isinstance(signature, dict):
            raise ValueError(f"{signature!r} is not an object")
        where = signature.get("id") or repr(signature)
        for key in REQUIRED_KEYS:
            value = signature.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"signature {where!r} needs a non-empty {key}")
        if signature["id"] in seen:
            raise ValueError(f"two signatures are called {signature['id']!r}")
        seen.add(signature["id"])
        try:
            re.compile(signature["pattern"])
        except re.error as error:
            raise ValueError(
                f"signature {where!r} pattern does not compile: {error}"
            ) from error
