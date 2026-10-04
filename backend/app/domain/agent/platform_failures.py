"""Stable, user-facing classifications for platform/runtime failures.

Provider details and exception traces belong in logs. Conversation events carry
only a small code + copy contract that the frontend can render safely.

Two kinds of failure arrive here and they are recognised differently:

- **Somebody else's failure** — a full disk, a missing docker image, docker
  refusing to start. The platform did not write those sentences and cannot make
  them structured, so it matches their text. That is reading a foreign format,
  which is what a parser is for.
- **The platform's own failure** — the prompt never reached the session, the
  turn hit its ceiling, the pinned machine is not answering. These used to be
  recognised the same way, by looking for a fragment of a sentence the platform
  itself had just written. Editing the copy silently reclassified the failure,
  and the copy could never be shortened past the fragment. They now carry
  ``failure_code`` on the exception or the result they travel in.
"""

from __future__ import annotations

import errno
import re
from dataclasses import dataclass, replace

from app.core.sentences import NoticeText, notice_keys, say

STORAGE_EXHAUSTED_CODE = "storage_exhausted"
RUNTIME_IMAGE_MISSING_CODE = "runtime_image_missing"
WORKSPACE_VCS_PERMS_CODE = "workspace_vcs_perms"
HOST_UNREACHABLE_CODE = "host_unreachable"
SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE = "subscription_credential_expired"
PROMPT_UNDELIVERED_CODE = "prompt_undelivered"
TURN_TIMEOUT_CODE = "turn_timeout"

# The platform's OWN wording for the two failures a driven runtime raises by
# itself: "the prompt never reached the claude session" and "this turn hit its
# ceiling". They live HERE, next to the classifier that recognises them, for the
# same reason DEVICE_OFFLINE_MESSAGE does — recognition matches the platform's own
# marker, never free-form provider text. `harness/driven/runtime.py` imports these
# instead of spelling its own copy, so the sentence and its classification cannot drift.
#
# Both used to fall through to the `else` in chat.py and render as
# 「AI 服务返回错误」, blaming the model provider for a turn the provider never
# saw — which sends whoever is debugging in exactly the wrong direction.
PROMPT_UNDELIVERED_MESSAGE = (
    "消息未能送达会话，会话没有任何响应。改动都还在，重试会重新打开会话。"
)
TURN_TIMEOUT_MESSAGE = say("turnTimeoutShort")

# The platform's OWN wording for "the machine this topic is pinned to is not
# answering". It sits next to the classification it belongs to, and the device
# provider raises it with HOST_UNREACHABLE_CODE attached. What must never happen
# is recognising it from free-form connectivity text ("connection refused", "no
# route to host") — that also fires on an unreachable *model gateway*, which is
# not a property of the machine and must never quarantine it.
DEVICE_OFFLINE_MESSAGE = (
    "话题绑定的设备已离线，重新连接后再继续（不会换到其他设备，以免工作目录和会话错乱）"
)
_STORAGE_PATTERNS = (
    re.compile(r"\bno space left on device\b", re.IGNORECASE),
    re.compile(r"\benospc\b", re.IGNORECASE),
    re.compile(r"\berrno\s*28\b", re.IGNORECASE),
)
_RUNTIME_IMAGE_MARKERS = (
    "cheesex-agent-sandbox",
    "/sandbox:",
)
_RUNTIME_IMAGE_FAILURES = (
    "unable to find image",
    "no such image",
    "pull access denied",
    "manifest unknown",
)


@dataclass(frozen=True, slots=True)
class PlatformFailure:
    code: str
    title: str
    #: 卡面上那一句。**一句**，不是一段 —— 事故卡 = 眉标 + 标题 + 正文 + 状态，
    #: 正文写成 3-5 句，卡就是 5-6 行，房间里连着几张就没法看了。
    content: str
    retryable: bool
    #: 展开才看的那部分：常见原因、该找谁、为什么反复重试没用。以前这些都挤在
    #: `content` 里。收起来 ≠ 删掉 —— 它照样进 `meta`，前端折叠展示，芝士从 API
    #: 读到的也还是全量（平台提示统一契约：信息不能丢，只能收起来）。
    detail: str = ""
    severity: str = "error"
    # Is this failure a property of THIS TURN, or of THE MACHINE the turn ran on?
    # Only host-scoped failures count towards "this machine is dead" — retrying a
    # host-scoped failure on the same box cannot help, and moving a turn off a box
    # for a failure that would follow it there is the expensive mistake.
    host_scoped: bool = False

    @property
    def meta(self) -> dict:
        detail_label = say("labelDetails") if self.detail else None
        return {
            "event_type": "platform_error",
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "retryable": self.retryable,
            "detail": self.detail or None,
            "detail_label": detail_label,
            **notice_keys(
                title=self.title, detail=self.detail, detail_label=detail_label
            ),
        }


