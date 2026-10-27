"""Pure native failure and tool-action classification."""

from app.core.obs import scrub_secrets
from app.core.sentences import NoticeText, say
from app.domain.agent.cli_notices import cli_notice
from app.domain.agent.platform_failures import (
    SESSION_START_CODES,
    classify_platform_failure,
    is_request_refused,
)
from app.domain.notice_meta import (
    EVENT_TURN_FAILED,
    SEVERITY_ERROR,
    WHO_HUMAN,
    notice,
)

_OUT_OF_CREDIT_MARKERS = (
    "余额不足",
    "请充值",
    "insufficient balance",
    "insufficient_quota",
    "quota exceeded",
    "billing",
)


def is_out_of_credit(detail: str | None) -> bool:
    """The upstream refused because ITS account is out of credit.

    Not the team's credits (that is the ledger's refusal) — the balance of the
    service the platform connects to, which nobody in a project can top up.
    """
    return _is_out_of_credit(detail)


def _is_out_of_credit(detail: str | None) -> bool:
    if not detail:
        return False
    lowered = detail.lower()
    return any(m.lower() in lowered for m in _OUT_OF_CREDIT_MARKERS)


_TOOL_ACTION = {
    "cheese_notify": "notify",
}


def _turn_failure_notice(
    text: str, code: str | None, *, log: str | None = None
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
    failure = classify_platform_failure(text, code=code)
    if failure is not None:
        if failure.code in SESSION_START_CODES and text.strip():
            # The sentence it was raised with can name what was found.
            line = text if isinstance(text, NoticeText) else text.strip()
            return line, _with_log(failure.meta, log)
        return failure.content, _with_log(failure.meta, log)
    detail = (text or "").strip()
    if not _is_out_of_credit(detail) and (said := cli_notice(scrub_secrets(detail))):
        # Claude Code's own one-line refusal ("API Error: ..."): the same card
        # it gets when it is printed as a reply, whose line names the cause.
        return said
    if _is_out_of_credit(detail):
        # A spent balance is not a wait — no amount of retrying refills it, and
        # telling someone to try again later sends them into a loop that cannot
        # succeed. Say what actually has to happen.
        line = say("turnFailedOutOfCredit")
        hint = say("turnFailedOutOfCreditHint")
        retryable = False
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
        who=WHO_HUMAN,
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
