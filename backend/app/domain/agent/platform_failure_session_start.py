"""A session that never came up, read out of its runner's log.

This is the one classifier whose input is a foreign format — a launch record
plus the tail of what starting printed — so it lives apart from the catalogue it
feeds and from the rules that read the platform's own exceptions. Its sentences
are the platform's own, but which one applies is decided by matching text the
failing program wrote, so the whole list of causes belongs in one place, next to
the patterns that recognise them.
"""

from __future__ import annotations

import re
from dataclasses import replace

from app.core.sentences import NoticeText, say
from app.domain.agent.platform_failure_rules import _RUNTIME_IMAGE_FAILURES
from app.domain.agent.platform_failure_types import PlatformFailure

# --- Claude Code 没有启动 ------------------------------------------------------
#
# A session that dies on its way up leaves its reason in its runner's log: the
# exit status and the tail of what that start printed (``claude_code/runner.py``),
# usually a Python traceback from the executor client's ``bootstrap``. That text
# is the process's, not the room's. The room is told one sentence, chosen here
# from the text; the text itself goes to 现场 (``meta.error`` on the notice).
#
# Every one of these is an event about this start, none about the machine's
# health: ``host_scoped`` stays False, as it was while they had no code at all.
#
# The room line is one sentence, 「<harness> 启动失败：<reason>」, with the reason
# a sentence of its own, so a harness other than Claude Code and a reason that
# names a program are both parameters of the same line.
_HARNESS = "Claude Code"


def _start_line(reason: NoticeText, harness: str = _HARNESS) -> NoticeText:
    return say("sessionStartFailed", harness=harness, reason=reason)


def _start_failure(code: str, reason: str, *, retryable: bool) -> PlatformFailure:
    return PlatformFailure(
        code=code,
        title=say("sessionStartFailedTitle"),
        content=_start_line(say(reason)),
        retryable=retryable,
    )


SESSION_START_UNKNOWN = _start_failure(
    "session_start_unknown", "sessionStartUnknown", retryable=True
)
SESSION_START_TIMEOUT = _start_failure(
    "session_start_timeout", "sessionStartTimeout", retryable=True
)
SESSION_START_RUNNER_BUSY = _start_failure(
    "session_start_runner_busy", "sessionStartRunnerBusy", retryable=True
)
SESSION_START_WORK_MACHINE_PREPARING = _start_failure(
    "session_start_work_machine_preparing",
    "sessionStartWorkMachinePreparing",
    retryable=True,
)
SESSION_START_WORK_MACHINE_OFFLINE = _start_failure(
    "session_start_work_machine_offline",
    "sessionStartWorkMachineOffline",
    retryable=True,
)
SESSION_START_WORK_MACHINE_UNBOUND = _start_failure(
    "session_start_work_machine_unbound",
    "sessionStartWorkMachineUnbound",
    retryable=False,
)
SESSION_START_WORK_MACHINE_REFUSED = _start_failure(
    "session_start_work_machine_refused",
    "sessionStartWorkMachineRefused",
    retryable=True,
)
SESSION_START_EXECUTOR_IMAGE_MISSING = _start_failure(
    "session_start_executor_image_missing",
    "sessionStartExecutorImageMissing",
    retryable=False,
)
SESSION_START_EXECUTOR_FAILED = _start_failure(
    "session_start_executor_failed", "sessionStartExecutorFailed", retryable=False
)
SESSION_START_EXECUTOR_NAME_TAKEN = _start_failure(
    "session_start_executor_name_taken",
    "sessionStartExecutorNameTaken",
    retryable=True,
)
SESSION_START_DOCKER_UNAVAILABLE = _start_failure(
    "session_start_docker_unavailable",
    "sessionStartDockerUnavailable",
    retryable=False,
)
SESSION_START_MODEL_LOGIN = _start_failure(
    "session_start_model_login",
    "sessionStartModelLogin",
    retryable=False,
)
SESSION_START_PLATFORM_CREDENTIAL = _start_failure(
    "session_start_platform_credential",
    "sessionStartPlatformCredential",
    retryable=True,
)
SESSION_START_PLATFORM_ERROR = _start_failure(
    "session_start_platform_error", "sessionStartPlatformError", retryable=True
)
SESSION_START_PROGRAM_BROKEN = _start_failure(
    "session_start_program_broken",
    "sessionStartProgramBroken",
    retryable=False,
)
SESSION_START_PROGRAM_MISSING = _start_failure(
    "session_start_program_missing",
    "sessionStartProgramMissing",
    retryable=False,
)