STORAGE_EXHAUSTED = PlatformFailure(
    code=STORAGE_EXHAUSTED_CODE,
    title=say("storageExhaustedTitle"),
    content=say("storageExhausted"),
    detail=say("storageExhaustedDetail"),
    retryable=True,
    # The disk belongs to the machine. Another container on the same box hits the
    # same full filesystem, so only a different machine can help.
    host_scoped=True,
)

RUNTIME_IMAGE_MISSING = PlatformFailure(
    code=RUNTIME_IMAGE_MISSING_CODE,
    title=say("runtimeImageMissingTitle"),
    content=say("runtimeImageMissing"),
    detail=say("runtimeImageMissingDetail"),
    retryable=True,
    # A missing image is a registry/network problem that follows the topic to any
    # machine — and usually hits every machine at once. Moving the topic would burn
    # a healthy box for nothing, so this must NOT count towards the machine's health.
    host_scoped=False,
)

HOST_UNREACHABLE = PlatformFailure(
    code=HOST_UNREACHABLE_CODE,
    title=say("hostUnreachableTitle"),
    content=say("hostUnreachable"),
    # 话题一旦绑定就不会再换设备——`device_provider.resolve_device` 只在第一轮
    # 挑一次，之后任何一轮都回到同一台。所以这句只说该设备重新连上，不承诺平台
    # 会替它找一台：那是没有的机制，等它等不来。
    detail=say("hostUnreachableDetail"),
    retryable=True,
    host_scoped=True,
)


SUBSCRIPTION_CREDENTIAL_EXPIRED = PlatformFailure(
    code=SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE,
    title=say("subscriptionCredentialExpiredTitle"),
    content=say("subscriptionCredentialExpired"),
    detail=say("subscriptionCredentialExpiredDetail"),
    # Not retried automatically: another turn against the same dead credential just
    # burns 300s again (the platform "对自己的失败没有记忆" complaint in #388). It
    # self-heals on the next human summon once the host re-auths.
    retryable=False,
    # The subscription credential is DEPLOYMENT-level (one metering proxy, shared
    # by every machine), not a property of one box — moving the topic to another
    # machine reaches the same dead credential. So it must NOT indict the machine.
    host_scoped=False,
)


WORKSPACE_VCS_PERMS = PlatformFailure(
    code=WORKSPACE_VCS_PERMS_CODE,
    title=say("workspaceVcsPermsTitle"),
    content=say("workspaceVcsPerms"),
    detail=say("workspaceVcsPermsDetail"),
    retryable=True,
)


PROMPT_UNDELIVERED = PlatformFailure(
    code=PROMPT_UNDELIVERED_CODE,
    title=say("promptUndeliveredTitle"),
    content=say("promptUndelivered"),
    detail=say("promptUndeliveredDetail"),
    retryable=True,
    # NOT host-scoped: a wedged or dead claude session is a property of THIS
    # topic's screen, not of the box. Every other topic on the same machine is
    # usually fine, so counting this against the machine would quarantine a
    # healthy box and drag unrelated topics onto a new one for nothing.
    host_scoped=False,
)


TURN_TIMEOUT = PlatformFailure(
    code=TURN_TIMEOUT_CODE,
    title=say("turnLimitTitle"),
    content=say("turnLimit"),
    detail=say("turnLimitDetail"),
    retryable=True,
    # Same reasoning as PROMPT_UNDELIVERED, and more sharply so: a turn that ran
    # long is usually a property of the WORK, not of the machine it ran on.
    host_scoped=False,
)


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
    "session_start_program_missing", "sessionStartProgramMissing", retryable=False
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


# Every classification this module can return. Keep new failures in this tuple —
# ``HOST_SCOPED_CODES`` is derived from it, so a failure left out silently opts
# itself out of the machine-health accounting.
ALL_FAILURES = (
    STORAGE_EXHAUSTED,
    RUNTIME_IMAGE_MISSING,
    HOST_UNREACHABLE,
    WORKSPACE_VCS_PERMS,
    PROMPT_UNDELIVERED,
    TURN_TIMEOUT,
    *_SESSION_START_FAILURES,
)

# Failure codes that indict the MACHINE rather than the turn. The turn layer reads
# this off the wire (error frames carry only a code) to tell "this box is suspect"
# from "this run went wrong".
HOST_SCOPED_CODES = frozenset(f.code for f in ALL_FAILURES if f.host_scoped)


def _text_is_storage_exhausted(text: str) -> bool:
    return any(pattern.search(text) for pattern in _STORAGE_PATTERNS)


