"""A comment thread's reader sees what the agent is doing — reading the
document, editing it — while it answers. The document is one tool with verbs
(`cheese_doc`), so which one it is is known only once the call's `action` is
written; the reader is told again then."""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.document import thread
from app.domain.agent.session_host.answer import Tool, ToolArgs, ToolDone


@pytest.fixture
def told(monkeypatch):
    frames: list[tuple[str, str | None]] = []

    async def tell(subject, thread_id, state, tool=None):
        frames.append((state, tool))

    monkeypatch.setattr(thread, "_tell", tell)
    monkeypatch.setattr(
        thread.Asked, "of", staticmethod(lambda data: SimpleNamespace(subject=None))
    )
    return frames


@pytest.mark.anyio
async def test_the_verb_is_told_once_its_action_is_written(told):
    replies = thread.Replies(None, None)  # type: ignore[arg-type]
    asked = SimpleNamespace(data={}, key=str(uuid.uuid4()))

    await replies.took(asked, Tool("cheese_doc", "c1"))
    await replies.took(asked, ToolArgs("c1", '{"act', 0))
    await replies.took(asked, ToolArgs("c1", 'ion": "edit", "edits": [', 5))
    await replies.took(asked, ToolArgs("c1", '{"old": "a"}]}', 30))
    await replies.took(asked, ToolDone("c1"))

    assert told == [("working", "cheese_doc"), ("working", "cheese_doc.edit")]


@pytest.mark.anyio
async def test_a_tool_without_verbs_is_told_once(told):
    replies = thread.Replies(None, None)  # type: ignore[arg-type]
    asked = SimpleNamespace(data={}, key=str(uuid.uuid4()))

    await replies.took(asked, Tool("cheese_project_search", "c2"))
    await replies.took(asked, ToolArgs("c2", '{"query": "范围"}', 0))
    await replies.took(asked, ToolDone("c2"))

    assert told == [("working", "cheese_project_search")]