_SESSION_START_FAILURES = (
    SESSION_START_UNKNOWN,
    SESSION_START_TIMEOUT,
    SESSION_START_RUNNER_BUSY,
    SESSION_START_WORK_MACHINE_PREPARING,
    SESSION_START_WORK_MACHINE_OFFLINE,
    SESSION_START_WORK_MACHINE_UNBOUND,
    SESSION_START_WORK_MACHINE_REFUSED,
    SESSION_START_EXECUTOR_IMAGE_MISSING,
    SESSION_START_EXECUTOR_FAILED,
    SESSION_START_EXECUTOR_NAME_TAKEN,
    SESSION_START_DOCKER_UNAVAILABLE,
    SESSION_START_MODEL_LOGIN,
    SESSION_START_PLATFORM_CREDENTIAL,
    SESSION_START_PLATFORM_ERROR,
    SESSION_START_PROGRAM_BROKEN,
    SESSION_START_PROGRAM_MISSING,
)
#: A start failure's sentence can name what it found (「机器上缺少 docker」), so
#: the room line is the sentence the failure was raised with, not the class's
#: fixed copy. Both come from ``classify_session_start``.
SESSION_START_CODES = frozenset(f.code for f in _SESSION_START_FAILURES)
# A program as a shell or Python names it when it cannot run it: a path.
_PROGRAM = r"([^\s'\":]{1,200})"
_PROGRAM_NAME = re.compile(r"[A-Za-z0-9._+-]{1,40}")
# How a shell names the program it could not run — dash:
# `sh: 1: exec: /x/claude: not found`; bash: `sh: line 1: claude: command not
# found`, `/x/wrapper: line 6: exec: /x/claude: cannot execute: No such file or
# directory`.
_SH = r"^\S+: (?:(?:line )?\d+: )?(?:exec: )?" + _PROGRAM + ": "
_PROGRAM_MISSING = (
    re.compile(_SH + "(?:command )?not found", re.M),
    re.compile(_SH + "(?:cannot execute: )?No such file or directory", re.M),
)
_PROGRAM_BROKEN = (
    re.compile(_SH + "(?:Permission denied|cannot execute|Exec format error)", re.M),
    # Python's os.exec* / subprocess: `[Errno 8] Exec format error: '/x/claude'`.
    re.compile(r"Exec format error(?:: '" + _PROGRAM + "')?"),
)
# Python spawning a program that is not there: subprocess or os.exec*.
_SPAWN_MISSING = re.compile(
    r"FileNotFoundError: \[Errno 2\] No such file or directory: '" + _PROGRAM + r"'"
)
_PLATFORM_HTTP = re.compile(r"Platform HTTP (\d{3})\b")
#: How `remote_execution/private.py` reports the network or image check failing.
_DOCKER_FAILED = re.compile(r"\bdocker (network|image) \S.* exited with status")


def _program_name(path: str | None) -> str | None:
    """What to call a program in the room: its file name, or Claude Code for
    the pinned binary (``<...>/claude/versions/<v>``). Never a whole path, and
    nothing that is not a plain name."""
    parts = [part for part in (path or "").split("/") if part]
    if not parts:
        return None
    if parts[-1] in ("claude", "claude.exe") or parts[-3:-1] == ["claude", "versions"]:
        return "Claude Code"
    return parts[-1] if _PROGRAM_NAME.fullmatch(parts[-1]) else None


def _named(failure: PlatformFailure, path: str | None, reason: str) -> PlatformFailure:
    name = _program_name(path)
    if not name:
        return failure
    return replace(failure, content=_start_line(say(reason, name=name)))


