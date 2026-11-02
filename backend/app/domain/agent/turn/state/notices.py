"""Pure native failure and tool-action classification."""

from app.core.obs import scrub_secrets
from app.core.sentences import NoticeText, say
from app.domain.agent.cli_notices import cli_notice
from app.domain.agent.platform_failures import (
    CONTROL_PLANE_UNAVAILABLE_CODE,
    MODEL_LIMIT_REACHED_CODE,
    PROVIDER_OVERLOADED_CODE,
    PROVIDER_UNREACHABLE_CODE,
    RUNNER_REPLACED_CODE,
    SESSION_START_CODES,
    SUBSCRIPTION_ACCOUNTS_COOLING_CODE,
    SUBSCRIPTION_EGRESS_OFFLINE_CODE,
    classify_cli_notice,
    classify_platform_failure,
    is_provider_out_of_credit,
    is_request_refused,
)
from app.domain.notice_meta import (
    EVENT_TURN_FAILED,
    SEVERITY_ERROR,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)

_TOOL_ACTION = {
    "cheese_notify": "notify",
}

#: A failure on the model's side of the call: the room says only that calling
#: the model went wrong (`model_error_notice`).
MODEL_ERROR_CODE = "model_error"
#: The turn ran on a model connection the project's own people added, and its
#: account has no money left: theirs to top up (`own_out_of_credit_notice`).
OWN_OUT_OF_CREDIT_CODE = "own_out_of_credit"

#: What Claude Code prints when calling the model failed: the service was busy
#: or unreachable, a quota ran out, or the platform's own way to the model
#: (its metering proxy, the subscription's egress and accounts) did not carry
#: the call. Whichever it was, the person reads one line.
_MODEL_SIDE_CLI: frozenset[str] = frozenset(
    {
        PROVIDER_UNREACHABLE_CODE,
        PROVIDER_OVERLOADED_CODE,
        SUBSCRIPTION_EGRESS_OFFLINE_CODE,
        CONTROL_PLANE_UNAVAILABLE_CODE,
        SUBSCRIPTION_ACCOUNTS_COOLING_CODE,
        MODEL_LIMIT_REACHED_CODE,
    }
)

#: Of those, the ones that are the platform's to fix on a call it carried: its
#: own way to the model and the limits on its own accounts and keys. The room
#: hears the same line; the operators are told (`intake.hooks`).
_PLATFORM_SIDE_CLI: frozenset[str] = frozenset(
    {
        SUBSCRIPTION_EGRESS_OFFLINE_CODE,
        CONTROL_PLANE_UNAVAILABLE_CODE,
        SUBSCRIPTION_ACCOUNTS_COOLING_CODE,
        MODEL_LIMIT_REACHED_CODE,
    }
)

#: The platform's own account with the model provider has no money.
PROVIDER_OUT_OF_CREDIT = "provider_out_of_credit"


def model_error_notice(text: str | None) -> tuple[str, dict]:
    """The room's line for a turn that failed because calling the model went
    wrong — a busy or unreachable service, a quota, the platform's own credit
    or way to the model, a wait the platform gave up on. One sentence for all
    of them, with a retry: the reader cannot act on which one it was, and the
    ones that are the platform's go to its operators instead. The service's
    own words stay one click away, unedited but for credentials."""
    said = scrub_secrets((text or "").strip())
    return say("turnFailedModelError"), {
        **notice(
            EVENT_TURN_FAILED,
            severity=SEVERITY_ERROR,
            who=WHO_HUMAN,
            detail=said or None,
            detail_label=say("labelServiceWords") if said else None,
            retryable=True,
        ),
        "code": MODEL_ERROR_CODE,
    }


def platform_side_cause(text: str | None, *, own: bool = False) -> str | None:
    """Which of the platform's own causes made this call fail, for its
    operators; None when the cause is not the platform's (a busy provider, the
    turn's own words) or the call went out on the project's own model
    connection (``own``), which the platform does not carry."""
    said = (text or "").strip()
    if own:
        return None
    if is_provider_out_of_credit(said):
        return PROVIDER_OUT_OF_CREDIT
    cli = classify_cli_notice(scrub_secrets(said))
    return cli if cli in _PLATFORM_SIDE_CLI else None


