"""The baseline a room turn's change summary is measured against.

One commit walk per open task is a forge round trip, and the baseline is read in
the turn's tail, so the walks run at once — bounded, and each in its own session.
What the baseline answers with stays what it answered with when the walks ran one
after another: every open branch's recent commits, or nothing at all when one of
them cannot be read."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.room_events import _CHANGE_COMMIT_WALK, _known_commits
from app.domain.repository import forge_files
from app.domain.room_task import services as room_task_services

#: The rule, not a reading of the code: a channel's branches are walked at once,
#: and never more than this many at once — the turn's tail is not the only thing
#: in the process, and the pool is shared.
AT_MOST_AT_ONCE = 6


class _Session:
    """One ``async with sessions()`` block."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Sessions:
    """The session factory handed to ``_known_commits``: a stand-in per block,
    kept so a test can ask which walk was given which."""

    def __init__(self) -> None:
        self.opened: list[_Session] = []

    def __call__(self) -> _Session:
        session = _Session()
        self.opened.append(session)
        return session


def _room(monkeypatch, branch_ids: list[uuid.UUID], history) -> None:
    """Point a channel at ``branch_ids``, with ``history`` standing in for the
    commit walk of one of them."""

    async def open_branches_in_room(self, room_id):
        return [SimpleNamespace(id=branch_id) for branch_id in branch_ids]

    monkeypatch.setattr(forge_files.ProjectFiles, "history", history)
    monkeypatch.setattr(
        room_task_services.TaskService,
        "open_branches_in_room",
        open_branches_in_room,
    )


@pytest.mark.anyio
async def test_a_rooms_open_branches_are_walked_at_once(monkeypatch):
    branch_ids = [uuid.uuid4() for _ in range(AT_MOST_AT_ONCE + 2)]
    in_flight = 0
    peak = 0
    walked: list[object] = []

    async def history(self):
        nonlocal in_flight, peak
        walked.append(self.session)
        in_flight += 1
        peak = max(peak, in_flight)
        try:
            await asyncio.sleep(0.05)  # one forge round trip
            return [{"sha": f"sha-{self.task_id}"}]
        finally:
            in_flight -= 1

    _room(monkeypatch, branch_ids, history)
    sessions = _Sessions()

    commits = await _known_commits(sessions, uuid.uuid4(), uuid.uuid4())

    assert commits == {f"sha-{branch_id}" for branch_id in branch_ids}
    # Not one after another, and not all at once either.
    assert 1 < peak
    assert peak <= AT_MOST_AT_ONCE
    # One AsyncSession is not driven from two coroutines at once: apart from the
    # listing read that happens before the walks, no two walks share a session.
    assert len({id(session) for session in walked}) == len(branch_ids)
    assert len({id(session) for session in sessions.opened}) == len(branch_ids) + 1


@pytest.mark.anyio
async def test_the_baseline_is_the_recent_commits_of_every_open_branch(monkeypatch):
    deep, shallow = uuid.uuid4(), uuid.uuid4()
    rows = {
        deep: [{"sha": f"deep-{i}"} for i in range(_CHANGE_COMMIT_WALK + 5)],
        shallow: [{"sha": f"shallow-{i}"} for i in range(3)],
    }

    async def history(self):
        return rows[self.task_id]

    _room(monkeypatch, [deep, shallow], history)

    commits = await _known_commits(_Sessions(), uuid.uuid4(), uuid.uuid4())

    assert commits == {
        *(f"deep-{i}" for i in range(5, _CHANGE_COMMIT_WALK + 5)),
        "shallow-0",
        "shallow-1",
        "shallow-2",
    }


@pytest.mark.anyio
async def test_one_branch_that_cannot_be_walked_leaves_no_baseline(monkeypatch):
    branch_ids = [uuid.uuid4() for _ in range(AT_MOST_AT_ONCE + 2)]
    gone = branch_ids[3]

    async def history(self):
        if self.task_id == gone:
            raise RuntimeError(f"branch of {self.task_id} does not exist")
        return [{"sha": f"sha-{self.task_id}"}]

    _room(monkeypatch, branch_ids, history)

    assert await _known_commits(_Sessions(), uuid.uuid4(), uuid.uuid4()) is None
