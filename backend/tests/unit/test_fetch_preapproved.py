"""A person's own 芝士 reads the documentation sites Claude Code reads without
asking, with no look by its gate; any other page is still looked at."""

import asyncio

import pytest

from app.api.routes import assistant_agent
from app.domain.assistant import gate
from app.domain.assistant.models import AssistantRules
from app.domain.fetch.preapproved import preapproved


@pytest.mark.parametrize(
    "url",
    [
        "https://docs.python.org/3/library/asyncio.html",
        "https://DOCS.PYTHON.ORG/3/",
        "https://react.dev/reference/react?q=anything",
        "https://go.dev/doc/effective_go",
        "https://huggingface.co/docs/transformers",
        "https://github.com/anthropics/claude-code/issues/1",
        "https://github.com/anthropic-experimental/repo",
        "https://github.com/anthropic-experimental/repo/blob/main/README.md",
        "https://raw.githubusercontent.com/anthropics/repo/refs/heads/main/a.md",
    ],
)
def test_a_documentation_site_is_preapproved(url):
    assert preapproved(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/?d=secret",
        # A whole host covers that host, not its subdomains.
        "https://evil.docs.python.org/",
        "https://docs.python.org.evil.com/",
        # A path entry covers that path only.
        "https://go.dev/play/",
        "https://huggingface.co/someone/model",
        "https://github.com/someone/repo",
        "https://github.com/anthropicsx/repo",
        "https://github.com/anthropics/../someone/repo",
        "https://github.com/anthropics%2f..%2fsomeone/repo",
        # Off the main branch, only Anthropic's own GitHub organization counts.
        "https://github.com/anthropic-experimental/repo/blob/dev/x",
        "https://raw.githubusercontent.com/anthropics/repo/refs/heads/dev/a.md",
        "not a url",
    ],
)
def test_any_other_page_is_not(url):
    assert not preapproved(url)


def _verdict(monkeypatch, url):
    async def no_rules(session, user_id):
        return AssistantRules(user_id=user_id, ask=[], allow_text="", ask_text="")

    async def no_model(session):
        return None

    monkeypatch.setattr(gate, "rules_of", no_rules)
    monkeypatch.setattr(gate, "gatekeeper", no_model)
    plan = assistant_agent._fetch(assistant_agent.ActIn(kind="fetch", url=url))
    return asyncio.run(
        gate.judge(None, user_id=1, conversation_id=None, action=plan.action)  # type: ignore[arg-type]
    )


def test_reading_a_documentation_site_needs_no_look(monkeypatch):
    verdict = _verdict(monkeypatch, "https://docs.python.org/3/")

    assert (verdict.allowed, verdict.stage) == (True, "low")


def test_reading_any_other_page_is_looked_at(monkeypatch):
    # With no model to look, the gate asks rather than lets it through.
    verdict = _verdict(monkeypatch, "https://example.com/?d=secret")

    assert (verdict.allowed, verdict.stage) == (False, "error")
