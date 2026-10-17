"""The platform's own failures, one entry each, and the codes that name them.

The second kind of failure in the module docstring lives here: the ones the
platform recognises because it raised them, not because of what they say. Each
carries ``failure_code`` on the exception or the result it travels in, so the
copy of a sentence can be rewritten without silently reclassifying anything —
which is what happened while these were recognised from their own wording.

Two things on every entry are decisions, not descriptions:

- ``retryable`` — whether running the same turn again could plausibly go
  differently. A dead subscription credential is not, however tempting the
  button looks.
- ``host_scoped`` — whether the failure is a property of the MACHINE. Only
  these count towards "this box is dead", and moving a topic off a box costs a
  healthy machine, so a wrong ``True`` here is the expensive mistake.

Both live next to the sentence they qualify, because that is the only place a
reader sees them together.
"""

from __future__ import annotations

from app.core.sentences import say
from app.domain.agent.platform_failure_session_start import _SESSION_START_FAILURES
from app.domain.agent.platform_failure_types import PlatformFailure

STORAGE_EXHAUSTED_CODE = "storage_exhausted"
RUNTIME_IMAGE_MISSING_CODE = "runtime_image_missing"
WORKSPACE_VCS_PERMS_CODE = "workspace_vcs_perms"
HOST_UNREACHABLE_CODE = "host_unreachable"
SUBSCRIPTION_CREDENTIAL_EXPIRED_CODE = "subscription_credential_expired"
PROMPT_UNDELIVERED_CODE = "prompt_undelivered"
TURN_TIMEOUT_CODE = "turn_timeout"


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

_BY_CODE = {failure.code: failure for failure in ALL_FAILURES}