def _reason_of(failure: PlatformFailure) -> NoticeText:
    """The reason half of a session-start failure's line."""
    content = failure.content
    assert isinstance(content, NoticeText)
    reason = content.params["reason"]
    assert isinstance(reason, NoticeText)
    return reason


def classify_session_start(
    log: str, *, harness: str = "Claude Code", timed_out: bool = False
) -> PlatformFailure:
    """Which sentence the room gets for a session that did not start.

    ``harness`` is the name the sentence opens with (Claude Code, pi, Codex).
    The causes are the same programs failing the same ways whichever harness
    was starting: the executor client, docker, the platform's lease."""
    failure = _classify_session_start(log, timed_out=timed_out)
    if harness == _HARNESS:
        return failure
    return replace(failure, content=_start_line(_reason_of(failure), harness))


def _classify_session_start(log: str, *, timed_out: bool) -> PlatformFailure:
    """The classification, worded for Claude Code.

    ``log`` is what its runner's log said: this launch's record, or with
    ``timed_out`` the log's tail when no record came in the whole wait. The
    patterns are the shapes the failing programs print (dev's session host,
    2026-09); anything else gets a sentence that says the cause was not
    recognised, never the text itself."""
    text = log or ""
    lowered = text.lower()
    if "BlockingIOError" in text and ("runner.lock" in text or "flock" in text):
        return SESSION_START_RUNNER_BUSY
    # The lease is taken in the bootstrap's `_take_leased_machine`, through the
    # executor transport's `acquire`: their frames name it in the traceback.
    if "_take_leased_machine" in text or ", in acquire" in text:
        http = _PLATFORM_HTTP.search(text)
        if (http and http.group(1) == "504") or "准备" in text:
            return SESSION_START_WORK_MACHINE_PREPARING
        # The lease route's own reason, as the session printed it.
        if "未连接" in text:
            return SESSION_START_WORK_MACHINE_OFFLINE
        if "解绑" in text:
            return SESSION_START_WORK_MACHINE_UNBOUND
        return SESSION_START_WORK_MACHINE_REFUSED
    if "docker" in lowered:
        # The executor container, as docker's own stderr describes it
        # (`remote_execution/private.py` keeps it in the failure it raises).
        if any(failure in lowered for failure in _RUNTIME_IMAGE_FAILURES):
            return SESSION_START_EXECUTOR_IMAGE_MISSING
        if "is already in use by container" in lowered:
            return SESSION_START_EXECUTOR_NAME_TAKEN
        if "cannot connect to the docker daemon" in lowered or (
            "permission denied" in lowered and "docker daemon socket" in lowered
        ):
            return SESSION_START_DOCKER_UNAVAILABLE
        # `docker run`, and the network and egress proxy it runs behind.
        if (
            "calledprocesserror" in lowered
            or "docker run" in lowered
            or _DOCKER_FAILED.search(lowered)
        ):
            return SESSION_START_EXECUTOR_FAILED
    if (
        "run /login" in text
        or "invalid api key" in lowered
        or "oauth token" in lowered
        or "authentication_error" in lowered
    ):
        return SESSION_START_MODEL_LOGIN
    http = _PLATFORM_HTTP.search(text)
    if http:
        if http.group(1) in ("401", "403"):
            return SESSION_START_PLATFORM_CREDENTIAL
        return SESSION_START_PLATFORM_ERROR
    missing = list(_PROGRAM_MISSING)
    if "_execute_child" in text or "os.exec" in text:
        # Only a spawn's FileNotFoundError names a program; any other names a
        # file some code expected.
        missing.append(_SPAWN_MISSING)
    for pattern in missing:
        if match := pattern.search(text):
            return _named(
                SESSION_START_PROGRAM_MISSING, match.group(1), "sessionStartNoProgram"
            )
    # After the missing ones: bash says `cannot execute` for both.
    for pattern in _PROGRAM_BROKEN:
        if match := pattern.search(text):
            return _named(
                SESSION_START_PROGRAM_BROKEN,
                match.group(1),
                "sessionStartProgramCannotRun",
            )
    return SESSION_START_TIMEOUT if timed_out else SESSION_START_UNKNOWN
