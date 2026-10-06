"""Tell the team's Feishu group that a report came in.

A report that nobody opens the feedback center to see is a report nobody
handles: the queue grew to a dozen untouched items with one person on the triage
roster. So every new report is pushed, once, to the group named by
`settings.feishu_feedback_webhook` (a Feishu custom bot's webhook). Empty — every
developer machine, every test, and a deployment that has not chosen a group —
means nothing is sent.

What goes out is decided by what the report's own visibility allows. A public
report carries its title, its author and the start of its text, because anyone
signed in can read those anyway. A private one carries its number and a link and
nothing else: the group is wider than the people `may_see` lets read it, and the
link answers 404 to everyone else.

Sent after the transaction commits, never before: a report whose insert rolls
back must not be announced.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import alerting
from app.core.config import settings
from app.domain.feedback.models import Feedback, FeedbackVisibility

KIND_LABELS = {"bug": "缺陷", "suggestion": "建议", "other": "其他"}
#: How much of the report's text the message carries; the link has the rest.
EXCERPT = 300


def text_of(row: Feedback) -> str:
    number = f"FB-{row.display_no}"
    link = f"{settings.frontend_url.rstrip('/')}/feedback/{row.id}"
    if row.visibility != FeedbackVisibility.public:
        return f"新反馈 {number}（私密，只有反馈管理员能打开）\n{link}"
    kind = KIND_LABELS.get(str(row.kind), str(row.kind))
    if row.submitted_by_handle:
        who = f"{row.author_handle}（AI 队友发现，{row.submitted_by_handle} 发出）"
    else:
        who = row.author_handle
    lines = [f"新反馈 {number}（{kind}）", row.title, f"提交：{who}"]
    body = (row.problem or "").strip()
    if body and body != row.title:
        lines.append(body if len(body) <= EXCERPT else body[:EXCERPT] + "…")
    lines.append(link)
    return "\n".join(lines)


def after_commit(session: AsyncSession, row: Feedback) -> None:
    """Announce `row` once `session` commits. A no-op with no group configured."""
    url = settings.feishu_feedback_webhook.strip()
    if not url:
        return
    text = text_of(row)
    event.listen(
        session.sync_session,
        "after_commit",
        lambda _s: alerting.post(url, text),
        once=True,
    )
