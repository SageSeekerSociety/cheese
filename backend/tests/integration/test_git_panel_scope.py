"""Git 面板显示的是「本话题」的提交 (P1-7).

The panel asked the project-level endpoints, which is wrong in both directions:
before 采纳 a topic's commits live only on its branch, so the panel showed NONE
of this topic's work; after 采纳 the base carries every topic's history, so the
panel showed OTHER topics' commits as if they were this one's.

These tests drive the HTTP endpoints the panel calls.
"""

import asyncio
import uuid

from app.domain.agent_session.repositories import AgentSessionRepository
from app.domain.identity.handles import CHEESE_HANDLE
from app.domain.workspace import service as ws
from tests.machine_work import machine_commits

_MSG = "chore: land the branch under test\n\nRequested-by: alice"


def _mkproject(client) -> uuid.UUID:
    resp = client.post("/projects", json={"name": "P", "owner_handle": "alice"}).json()
    return uuid.UUID(resp["data"]["id"])


def _owner(client) -> dict[str, str]:
    from tests.integration.test_connector_viewer import _login

    return {"Authorization": f"Bearer {_login(client, 'alice')}"}


def _turn(pid: uuid.UUID, tid: uuid.UUID, path: str, content: str, msg: str) -> None:
    """A turn's edits, committed and pushed by the machine that made them."""
    machine_commits(pid, tid, {path: content}, msg)


def _log(client, pid, topic=None) -> list[dict]:
    params = {"topic": str(topic)} if topic else {}
    return client.get(
        f"/projects/{pid}/git/log", params=params, headers=_owner(client)
    ).json()["data"]["data"]


def _diff(client, pid, topic=None) -> str:
    params = {"topic": str(topic)} if topic else {}
    return client.get(
        f"/projects/{pid}/git/diff", params=params, headers=_owner(client)
    ).json()["data"]["diff"]


def _mktopic(client, pid: uuid.UUID) -> uuid.UUID:
    r = client.post("/topics", json={"project_id": str(pid), "title": "做一个东西"})
    assert r.status_code == 200
    return uuid.UUID(r.json()["data"]["id"])


def _seed_session(client, topic_id: uuid.UUID, session_id: str) -> None:
    """Mark the topic as having run — what the first turn does for real."""

    async def _run() -> None:
        async with client.test_factory() as s:
            await AgentSessionRepository(s).save(
                topic_id=topic_id,
                agent_handle=CHEESE_HANDLE,
                resume_token=session_id,
            )
            await s.commit()

    asyncio.run(_run())


def _summary(client, pid, topic) -> dict:
    return client.get(
        f"/projects/{pid}/topics/{topic}/work-summary", headers=_owner(client)
    ).json()["data"]


def test_topic_commits_visible_before_accept(client):
    """The panel's main complaint: 采纳 前一条提交都不显示。"""
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _turn(pid, tid, "a.py", "print(1)\n", "加了 a.py")

    messages = [c["message"] for c in _log(client, pid, topic=tid)]
    assert "加了 a.py" in messages
    assert "print(1)" in _diff(client, pid, topic=tid)


def test_topic_log_excludes_other_topics_commits(client):
    """After one topic is 采纳'd, its commits are on the base — and must not
    show up as another topic's work."""
    pid = _mkproject(client)
    mine, theirs = _mktopic(client, pid), _mktopic(client, pid)
    _turn(pid, theirs, "theirs.py", "x = 1\n", "别的话题的提交")
    assert ws.merge_topic(pid, theirs, message=_MSG)["merged"] is True
    _turn(pid, mine, "mine.py", "y = 2\n", "我的提交")

    messages = [c["message"] for c in _log(client, pid, topic=mine)]
    assert "我的提交" in messages
    assert "别的话题的提交" not in messages


def test_topic_with_no_commits_shows_none_not_the_projects(client):
    """An untouched topic has no history of its own — and must not borrow the
    project's, which is what made the panel look busy on a fresh topic."""
    pid = _mkproject(client)
    busy, fresh = _mktopic(client, pid), _mktopic(client, pid)
    _turn(pid, busy, "busy.py", "z = 3\n", "主干上的提交")
    assert ws.merge_topic(pid, busy, message=_MSG)["merged"] is True

    assert _log(client, pid, topic=fresh) == []
    assert _diff(client, pid, topic=fresh) == ""


def test_work_summary_lists_the_same_range_the_diff_renders(client):
    """The panel decides whether to OFFER the 改动 tab, and what count to put on
    it, from this list — so it has to answer about the same range as the diff."""
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _turn(pid, tid, "src/a.py", "print(1)\n", "加了 a.py")
    _turn(pid, tid, "src/b.py", "print(2)\n", "加了 b.py")

    assert sorted(_summary(client, pid, tid)["changed_files"]) == [
        "src/a.py",
        "src/b.py",
    ]


def test_work_summary_excludes_other_topics_work(client):
    """Same trap /git/log documents: after another topic is 采纳'd its files are
    on the base, and counting them here would put a badge on an idle topic."""
    pid = _mkproject(client)
    mine, theirs = _mktopic(client, pid), _mktopic(client, pid)
    _turn(pid, theirs, "theirs.py", "x = 1\n", "别的话题的提交")
    assert ws.merge_topic(pid, theirs, message=_MSG)["merged"] is True
    _turn(pid, mine, "mine.py", "y = 2\n", "我的提交")

    assert _summary(client, pid, mine)["changed_files"] == ["mine.py"]


def test_work_summary_empty_for_a_topic_that_never_wrote(client):
    """No branch yet — the tab must not be offered, so this cannot raise."""
    pid = _mkproject(client)
    summary = _summary(client, pid, _mktopic(client, pid))
    assert summary["changed_files"] == []
    assert summary["has_run"] is False


def test_work_summary_reports_a_topic_that_has_run(client):
    """现场 shows what 芝士 did. A room where only people talked has none, and a
    topic that ran has one even when the turn changed no files."""
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _seed_session(client, tid, "sess-1")

    summary = _summary(client, pid, tid)
    assert summary["has_run"] is True
    assert summary["changed_files"] == []


def test_project_log_still_available_without_a_topic(client):
    """The project-level view is unchanged — it is simply not what a topic
    panel asks for."""
    pid = _mkproject(client)
    tid = _mktopic(client, pid)
    _turn(pid, tid, "c.py", "w = 4\n", "会被采纳的提交")
    assert ws.merge_topic(pid, tid, message=_MSG)["merged"] is True

    assert len(_log(client, pid)) >= 1
