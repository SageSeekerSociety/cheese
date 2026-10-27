"""A person's 芝士 weighs a call that reaches others before it runs it, the way
Claude Code checks a tool call before running it: its harness asks the gate
(`run_platform_tool`), and the tool, once taken, acts as the person. These run
the tool against this app on the credential 芝士 acts with, so what is checked
is what the channel shows."""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from app.domain.agent.harness.pi import catalog
from app.domain.assistant import gate
from tests.integration.conftest import session_auth_headers
from tests.integration.test_assistant_agent import (
    _acting,
    _asked_in,
    _Gatekeeper,
    _names,
)
from tests.integration.test_message_edit import _room

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_acts_for_the_person", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load()


class PersonHost:
    """The session host of a person's 芝士, on the credential it acts with."""

    def __init__(self, client, token):
        self.client = client
        self.environ = {"CHEESE_TOPIC": "", "CHEESE_PROJECT": ""}
        self.headers = {"Authorization": f"Bearer {token}"}
        self.doc_versions: dict = {}

    def request(self, plan):
        self.client.headers.pop("Authorization", None)
        response = self.client.request(
            plan["method"], plan["path"], json=plan.get("body"), headers=self.headers
        )
        if not 200 <= response.status_code < 300:
            raise cheese.PlatformHTTPError(response.status_code, response.text)
        return response.json()


def _said_in(client, room: str) -> list[tuple[str, str]]:
    got = client.get(f"/topics/{room}/blocks", headers=session_auth_headers("alice"))
    assert got.status_code == 200, got.text
    return [
        (b.get("author"), (b.get("content") or {}).get("text") or "")
        for b in got.json()["data"]["data"]
    ]


def _send(client, monkeypatch, verdict: str, said: str):
    room, _ = _room(client)
    slug, number = _names(client, room)
    token = _acting(client, "alice", _asked_in(client, "alice", said))

    async def looks(session):
        return _Gatekeeper(verdict, verdict)

    monkeypatch.setattr(gate, "gatekeeper", looks)
    told = cheese.run_platform_tool(
        "chat_send",
        {"where": f"{slug}/channels/{number}", "content": "周五组会改到下午三点"},
        PersonHost(client, token),
        delegated=True,
    )
    return room, told


def test_a_message_the_gate_takes_is_sent_as_the_person(client, monkeypatch):
    room, _ = _send(client, monkeypatch, "放", "跟大家说周五组会改到下午三点")

    assert ("alice", "周五组会改到下午三点") in _said_in(client, room)


def test_a_message_the_gate_stops_is_not_sent_and_cheese_is_told_why(
    client, monkeypatch
):
    room, told = _send(client, monkeypatch, "拦", "帮我看看综合频道")

    assert all(text != "周五组会改到下午三点" for _, text in _said_in(client, room))
    assert "没有执行" in told


def test_a_call_that_does_not_say_what_it_does_is_never_handed_to_a_persons_cheese():
    """`platform_request` reaches any route; nothing about it says whether a
    call reaches others, so a session acting for a person does not start
    holding it."""
    with pytest.raises(ValueError, match="platform_request"):
        catalog.schemas_of(["cheese_read", "platform_request"])