def is_storage_exhausted(value: BaseException | str) -> bool:
    """Conservatively identify ENOSPC without guessing from vague copy."""
    if isinstance(value, str):
        return _text_is_storage_exhausted(value)

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, OSError) and current.errno == errno.ENOSPC:
            return True
        if _text_is_storage_exhausted(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


def _text_is_runtime_image_missing(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _RUNTIME_IMAGE_MARKERS) and any(
        failure in lowered for failure in _RUNTIME_IMAGE_FAILURES
    )


def is_runtime_image_missing(value: BaseException | str) -> bool:
    """Identify an unavailable Cheese agent image without leaking daemon copy."""
    if isinstance(value, str):
        return _text_is_runtime_image_missing(value)

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if _text_is_runtime_image_missing(str(current)):
            return True
        current = current.__cause__ or current.__context__
    return False


_BY_CODE = {failure.code: failure for failure in ALL_FAILURES}


def failure_code_of(value: BaseException | str) -> str | None:
    """The code an exception is carrying, if it raised itself deliberately.

    Walks the cause/context chain, because the raise site and the handler that
    turns it into a result are usually several frames and one wrapper apart."""
    if isinstance(value, str):
        return None

    seen: set[int] = set()
    current: BaseException | None = value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "failure_code", None)
        if isinstance(code, str) and code:
            return code
        current = current.__cause__ or current.__context__
    return None


def classify_platform_failure(
    value: BaseException | str,
    *,
    code: str | None = None,
) -> PlatformFailure | None:
    """What went wrong, or None when the platform cannot say.

    ``code`` is what the failure declared about itself — from an ``AgentResult``
    that carried one across the turn boundary. It wins over everything below,
    which only ever inspects text the platform did not write."""
    carried = code or failure_code_of(value)
    if carried is not None:
        return _BY_CODE.get(carried)
    if is_storage_exhausted(value):
        return STORAGE_EXHAUSTED
    if is_runtime_image_missing(value):
        return RUNTIME_IMAGE_MISSING
    return None


# --- CLI 自己印在对话里的英文提示 -------------------------------------------
#
# 上面那些故障都走异常或 `AgentResult`。这一类不走:它们是 Claude Code 自己
# **当成助手输出**印出来的一句话,于是原样落进房间,看起来像芝士在用英文说
# 「API Error: Unable to connect to API (ConnectionRefused)」。实测 200 个会话
# 里 88 条,而且高度集中 —— 去重之后就几句,前两句占了 57 条。
#
# 这正是本模块开头说的第一类:「别人写的句子,平台没法让它变结构化,只能匹配
# 它的文本」。识别出来之后它们该走平台提示卡,不该顶着芝士的名字发英文。
#
# 匹配**整条消息**,不是子串:芝士自己用中文讨论一个报错时会把原话引在句子里,
# 那条消息是它的话,不能被换掉。所以三个条件缺一不可 —— 通篇没有汉字、短、且
# 从已知的开头起头。
PROVIDER_UNREACHABLE_CODE = "provider_unreachable"
PROVIDER_OVERLOADED_CODE = "provider_overloaded"
MODEL_LIMIT_REACHED_CODE = "model_limit_reached"
RESPONSE_TRUNCATED_CODE = "response_truncated"
TOOL_UNAVAILABLE_CODE = "tool_unavailable"

#: 一条 CLI 提示最长能有多长。真实样本最长的一条 120 字符出头;留三倍余量,再长
#: 就不是提示而是内容了。
_CLI_NOTICE_MAX = 300

_CLI_NOTICES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^API Error:.*\b(ConnectionRefused|ECONNRESET|Connection refused)\b",
            re.I,
        ),
        PROVIDER_UNREACHABLE_CODE,
    ),
    (
        re.compile(r"^API Error:\s*(502|503|529)\b", re.I),
        PROVIDER_OVERLOADED_CODE,
    ),
    (
        re.compile(r"^API Error:.*\b(Overloaded|Bad Gateway)\b", re.I),
        PROVIDER_OVERLOADED_CODE,
    ),
    (
        re.compile(r"^You'?ve reached your .*\blimit\b", re.I),
        MODEL_LIMIT_REACHED_CODE,
    ),
    (
        re.compile(
            r"^(API Error:\s*)?(Response stalled mid-stream"
            r"|The response stopped arriving)",
            re.I,
        ),
        RESPONSE_TRUNCATED_CODE,
    ),
    # 「我还没有 X 的权限,请批准一下」—— 在这个平台上根本没有人能批准:那个
    # 授权框画在容器的终端里,房间里的人够不着。所以它出现本身就说明配置不对,
    # 而它读起来却像一句正常的请求 —— 一个故障伪装成了一句话,最坏的一种。
    (
        re.compile(
            r"^(I don'?t have permission to use\b"
            r"|No response requested\.?$"
            r"|Tool ran without output)",
            re.I,
        ),
        TOOL_UNAVAILABLE_CODE,
    ),
)

_HAS_CJK = re.compile(r"[一-鿿]")


def classify_cli_notice(text: str) -> str | None:
    """整条消息其实是 CLI 自己印的一句英文提示时,它属于哪一类;否则 None。"""
    line = text.strip()
    if not line or len(line) > _CLI_NOTICE_MAX or _HAS_CJK.search(line):
        return None
    for pattern, failure in _CLI_NOTICES:
        if pattern.search(line):
            return failure
    return None
