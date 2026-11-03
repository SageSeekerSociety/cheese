"""A message that reaches its reader long after it was sent says when it was
sent.

Messages held behind a turn that never ended, or an input nothing would settle,
reach the next prompt days late, in their order but without their time. Read
bare, a four-day-old question looks like one asked a moment ago, and the agent
answers it as if nothing had happened since.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.domain.agent.harness.prompt import prompt_line
from app.domain.block.models import BlockKind


def _message(sent_at: datetime):
    return SimpleNamespace(
        kind=BlockKind.message,
        author="alice",
        content="书签点了怎么又跳回底部？",
        meta=None,
        mime_type=None,
        created_at=sent_at,
    )


def test_a_message_sent_days_ago_carries_its_send_time_and_the_wait():
    sent_at = datetime.now(UTC) - timedelta(days=4, hours=3)

    line = prompt_line(_message(sent_at), embeds_images=False)

    assert line.startswith("[alice]: 书签点了怎么又跳回底部？")
    assert f"{sent_at:%Y-%m-%d %H:%M} UTC" in line, line
    assert "4 天" in line, line


def test_a_message_just_sent_reads_as_before():
    line = prompt_line(
        _message(datetime.now(UTC) - timedelta(minutes=2)), embeds_images=False
    )

    assert line == "[alice]: 书签点了怎么又跳回底部？"
