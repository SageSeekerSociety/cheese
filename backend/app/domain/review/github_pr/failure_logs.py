"""What a failed check's job log is read down to before it reaches a card.

A red check used to reach 芝士 as `"shell-tests: failure"` and nothing else, so
every CI failure cost a round trip: read the useless line, go dig the log out of
GitHub by hand, only then start fixing. `check_state` digs it once and puts the
answer in the message; this is the digging — the constants that bound it, the
headline for one failed run, and the slice of a job log worth reading.

`_download_log_tail` is the only thing here that touches the network, and it
streams a pre-signed blob URL GitHub redirected us to: no token of ours is sent
to that host.
"""

import re

import httpx

# --- 失败详情 (CI失败要把日志送到芝士眼前) ------------------------------------
#
# A red check used to reach 芝士 as `"shell-tests: failure"` and nothing else,
# so every CI failure cost a round trip: read the useless line, go dig the log
# out of GitHub by hand, only then start fixing. The backend already holds a
# token that can read both, so it digs once and puts the answer in the message.
#
# How many failed jobs get their log pulled. Beyond this only the headline
# survives — a PR that reddens ten jobs is one root cause repeated, not ten
# investigations, and every extra job is another GitHub round trip per poll.
_FAILURE_DETAIL_MAX_JOBS = 3
#: Hard stop on how much of one job's log is downloaded before giving up.
_LOG_DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
#: How much of the download is KEPT — the tail, because that's where a failing
#: run ends up. A log longer than this streams past a sliding window rather
#: than being cut at the front, so the end is always the part we hold.
_LOG_TAIL_BYTES = 256 * 1024
#: Lines of run-up kept before the error marker. See `_error_excerpt`.
_LOG_CONTEXT_LINES = 15
_LOG_EXCERPT_MAX_CHARS = 1200
_LOG_LINE_MAX_CHARS = 200
_LOG_OUTPUT_FIELD_MAX_CHARS = 400

#: Every Actions log line is prefixed `2026-08-11T07:44:58.9065765Z `.
_LOG_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z ")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
#: Actions masks its own secrets as `***`, but a log can still echo a token
#: that the runner never knew was one (a curl of our API, a `gh auth status`).
#: Redact the shapes GitHub itself hands out before any of this enters a topic.
_TOKEN_SHAPE_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})"
)


def _failure_headline(run: dict) -> str:
    """One failed check-run's name, conclusion, link, and `output` text.

    `output.title`/`output.summary` are where GitHub's docs say the failure
    summary lives, and where the odd non-Actions check-run does put it — but
    Actions itself leaves both null and files the detail as annotations
    instead (verified 2026-08-11), which is why the log excerpt below is the
    part that actually carries the error. Included when present, skipped
    silently when not.
    """
    lines = [f"▸ {run.get('name', '?')}（{run.get('conclusion')}）"]
    url = run.get("html_url") or run.get("details_url")
    if url:
        lines.append(f"  {url}")
    output = run.get("output") or {}
    for key in ("title", "summary"):
        value = output.get(key)
        if isinstance(value, str) and value.strip():
            text = _redact(value.strip())[:_LOG_OUTPUT_FIELD_MAX_CHARS]
            lines.extend(f"  {ln}" for ln in text.splitlines())
    return "\n".join(lines)


def _redact(text: str) -> str:
    return _TOKEN_SHAPE_RE.sub("<已脱敏>", text)


def _clean_log_line(raw: str) -> str:
    line = _LOG_TIMESTAMP_RE.sub("", raw.rstrip("\r\n"))
    line = _ANSI_RE.sub("", line)
    return _redact(line)[:_LOG_LINE_MAX_CHARS]


def _error_excerpt(lines: list[str]) -> str:
    """The interesting slice of a failed job's log.

    Grepping `##[error]` on its own is close to useless: what a failed shell
    step actually emits is `##[error]Process completed with exit code 1.`,
    and the line that says WHAT broke (`FAIL: the secret file was not handed
    over`) is the one right above it — verified against this repo's
    shell-tests job on 2026-08-11. So the excerpt is the LAST error marker
    plus its run-up, and a log with no marker at all (a cancelled or
    timed-out job) falls back to its tail.
    """
    if not lines:
        return ""
    marks = [i for i, line in enumerate(lines) if "##[error]" in line]
    end = marks[-1] + 1 if marks else len(lines)
    start = max(0, end - _LOG_CONTEXT_LINES - 1)
    text = "\n".join(line for line in lines[start:end] if line.strip())
    if len(text) > _LOG_EXCERPT_MAX_CHARS:
        text = "…" + text[-_LOG_EXCERPT_MAX_CHARS:]
    return text


async def _download_log_tail(client: httpx.AsyncClient, url: str) -> str:
    """Stream `url` keeping only its last `_LOG_TAIL_BYTES`.

    A sliding window rather than a read-then-cut: an Actions log has no size
    ceiling, the part worth reading is at the END, and a naive
    `resp.text[:cap]` would faithfully deliver the setup steps of a job that
    failed twenty minutes later.
    """
    window = bytearray()
    downloaded = 0
    async with client.stream("GET", url, headers={}) as resp:
        if resp.status_code != 200:
            return ""
        async for chunk in resp.aiter_bytes():
            downloaded += len(chunk)
            window.extend(chunk)
            if len(window) > _LOG_TAIL_BYTES:
                del window[: len(window) - _LOG_TAIL_BYTES]
            if downloaded >= _LOG_DOWNLOAD_MAX_BYTES:
                break
    return window.decode("utf-8", errors="replace")
