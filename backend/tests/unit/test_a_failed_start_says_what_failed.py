"""A run that ends before it asks the model anything is not the AI service
refusing it.

A room was told 「AI 服务拒绝了请求（error_during_execution）」, under 「服务原话」,
when Claude Code could not find the conversation it was asked to resume: no
request had gone to any AI service, and the build's own words for what went
wrong were dropped.
"""

from app.domain.agent.harness.claude_code.events import Assembler

# The record the pinned build printed for that failure, less its usage counters.
COULD_NOT_RESUME = {
    "type": "result",
    "subtype": "error_during_execution",
    "duration_ms": 0,
    "duration_api_ms": 0,
    "is_error": True,
    "num_turns": 0,
    "stop_reason": None,
    "session_id": "00000000-0000-4000-8000-000000000001",
    "total_cost_usd": 0,
    "errors": [
        "No conversation found with session ID: 00000000-0000-4000-8000-000000000001"
    ],
}


def test_the_room_reads_why_the_run_failed_not_that_the_service_refused():
    [ended] = Assembler({}, "session-1").accept(COULD_NOT_RESUME)

    assert ended.is_error
    assert "No conversation found with session ID" in ended.text
    assert "AI 服务拒绝" not in ended.text
