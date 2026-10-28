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

This module is what every caller imports and nothing else needs to know about.
The work sits in five siblings, split by which of the two kinds above they
recognise:

- ``platform_failure_types`` — the shape one classification answers in
  (``PlatformFailure``) and the read half of the carried-code contract.
- ``platform_failure_rules`` — matching the text somebody else wrote.
- ``platform_failure_session_start`` — reading a runner's log for a session
  that never came up.
- ``platform_failure_catalog`` — the platform's own failures, one entry each,
  with the ``retryable`` and ``host_scoped`` decision that qualifies it.
- ``platform_failure_cli_notices`` — the lines Claude Code prints into the
  conversation as if the agent had said them.

Every name that used to resolve here still does: the five halves are imported
in full rather than only in part, so `app.domain.agent.platform_failures._xxx`
keeps working for anything that reaches a helper by that path or patches it by
string. Renaming is not what a size split is for.
"""

from __future__ import annotations

# 下面这些 import 都是再导出：`X as X` 是仓库里认的那种写法，读的人一眼看出它
# 进来不是为了在本文件里用。拆分改的是文件，不是这个模块能取到的名字 ——
# `errno`、`re`、`dataclass`、`replace`、`NoticeText`、`notice_keys` 是原来借这里
# 的 import 露面的，全仓已经没有人按本模块路径取它们，但留着这一层，就没有任何
# 一次导入的结果改变，也没有一个按字符串打的补丁会到解析期才炸。
import errno as errno
import re as re
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace

from app.core.sentences import NoticeText as NoticeText
from app.core.sentences import notice_keys as notice_keys
from app.core.sentences import say
from app.domain.agent.platform_failure_catalog import (
    _BY_CODE,
    STORAGE_EXHAUSTED,
)
from app.domain.agent.platform_failure_catalog import (
    ALL_FAILURES as ALL_FAILURES,
)
from app.domain.agent.platform_failure_catalog import (
    HOST_SCOPED_CODES as HOST_SCOPED_CODES,
)
from app.domain.agent.platform_failure_catalog import (
    HOST_UNREACHABLE as HOST_UNREACHABLE,
)
from app.domain.agent.platform_failure_catalog import (
    HOST_UNREACHABLE_CODE as HOST_UNREACHABLE_CODE,
)
from app.domain.agent.platform_failure_catalog import (
    PROMPT_UNDELIVERED as PROMPT_UNDELIVERED,
)
from app.domain.agent.platform_failure_catalog import (
    PROMPT_UNDELIVERED_CODE as PROMPT_UNDELIVERED_CODE,
)
from app.domain.agent.platform_failure_catalog import (
    RUNTIME_IMAGE_MISSING as RUNTIME_IMAGE_MISSING,
)
from app.domain.agent.platform_failure_catalog import (
    RUNTIME_IMAGE_MISSING_CODE as RUNTIME_IMAGE_MISSING_CODE,
)
from app.domain.agent.platform_failure_catalog import (
    STORAGE_EXHAUSTED_CODE as STORAGE_EXHAUSTED_CODE,
)
from app.domain.agent.platform_failure_catalog import (
    SUBSCRIPTION_CREDENTIAL_EXPIRED as SUBSCRIPTION_CREDENTIAL_EXPIRED,
)
from app.domain.agent.platform_failure_catalog import (
    SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE as SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE,
)
from app.domain.agent.platform_failure_catalog import (
    TURN_TIMEOUT as TURN_TIMEOUT,
)
from app.domain.agent.platform_failure_catalog import (
    TURN_TIMEOUT_CODE as TURN_TIMEOUT_CODE,
)
from app.domain.agent.platform_failure_catalog import (
    WORKSPACE_VCS_PERMS as WORKSPACE_VCS_PERMS,
)
from app.domain.agent.platform_failure_catalog import (
    WORKSPACE_VCS_PERMS_CODE as WORKSPACE_VCS_PERMS_CODE,
)
from app.domain.agent.platform_failure_cli_notices import (
    _CLI_NOTICE_MAX as _CLI_NOTICE_MAX,
)
from app.domain.agent.platform_failure_cli_notices import (
    _CLI_NOTICES as _CLI_NOTICES,
)
from app.domain.agent.platform_failure_cli_notices import (
    _HAS_CJK as _HAS_CJK,
)
from app.domain.agent.platform_failure_cli_notices import (
    _REQUEST_REFUSED as _REQUEST_REFUSED,
)
from app.domain.agent.platform_failure_cli_notices import (
    CONTROL_PLANE_UNAVAILABLE_CODE as CONTROL_PLANE_UNAVAILABLE_CODE,
)
from app.domain.agent.platform_failure_cli_notices import (
    MODEL_LIMIT_REACHED_CODE as MODEL_LIMIT_REACHED_CODE,
)
from app.domain.agent.platform_failure_cli_notices import (
    PROVIDER_OVERLOADED_CODE as PROVIDER_OVERLOADED_CODE,
)
from app.domain.agent.platform_failure_cli_notices import (
    PROVIDER_UNREACHABLE_CODE as PROVIDER_UNREACHABLE_CODE,
)
from app.domain.agent.platform_failure_cli_notices import (
    RESPONSE_TRUNCATED_CODE as RESPONSE_TRUNCATED_CODE,
)
from app.domain.agent.platform_failure_cli_notices import (
    SUBSCRIPTION_ACCOUNTS_COOLING_CODE as SUBSCRIPTION_ACCOUNTS_COOLING_CODE,
)
from app.domain.agent.platform_failure_cli_notices import (
    SUBSCRIPTION_EGRESS_OFFLINE_CODE as SUBSCRIPTION_EGRESS_OFFLINE_CODE,
)
from app.domain.agent.platform_failure_cli_notices import (
    TOOL_UNAVAILABLE_CODE as TOOL_UNAVAILABLE_CODE,
)
from app.domain.agent.platform_failure_cli_notices import (
    classify_cli_notice as classify_cli_notice,
)
from app.domain.agent.platform_failure_cli_notices import (
    is_request_refused as is_request_refused,
)
from app.domain.agent.platform_failure_rules import (
    _RUNTIME_IMAGE_FAILURES as _RUNTIME_IMAGE_FAILURES,
)
from app.domain.agent.platform_failure_rules import (
    _RUNTIME_IMAGE_MARKERS as _RUNTIME_IMAGE_MARKERS,
)
from app.domain.agent.platform_failure_rules import (
    _STORAGE_PATTERNS as _STORAGE_PATTERNS,
)
from app.domain.agent.platform_failure_rules import (
    _text_is_runtime_image_missing as _text_is_runtime_image_missing,
)
from app.domain.agent.platform_failure_rules import (
    _text_is_storage_exhausted as _text_is_storage_exhausted,
)
from app.domain.agent.platform_failure_rules import (
    is_provider_out_of_credit as is_provider_out_of_credit,
)
from app.domain.agent.platform_failure_rules import (
    is_runtime_image_missing,
    is_storage_exhausted,
)
from app.domain.agent.platform_failure_session_start import (
    _DOCKER_FAILED as _DOCKER_FAILED,
)
from app.domain.agent.platform_failure_session_start import (
    _HARNESS as _HARNESS,
)
from app.domain.agent.platform_failure_session_start import (
    _PLATFORM_HTTP as _PLATFORM_HTTP,
)
from app.domain.agent.platform_failure_session_start import (
    _PROGRAM as _PROGRAM,
)
from app.domain.agent.platform_failure_session_start import (
    _PROGRAM_BROKEN as _PROGRAM_BROKEN,
)
from app.domain.agent.platform_failure_session_start import (
    _PROGRAM_MISSING as _PROGRAM_MISSING,
)
from app.domain.agent.platform_failure_session_start import (
    _PROGRAM_NAME as _PROGRAM_NAME,
)
from app.domain.agent.platform_failure_session_start import (
    _SESSION_START_FAILURES as _SESSION_START_FAILURES,
)
from app.domain.agent.platform_failure_session_start import (
    _SH as _SH,
)
from app.domain.agent.platform_failure_session_start import (
    _SPAWN_MISSING as _SPAWN_MISSING,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_CODES as SESSION_START_CODES,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_DOCKER_UNAVAILABLE as SESSION_START_DOCKER_UNAVAILABLE,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_EXECUTOR_FAILED as SESSION_START_EXECUTOR_FAILED,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_EXECUTOR_IMAGE_MISSING as SESSION_START_EXECUTOR_IMAGE_MISSING,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_EXECUTOR_NAME_TAKEN as SESSION_START_EXECUTOR_NAME_TAKEN,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_MODEL_LOGIN as SESSION_START_MODEL_LOGIN,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_PLATFORM_CREDENTIAL as SESSION_START_PLATFORM_CREDENTIAL,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_PLATFORM_ERROR as SESSION_START_PLATFORM_ERROR,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_PROGRAM_BROKEN as SESSION_START_PROGRAM_BROKEN,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_PROGRAM_MISSING as SESSION_START_PROGRAM_MISSING,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_RUNNER_BUSY as SESSION_START_RUNNER_BUSY,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_TIMEOUT as SESSION_START_TIMEOUT,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_UNKNOWN as SESSION_START_UNKNOWN,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_WORK_MACHINE_OFFLINE as SESSION_START_WORK_MACHINE_OFFLINE,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_WORK_MACHINE_PREPARING as SESSION_START_WORK_MACHINE_PREPARING,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_WORK_MACHINE_REFUSED as SESSION_START_WORK_MACHINE_REFUSED,
)
from app.domain.agent.platform_failure_session_start import (
    SESSION_START_WORK_MACHINE_UNBOUND as SESSION_START_WORK_MACHINE_UNBOUND,
)
from app.domain.agent.platform_failure_session_start import (
    _classify_session_start as _classify_session_start,
)
from app.domain.agent.platform_failure_session_start import (
    _named as _named,
)
from app.domain.agent.platform_failure_session_start import (
    _program_name as _program_name,
)
from app.domain.agent.platform_failure_session_start import (
    _reason_of as _reason_of,
)
from app.domain.agent.platform_failure_session_start import (
    _start_failure as _start_failure,
)
from app.domain.agent.platform_failure_session_start import (
    _start_line as _start_line,
)
from app.domain.agent.platform_failure_session_start import (
    classify_session_start as classify_session_start,
)
from app.domain.agent.platform_failure_types import (
    PlatformFailure,
    failure_code_of,
)

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
    "消息没送到 AI 队友，它没有任何响应。改动都还在，重试会让它重新启动。"
)
TURN_TIMEOUT_MESSAGE = say("turnTimeoutShort")
#: A turn the platform's upgrade cut short: the runner the previous release
#: started was replaced by one of this release's, on the same conversation.
#: The turn is started again by itself (``resumable_failures``), so its line
#: says so and names the teammate whose work picks up again.
RUNNER_REPLACED_CODE = "runner_replaced"


def runner_replaced_message(agent_handle: str) -> NoticeText:
    """The line a turn the upgrade cut short ends with."""
    return say("sessionReplacedForUpgrade", agent=f"<@{agent_handle}>")


# The platform's OWN wording for "the machine this topic is pinned to is not
# answering". It sits next to the classification it belongs to, and the device
# provider raises it with HOST_UNREACHABLE_CODE attached. What must never happen
# is recognising it from free-form connectivity text ("connection refused", "no
# route to host") — that also fires on an unreachable *model gateway*, which is
# not a property of the machine and must never quarantine it.
DEVICE_OFFLINE_MESSAGE = (
    "频道绑定的设备已离线，设备重新连上后接着做。不会换到别的设备，以免工作目录错乱"
)


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
