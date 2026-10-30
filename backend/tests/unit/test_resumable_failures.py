"""Which failed turns the platform starts again by itself.

The failure texts are the ones dev recorded on failed turns (2026-10-03..17),
in the form the hook intake receives them: Claude Code's line with the
terminal reason after it. A failure that passes with time resumes; one that a
retry repeats exactly (an invalid request, a refused credential, a spent
balance, a quota with no reset) does not.
"""

import pytest

from app.domain.agent.resumable_failures import (
    EGRESS_OFFLINE,
    OVERLOADED,
    QUOTA_RESET,
    RATE_LIMITED,
    STREAM_ENDED,
    SUBSCRIPTION_COOLING,
    UNREACHABLE,
    classify,
)


@pytest.mark.parametrize(
    ("said", "kind"),
    [
        (
            "API Error: Stream ended without receiving any events（unknown）",
            STREAM_ENDED,
        ),
        (
            "API Error: Stream idle timeout - no chunks received（unknown）",
            STREAM_ENDED,
        ),
        (
            "API Error: Server is temporarily limiting requests (not your usage "
            "limit) · Rate limited（rate_limit）",
            RATE_LIMITED,
        ),
        (
            "API Error: Server is temporarily limiting requests (not your usage "
            "limit) · cheese: all Claude accounts are cooling down（rate_limit）",
            SUBSCRIPTION_COOLING,
        ),
        (
            "API Error: 502 Bad Gateway. This is a server-side issue, usually "
            "temporary — try again in a moment.（server_error）",
            OVERLOADED,
        ),
        (
            "API Error: litellm.APIError: litellm.InternalServerError: "
            "InternalServerError: OpenAIException - Our servers are currently "
            "overloaded. Please try again later.（unknown）",
            OVERLOADED,
        ),
        (
            "API Error: 503 cheese: the subscription egress is offline, so no "
            "model call was made.（server_error）",
            EGRESS_OFFLINE,
        ),
        ("API Error: Connection dropped (ECONNRESET)（server_error）", UNREACHABLE),
        (
            "API Error: litellm.APIError: litellm.MidStreamFallbackError: "
            "litellm.APIConnectionError: Vertex_ai_betaException - Cannot connect "
            "to host generativelanguage.googleapis.com:443（unknown）",
            STREAM_ENDED,
        ),
        # The metering proxy's own 503 when its admission check cannot answer,
        # verbatim from 2026-10-27 11:00. It said "billing", was read as a
        # spent balance, and a spent balance is a state no wait changes — so
        # the seat sat idle until a person retried. It is a 503: wait and ask.
        (
            "API Error: 503 cheese: task billing attribution is temporarily "
            "unavailable; retry shortly. This is a server-side issue, usually "
            "temporary — try again in a moment. If it persists, check "
            "https://status.claude.com.（server_error）",
            OVERLOADED,
        ),
    ],
)
def test_a_passing_failure_resumes(said, kind):
    found = classify(said)
    assert found is not None and found.kind == kind


@pytest.mark.parametrize(
    "said",
    [
        # The gateway's refusal of a spent or suspended account, inside Claude
        # Code's "temporarily limiting" wording.
        "API Error: Server is temporarily limiting requests (not your usage limit)"
        ' · litellm.RateLimitError: AnthropicException - {"error":{"message":"Your '
        'account org-1 is suspended due to insufficient balance"}}',
        "API Error: Server is temporarily limiting requests (not your usage limit)"
        ' · litellm.RateLimitError: Hosted_vllmException - {"type":"error",'
        '"error":{"type":"GoUsageLimitError","message":"Go usage limit exceeded"}}',
        "API Error: Server is temporarily limiting requests (not your usage limit)"
        ' · litellm.RateLimitError: AnthropicException - {"error": {"code": "1113",'
        ' "message": "quota exhausted", "type": "limitation"}}',
        # Volcengine's paused account and the gateway's answer once no row of
        # the model has credit (deploy/gateway/provider_credit.py).
        "API Error: Server is temporarily limiting requests (not your usage limit)"
        ' · litellm.RateLimitError: {"error":{"code":"SetLimitExceeded","message":'
        '"Your account [2100] has reached the set usage limit for this model, and '
        'the model service has been paused."}}',
        'API Error: 402 {"type":"error","error":{"type":"api_error","message":'
        "\"cheese_upstream_credit_exhausted: the platform's own account with every "
        'provider serving deepseek-flash is out of credit."}}',
        # Our gateway's lifetime cap on an upstream key. LiteLLM words it as a
        # rate limit, and the platform used to resume it until the streak ran
        # out — six rounds of ten harness retries, all refused (2026-10-21).
        "API Error: Server is temporarily limiting requests (not your usage limit)"
        " · gemini/gemini-4-argon has reached its spend cap: $1005.08 of $1000.00."
        " Received Model Group=gemini-4-argon\nAvailable Model Group Fallbacks=None"
        "（rate_limit）",
        # The same conversation goes out again and is refused again.
        "API Error: 400 litellm.BadRequestError: DeepseekException - "
        '{"error":{"message":"messages.3.content.0.tool_result.image[0]: You have '
        'uploaded an unsupported image."}}',
        "API Error: 401 Invalid API key · Please run /login",
        # Failures the platform cannot name are a person's to look at.
        "AI 服务拒绝了请求（error_during_execution）",
        "Claude Code session process exited",
        "[ede_diagnostic] result_type=user last_content_type=n/a "
        "stop_reason=null（aborted_streaming）",
        "",
    ],
)
def test_a_failure_no_wait_changes_does_not_resume(said):
    assert classify(said) is None


def test_a_status_the_session_reported_decides_without_the_words():
    assert classify("request failed", api_error_status=529).kind == OVERLOADED
    assert classify("request failed", api_error_status=429).kind == RATE_LIMITED
    assert classify("request failed", api_error_status=403) is None


def test_a_status_with_no_words_at_all_still_decides():
    """A session can report a 429 or a 529 and say nothing else; the status is
    still what happened."""
    assert classify("", api_error_status=429).kind == RATE_LIMITED
    assert classify("", api_error_status=529).kind == OVERLOADED
    assert classify("", api_error_status=503).kind == OVERLOADED
    assert classify("", api_error_status=400) is None


def test_a_wait_the_service_names_is_the_wait():
    found = classify("API Error: 429 rate limited, retry-after: 42")
    assert found.kind == RATE_LIMITED and found.wait_s == 42
    minutes = classify(
        "API Error: 429 Too Many Requests. Please retry after 5 minutes."
    )
    assert minutes.kind == RATE_LIMITED and minutes.wait_s == 5 * 60
    quota = classify(
        "You've hit your usage limit. Upgrade to Pro or try again in 2 days "
        "3 hours 5 minutes."
    )
    assert quota.kind == QUOTA_RESET
    assert quota.wait_s == 2 * 86400 + 3 * 3600 + 5 * 60