def _turn_failure_notice(
    text: str,
    code: str | None,
    *,
    log: str | None = None,
    own: bool = False,
    service: str | None = None,
) -> tuple[str, dict]:
    """A failed turn's room line and the structured card behind it.

    Every failure gets one, classified or not. 平台提示统一契约: the room line
    is ONE line and the service's own words go in `meta.detail` — pasting them
    into the line is what made a plain system row run to seven or eight, and
    only a classified failure used to get meta at all, so the three most common
    ones (座位限流 / 余额用尽 / HTTP 错误) carried no structure whatsoever.

    An unclassified failure says only that the turn did not finish, with a
    retry: the service's own words are often English and say nothing to the
    people in the room. They are kept whole, one click away, under a label that
    says they are the service's words — not dropped: the reason buried behind
    a misleading label sent a whole room hunting a mystery bug twice in one
    night (2026-08-16, topic ee17b136 — the real text was the delivery timeout
    all along). The platform's own one-line sentences (a runner that stopped
    answering) are already written for people and stay on the line.

    A failure on the model's side of the call — Claude Code's own refusal
    naming a busy or unreachable service, a quota, the platform's account
    running dry — says only that (`model_error_notice`). One the project's own
    model connection ran dry on names that connection (``service``) and where
    to change it.
    """
    if code == RUNNER_REPLACED_CODE and isinstance(text, NoticeText):
        # The platform's own upgrade cut the turn short. When the platform
        # starts it again by itself the room hears nothing (`intake.hooks`);
        # this line is for the case it did not, so it says the work is safe and one retry carries on — one line,
        # not 「这次没做完」 wrapped around a sentence that already says so.
        return text, notice(
            EVENT_TURN_FAILED, severity=SEVERITY_WARN, who=WHO_HUMAN, retryable=True
        )
    failure = classify_platform_failure(text, code=code)
    if failure is not None:
        if failure.code in SESSION_START_CODES and text.strip():
            # The sentence it was raised with can name what was found.
            line = text if isinstance(text, NoticeText) else text.strip()
            return line, _with_log(failure.meta, log)
        return failure.content, _with_log(failure.meta, log)
    detail = (text or "").strip()
    out_of_credit = is_provider_out_of_credit(detail)
    if out_of_credit and own:
        # The turn ran on the team's or member's own model connection
        # (`gateway_usage.OWN_ROUTE`): the account that ran dry is theirs.
        return own_out_of_credit_notice(detail, service)
    if out_of_credit or classify_cli_notice(scrub_secrets(detail)) in _MODEL_SIDE_CLI:
        # The platform's own account with a model provider has no money, or
        # Claude Code says calling the model failed. Nothing here is the
        # reader's to fix; a retry works once it passes.
        return model_error_notice(detail)
    if said := cli_notice(scrub_secrets(detail)):
        # Claude Code's own one-line refusal ("API Error: ..."): the same card
        # it gets when it is printed as a reply, whose line names the cause.
        return said
    who = WHO_HUMAN
    if is_request_refused(detail):
        # The same conversation goes out again on a retry and is refused the
        # same way, so a retry is not offered.
        line = say("turnFailedService")
        hint = say("turnFailedRequestRefusedHint")
        retryable = False
    else:
        first = detail.splitlines()[0].strip() if detail else ""
        line = (
            # The platform's own one-line sentence: nested whole, so it keeps
            # its key.
            say("turnFailedWith", reason=text)
            if isinstance(text, NoticeText) and first == text
            else say("turnFailedService")
        )
        hint = say("turnFailedRetryLater")
        retryable = True
    return line, notice(
        EVENT_TURN_FAILED,
        severity=SEVERITY_ERROR,
        who=who,
        # 原话是唯一的一份——它没有第二个副本可以「去别处看」，所以原样收进
        # detail，不截、不摘要；只把凭据遮成 ***：它会存进对话。
        detail=(
            say("hintAndServiceWords", hint=hint, said=scrub_secrets(detail))
            if detail
            else hint
        ),
        detail_label=say("labelDetails"),
        retryable=retryable,
    )


def own_out_of_credit_notice(text: str, service: str | None) -> tuple[str, dict]:
    """The room's line for a turn whose own model connection ran out of money:
    which connection (``service``, its name as the project's settings show it),
    where to change it, and a retry for after the top-up."""
    said = scrub_secrets((text or "").strip())
    return say("turnFailedOwnBalance", service=service or "API"), {
        **notice(
            EVENT_TURN_FAILED,
            severity=SEVERITY_ERROR,
            who=WHO_HUMAN,
            detail=said or None,
            detail_label=say("labelServiceWords") if said else None,
            retryable=True,
        ),
        "code": OWN_OUT_OF_CREDIT_CODE,
    }


def _with_log(meta: dict, log: str | None) -> dict:
    """What the failing process printed, as a failed row's error, scrubbed
    of credentials. The turn's run record keeps both fields, for 现场; the
    room's notice is sent without them (`intake.hooks`)."""
    if not log or not log.strip():
        return meta
    return {**meta, "failed": True, "error": scrub_secrets(log.strip())}
