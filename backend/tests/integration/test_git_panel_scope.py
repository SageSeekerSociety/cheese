"""Git 面板显示的是「本话题」的提交 (P1-7).

The panel asked the project-level endpoints, which is wrong in both directions:
before 采纳 a topic's commits live only on its branch, so the panel showed NONE
of this topic's work; after 采纳 the base carries every topic's history, so the
panel showed OTHER topics' commits as if they were this one's.

These tests drive the HTTP endpoints the panel calls.
"""

import uuid

from app.domain.workspace import service as ws


def _mkproject(client) -> uuid.UUID:
    resp = client.post(
        "/api/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()
    return uuid.UUID(resp["data"]["id"])


def _owner(client) -> dict[str, str]:
    from tests.integration.test_connector_viewer import _login

    return {"Authorization": f"Bearer {_login(client, 'alice')}"}


def _turn(pid: uuid.UUID, tid: uuid.UUID, path: str, content: str, msg: str) -> None:
    """A turn's edits, snapshotted as the topic's own commit."""
    wt = ws.topic_worktree(pid, tid)
    target = wt / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    ws.snapshot_worktree(pid, tid, message=msg)


def _log(client, pid, topic=None) -> list[dict]:
    params = {"topic": str(topic)} if topic else {}
    return client.get(
        f"/api/projects/{pid}/git/log", params=params, headers=_owner(client)
    ).json()["data"]["data"]


def _diff(client, pid, topic=None) -> str:
    params = {"topic": str(topic)} if topic else {}
    return client.get(
        f"/api/projects/{pid}/git/diff", params=params, headers=_owner(client)
    ).json()["data"]["diff"]


def test_topic_commits_visible_before_accept(client):
    """The panel's main complaint: 采纳 前一条提交都不显示。"""
    pid = _mkproject(client)
    tid = uuid.uuid4()
    _turn(pid, tid, "a.py", "print(1)\n", "加了 a.py")

    messages = [c["message"] for c in _log(client, pid, topic=tid)]
    assert "加了 a.py" in messages
    assert "print(1)" in _diff(client, pid, topic=tid)


def test_topic_log_excludes_other_topics_commits(client):
    """After one topic is 采纳'd, its commits are on the base — and must not
    show up as another topic's work."""
    pid = _mkproject(client)
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    _turn(pid, theirs, "theirs.py", "x = 1\n", "别的话题的提交")
    assert ws.merge_topic(pid, theirs)["merged"] is True
    _turn(pid, mine, "mine.py", "y = 2\n", "我的提交")

    messages = [c["message"] for c in _log(client, pid, topic=mine)]
    assert "我的提交" in messages
    assert "别的话题的提交" not in messages


def test_topic_with_no_commits_shows_none_not_the_projects(client):
    """An untouched topic has no history of its own — and must not borrow the
    project's, which is what made the panel look busy on a fresh topic."""
    pid = _mkproject(client)
    busy, fresh = uuid.uuid4(), uuid.uuid4()
    _turn(pid, busy, "busy.py", "z = 3\n", "主干上的提交")
    assert ws.merge_topic(pid, busy)["merged"] is True

    assert _log(client, pid, topic=fresh) == []
    assert _diff(client, pid, topic=fresh) == ""


def test_project_log_still_available_without_a_topic(client):
    """The project-level view is unchanged — it is simply not what a topic
    panel asks for."""
    pid = _mkproject(client)
    tid = uuid.uuid4()
    _turn(pid, tid, "c.py", "w = 4\n", "会被采纳的提交")
    assert ws.merge_topic(pid, tid)["merged"] is True

    assert len(_log(client, pid)) >= 1
