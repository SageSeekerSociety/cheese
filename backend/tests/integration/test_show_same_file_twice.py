"""Showing the same unchanged file twice in a row leaves one card, not two.

An agent ran `cheese show` on one file twice seconds apart, once from the task's
directory and once from the room's, and the conversation got two identical
cards. A file whose content changed is a new version and still gets its own.
"""

from datetime import timedelta

import pytest

from app.core.config import settings
from app.domain.block import shown
from tests.integration.conftest import post_project, session_auth_headers


@pytest.fixture(autouse=True)
def content_domain(monkeypatch):
    monkeypatch.setattr(settings, "sites_domain", "content.example.com")
    monkeypatch.setattr(settings, "sites_scheme", "https")


def _room(client) -> str:
    p = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    return client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]["id"]


def _show(client, tid: str, content: str, path: str = "compare.html") -> dict:
    r = client.post(
        f"/topics/{tid}/shown",
        headers=session_auth_headers("alice"),
        json={"path": path, "content": content},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _cards(client, tid: str) -> list[dict]:
    blocks = client.get(
        f"/topics/{tid}/blocks",
        params={"kind": "artifact"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    return blocks["data"] if isinstance(blocks, dict) else blocks


def test_the_same_unchanged_file_shown_again_keeps_one_card(client):
    tid = _room(client)
    first = _show(client, tid, "<h1>before / after</h1>")
    again = _show(client, tid, "<h1>before / after</h1>")

    assert again["id"] == first["id"]
    assert again["already_shown"] is True
    assert [c["id"] for c in _cards(client, tid)] == [first["id"]]


def test_a_changed_file_gets_a_new_card(client):
    tid = _room(client)
    first = _show(client, tid, "<h1>v1</h1>")
    second = _show(client, tid, "<h1>v2</h1>")

    assert second["id"] != first["id"]
    assert "already_shown" not in second
    assert len(_cards(client, tid)) == 2


def test_another_file_in_between_makes_the_repeat_a_new_card(client):
    tid = _room(client)
    _show(client, tid, "<h1>a</h1>", path="a.html")
    _show(client, tid, "<h1>b</h1>", path="b.html")
    _show(client, tid, "<h1>a</h1>", path="a.html")

    assert [c["content"] for c in _cards(client, tid)] == [
        "a.html",
        "b.html",
        "a.html",
    ]


def test_the_same_file_shown_again_later_gets_a_new_card(client, monkeypatch):
    tid = _room(client)
    first = _show(client, tid, "<h1>same</h1>")
    monkeypatch.setattr(shown, "SHOWN_AGAIN_WINDOW", timedelta(0))
    again = _show(client, tid, "<h1>same</h1>")

    assert again["id"] != first["id"]
    assert len(_cards(client, tid)) == 2
