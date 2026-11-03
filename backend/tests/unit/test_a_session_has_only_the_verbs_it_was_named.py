"""A session may be handed only some of an object's verbs: a document's 芝士
reads and edits its document (`cheese_doc.get`, `.edit`, `.set`) but makes no
new one and lists none of the project's. The tool it is offered lists only
those verbs, and a call to another is refused before it reaches the platform,
told the ones it has."""

import asyncio

from app.domain.agent.harness.pi import catalog
from app.domain.agent.harness.pi import runner as pi_runner
from app.domain.agent.harness.pi.runner import Runner


def _document_session(tmp_path, monkeypatch, ran):
    def run(tool, arguments, host, *, delegated=False):
        ran.append((tool, arguments, delegated))
        return "好"

    monkeypatch.setattr(pi_runner.catalog, "run_platform_tool", run)
    session = Runner(tmp_path / "pi")
    session.tools = {"names": ["cheese_doc.get", "cheese_doc.edit", "cheese_doc.set"]}
    session.platform_token = "token"
    return session


def test_the_tool_it_is_offered_lists_only_its_verbs():
    [doc] = catalog.schemas_of(["cheese_doc.get", "cheese_doc.edit", "cheese_doc.set"])

    assert doc["name"] == "cheese_doc"
    verbs = doc["inputSchema"]["properties"]["action"]["enum"]
    assert sorted(verbs) == ["edit", "get", "set"]
    assert "title" not in doc["inputSchema"]["properties"], "only `new` takes one"


def test_a_verb_it_was_not_named_is_refused(tmp_path, monkeypatch):
    ran: list = []
    session = _document_session(tmp_path, monkeypatch, ran)

    made = asyncio.run(
        session.run_cli("cheese_doc", {"action": "new", "title": "另一份"}, None, "c1")
    )
    read = asyncio.run(session.run_cli("cheese_doc", {"action": "get"}, None, "c2"))

    assert made["status"] == 1
    assert made["stderr"] == "cheese_doc 要 action：get / edit / set"
    assert read["status"] == 0
    assert ran == [("cheese_doc", {"action": "get"}, True)]
