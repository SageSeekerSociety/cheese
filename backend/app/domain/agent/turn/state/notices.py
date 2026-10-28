"""Pure native failure and tool-action classification."""

from app.core.obs import scrub_secrets
from app.core.sentences import NoticeText, say
from app.domain.agent.cli_notices import cli_notice
from app.domain.agent.platform_failures import (
    RUNNER_REPLACED_CODE,
    SESSION_START_CODES,
    classify_platform_failure,
    is_provider_out_of_credit,
    is_request_refused,
)
from app.domain.notice_meta import (
    EVENT_TURN_FAILED,
    SEVERITY_ERROR,
    SEVERITY_WARN,
    WHO_HUMAN,
    WHO_PLATFORM,
    notice,
)

_TOOL_ACTION = {
    "cheese_notify": "notify",
}


def _turn_failure_notice(
    text: str, code: str | None, *, log: str | None = None, own: bool = False
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
    """
    if code == RUNNER_REPLACED_CODE and isinstance(text, NoticeText):
        # The platform's own upgrade cut the turn short. When the platform
        # starts it again by itself the room hears that instead
        # (`turnResumeWaitRunnerReplaced`); this line is for the case it did
        # not, so it says the work is safe and one retry carries on — one line,
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
    if not out_of_credit and (said := cli_notice(scrub_secrets(detail))):
        # Claude Code's own one-line refusal ("API Error: ..."): the same card
        # it gets when it is printed as a reply, whose line names the cause.
        return said
    who = WHO_HUMAN
    if out_of_credit and own:
        # The turn ran on the team's or member's own model connection
        # (`gateway_usage.OWN_ROUTE`): the account that ran dry is theirs, and
        # nobody on the platform side was told.
        line = say("turnFailedOwnOutOfCredit")
        hint = say("turnFailedOwnOutOfCreditHint")
        retryable = False
    elif out_of_credit:
        # The platform's own account with a model provider has no money: only
        # its administrators can top it up, and the gateway has already told
        # them (deploy/gateway/provider_credit.py). Nothing here is the
        # reader's to pay for; a retry works once the account is topped up.
        line = say("turnFailedOutOfCredit")
        hint = say("turnFailedOutOfCreditHint")
        who = WHO_PLATFORM
        retryable = True
    elif is_request_refused(detail):
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


def _with_log(meta: dict, log: str | None) -> dict:
    """What the failing process printed, as a failed row's error, scrubbed
    of credentials. The turn's run record keeps both fields, for 现场; the
    room's notice is sent without them (`intake.hooks`)."""
    if not log or not log.strip():
        return meta
    return {**meta, "failed": True, "error": scrub_secrets(log.strip())}
