"""Who may do what in a project's channels, answered over HTTP, one seat at a time.

The seats are the project's (``test_who_may_act_on_a_project``): its owner and
the team's owner, a team admin, a project admin, a member, a guest invited from
outside the team, a mentor, an outsider, the channel's AI teammate on the room
credential its turn carries, and nobody signed in. In that project bob, a
member, has opened a public channel and a private one, so he is the manager of
both and the only person in them; 综合 holds everyone.

Each row is one action in a channel, named by the permission it exercises
(``app.domain.project_permissions.Permission``), with the answer every seat
gets. Every refused seat tries first and the channel is read back to show none
of them changed it; then every seat that may tries in turn and its change is
read back. The answers are pinned as the API gives them today: a channel the
caller is not in and may not step into answers 404 (it is not confirmed to
exist), a public one answers 403.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.conftest import room_agent_headers
from tests.integration.test_project_reads_need_membership import off_the_street
from tests.integration.test_who_may_act_on_a_project import (
    HANDLES,
    SEATS,
    Project,
    seat_everyone,
)


@dataclass
class Channels:
    """The project, bob's two channels, and a message alice said in 综合."""

    project: Project
    public: str
    private: str
    said: str

    @property
    def client(self) -> TestClient:
        return self.project.client

    @property
    def root(self) -> str:
        return self.project.root

    def headers(self, seat: str, room: str) -> dict[str, str]:
        """``seat``'s credential for an action in ``room``: a person's own
        session, or the AI teammate's credential for that room."""
        if seat == "agent":
            return room_agent_headers(self.client, room)
        return self.project.headers[seat]

    def read_as(self, seat: str, path: str) -> dict:
        resp = self.client.get(path, headers=self.project.headers[seat])
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]

    def open_channel(self, *, private: bool) -> str:
        """A new channel of bob's."""
        resp = self.client.post(
            "/topics",
            json={
                "project_id": self.project.id,
                "title": f"频道-{uuid.uuid4().hex[:6]}",
                "members_only": private,
            },
            headers=self.project.headers["member"],
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["id"]


@pytest.fixture
def channels(client: TestClient) -> Channels:
    project = seat_everyone(client)
    ch = Channels(project=project, public="", private="", said="")
    ch.public = ch.open_channel(private=False)
    ch.private = ch.open_channel(private=True)
    said = client.post(
        f"/topics/{project.root}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "开个头"},
        headers=project.headers["owner"],
    )
    assert said.status_code == 200, said.text
    ch.said = said.json()["data"]["id"]
    return ch


# -- reading back ---------------------------------------------------------------


def _said_in(ch: Channels, room: str, text: str) -> bool:
    blocks = ch.read_as("member", f"/topics/{room}/blocks")["data"]
    return any(b.get("content") == text for b in blocks)


def _people_in(ch: Channels, room: str) -> set[str]:
    rows = ch.read_as("member", f"/topics/{room}/members")["data"]
    return {m["member_handle"] for m in rows if not m["agent"]}


def _topic(ch: Channels, room: str) -> dict:
    return ch.read_as("member", f"/topics/{room}")


def _directory(ch: Channels) -> dict[str, dict]:
    items = ch.read_as("owner", f"/projects/{ch.project.id}/channels")["items"]
    return {c["id"]: c for c in items}


def _task_titles(ch: Channels) -> set[str]:
    rows = ch.read_as("owner", f"/projects/{ch.project.id}/tasks")["data"]
    return {t["title"] for t in rows}


def _pinned(ch: Channels) -> bool:
    pins = ch.read_as("owner", f"/topics/{ch.root}/pins")
    return any(ch.said in str(pin) for pin in pins)


# -- what is set up before each seat tries --------------------------------------


def _fresh_public(ch: Channels) -> None:
    ch.public = ch.open_channel(private=False)


def _fresh_private(ch: Channels) -> None:
    ch.private = ch.open_channel(private=True)


def _erin_not_in(room: Callable[[Channels], str]) -> Callable[[Channels], None]:
    def prepare(ch: Channels) -> None:
        ch.client.delete(
            f"/topics/{room(ch)}/members/erin", headers=ch.project.headers["member"]
        )

    return prepare


def _erin_in_public(ch: Channels) -> None:
    _fresh_public(ch)
    resp = ch.client.post(
        f"/topics/{ch.public}/members",
        json={"handle": "erin"},
        headers=ch.project.headers["member"],
    )
    assert resp.status_code == 200, resp.text


def _unpinned(ch: Channels) -> None:
    ch.client.delete(
        f"/topics/{ch.root}/pins/{ch.said}", headers=ch.project.headers["owner"]
    )


# -- the table ------------------------------------------------------------------


def _answers(**by_seat: int) -> dict[str, int]:
    assert set(by_seat) == set(SEATS), set(SEATS) ^ set(by_seat)
    return by_seat


#: Everyone in the project, the mentor and the AI teammate too.
_EVERYONE_IN = dict(
    anonymous=401,
    outsider=403,
    agent=200,
    mentor=200,
    member=200,
    guest=200,
    admin=200,
    team_admin=200,
    owner=200,
    team_owner=200,
)

#: Only who is in the channel: bob and its AI teammate. A public channel says
#: 403 to the rest, a private one 404.
_ONLY_WHO_IS_IN_PUBLIC = dict(
    anonymous=401,
    outsider=403,
    agent=200,
    mentor=403,
    member=200,
    guest=403,
    admin=403,
    team_admin=403,
    owner=403,
    team_owner=403,
)
_ONLY_WHO_IS_IN_PRIVATE = dict(
    anonymous=401,
    outsider=404,
    agent=200,
    mentor=404,
    member=200,
    guest=404,
    admin=404,
    team_admin=404,
    owner=404,
    team_owner=404,
)

#: The channel's manager (bob) and whoever manages the project's channels.
_CHANNEL_MANAGERS = dict(
    anonymous=401,
    outsider=403,
    agent=403,
    mentor=403,
    member=200,
    guest=403,
    admin=200,
    team_admin=200,
    owner=200,
    team_owner=200,
)


@dataclass(frozen=True)
class Action:
    """One action in a channel.

    ``room`` is the channel it is taken in, which is also the room whose AI
    teammate's credential the ``agent`` seat presents. ``done`` reads back
    whether that seat's request took effect; ``prepare`` runs before the
    refusals and again before each seat that may.
    """

    rule: str
    permission: str
    room: Callable[[Channels], str]
    act: Callable[[Channels, dict, str], Response]
    answers: dict[str, int]
    prepare: Callable[[Channels], None] | None = None
    done: Callable[[Channels, str], bool] | None = None


def _say(room: Callable[[Channels], str]):
    return lambda ch, h, s: ch.client.post(
        f"/topics/{room(ch)}/messages",
        json={"request_id": str(uuid.uuid4()), "content": f"{s} 说一句"},
        headers=h,
    )


def _root(ch: Channels) -> str:
    return ch.root


def _public(ch: Channels) -> str:
    return ch.public


def _private(ch: Channels) -> str:
    return ch.private


ACTIONS = [
    # speak
    Action(
        "everyone in the project speaks in 综合, a mentor and the AI teammate too",
        "speak",
        _root,
        _say(_root),
        _answers(**_EVERYONE_IN),
        done=lambda ch, s: _said_in(ch, ch.root, f"{s} 说一句"),
    ),
    Action(
        "only who is in a public channel speaks in it, even the project's owner "
        "has to join first",
        "speak",
        _public,
        _say(_public),
        _answers(**_ONLY_WHO_IS_IN_PUBLIC),
        done=lambda ch, s: _said_in(ch, ch.public, f"{s} 说一句"),
    ),
    Action(
        "only who is in a private channel speaks in it; to the rest it is not there",
        "speak",
        _private,
        _say(_private),
        _answers(**_ONLY_WHO_IS_IN_PRIVATE),
        done=lambda ch, s: _said_in(ch, ch.private, f"{s} 说一句"),
    ),
    Action(
        "only who is in a private channel reads it; to the rest it is not there",
        "speak",
        _private,
        lambda ch, h, s: ch.client.get(f"/topics/{ch.private}/blocks", headers=h),
        _answers(**_ONLY_WHO_IS_IN_PRIVATE),
    ),
    # ask_agent
    Action(
        "everyone in the project asks the AI teammate to read the room, a mentor too",
        "ask_agent",
        _root,
        lambda ch, h, s: ch.client.post(
            f"/topics/{ch.root}/summon", json={}, headers=h
        ),
        _answers(**_EVERYONE_IN),
    ),
    # tasks
    Action(
        "any member opens a task; a mentor and the AI teammate do not",
        "tasks",
        _root,
        lambda ch, h, s: ch.client.post(
            f"/topics/{ch.root}/tasks", json={"title": f"任务-{s}"}, headers=h
        ),
        _answers(**{**_EVERYONE_IN, "agent": 403, "mentor": 403}),
        done=lambda ch, s: f"任务-{s}" in _task_titles(ch),
    ),
    # create_channels: bringing someone into a channel one is in
    Action(
        "who is in a channel, or manages the project's channels, brings someone in",
        "create_channels",
        _public,
        lambda ch, h, s: ch.client.post(
            f"/topics/{ch.public}/members", json={"handle": "erin"}, headers=h
        ),
        _answers(**_CHANNEL_MANAGERS),
        prepare=_erin_not_in(_public),
        done=lambda ch, s: "erin" in _people_in(ch, ch.public),
    ),
    Action(
        "only who is in a private channel brings someone in; to the rest it is "
        "not there",
        "create_channels",
        _private,
        lambda ch, h, s: ch.client.post(
            f"/topics/{ch.private}/members", json={"handle": "erin"}, headers=h
        ),
        _answers(**{**_ONLY_WHO_IS_IN_PRIVATE, "agent": 403}),
        prepare=_erin_not_in(_private),
        done=lambda ch, s: "erin" in _people_in(ch, ch.private),
    ),
    # manage_channels: the channel's own manager, or whoever manages them all
    Action(
        "only a channel's manager renames it",
        "manage_channels",
        _public,
        lambda ch, h, s: ch.client.post(
            f"/topics/{ch.public}/title", json={"title": f"改名-{s}"}, headers=h
        ),
        _answers(**_CHANNEL_MANAGERS),
        done=lambda ch, s: _topic(ch, ch.public)["title"] == f"改名-{s}",
    ),
    Action(
        "only a channel's manager says what it is for",
        "manage_channels",
        _public,
        lambda ch, h, s: ch.client.put(
            f"/topics/{ch.public}/description",
            json={"description": f"用途-{s}"},
            headers=h,
        ),
        _answers(**_CHANNEL_MANAGERS),
        done=lambda ch, s: _topic(ch, ch.public)["description"] == f"用途-{s}",
    ),
    Action(
        "only a channel's manager makes it private",
        "manage_channels",
        _public,
        lambda ch, h, s: ch.client.put(
            f"/topics/{ch.public}/members-only",
            json={"members_only": True},
            headers=h,
        ),
        _answers(**_CHANNEL_MANAGERS),
        prepare=_fresh_public,
        done=lambda ch, s: _directory(ch)[ch.public]["members_only"],
    ),
    Action(
        "only a channel's manager takes someone out of it",
        "manage_channels",
        _public,
        lambda ch, h, s: ch.client.delete(
            f"/topics/{ch.public}/members/erin", headers=h
        ),
        _answers(**_CHANNEL_MANAGERS),
        prepare=_erin_in_public,
        done=lambda ch, s: "erin" not in _people_in(ch, ch.public),
    ),
    Action(
        "only a channel's manager hands it to someone else",
        "manage_channels",
        _public,
        lambda ch, h, s: ch.client.put(
            f"/projects/{ch.project.id}/channels/{ch.public}/manager",
            json={"handle": "erin"},
            headers=h,
        ),
        _answers(**_CHANNEL_MANAGERS),
        prepare=_fresh_public,
        done=lambda ch, s: _directory(ch)[ch.public]["manager"] == "erin",
    ),
    Action(
        "only a channel's manager archives it",
        "manage_channels",
        _public,
        lambda ch, h, s: ch.client.post(f"/topics/{ch.public}/archive", headers=h),
        _answers(**_CHANNEL_MANAGERS),
        prepare=_fresh_public,
        done=lambda ch, s: _directory(ch)[ch.public]["archived"],
    ),
    Action(
        "only a channel's manager deletes it",
        "manage_channels",
        _public,
        lambda ch, h, s: ch.client.delete(f"/topics/{ch.public}", headers=h),
        _answers(**_CHANNEL_MANAGERS),
        prepare=_fresh_public,
        done=lambda ch, s: ch.public not in _directory(ch),
    ),
    Action(
        "whoever manages the project's channels archives a private one from outside it",
        "manage_channels",
        _private,
        lambda ch, h, s: ch.client.post(f"/topics/{ch.private}/archive", headers=h),
        _answers(**{**_CHANNEL_MANAGERS, "guest": 404}),
        prepare=_fresh_private,
        done=lambda ch, s: _directory(ch)[ch.private]["archived"],
    ),
    Action(
        "only whoever manages the project's channels steps into a private one, "
        "not even the channel's own manager",
        "manage_channels",
        _private,
        lambda ch, h, s: ch.client.post(
            f"/projects/{ch.project.id}/channels/{ch.private}/step-in", headers=h
        ),
        _answers(**{**_CHANNEL_MANAGERS, "member": 404, "guest": 404}),
        prepare=_fresh_private,
        done=lambda ch, s: HANDLES.get(s) in _people_in(ch, ch.private) - {"bob"},
    ),
    # being in a channel: one's own
    Action(
        "everyone in the project joins a public channel, a mentor and the AI "
        "teammate too",
        "in the project",
        _public,
        lambda ch, h, s: ch.client.post(f"/topics/{ch.public}/join", headers=h),
        _answers(**_EVERYONE_IN),
        # The AI teammate is seated in every channel already; joining keeps it.
        done=lambda ch, s: s == "agent" or HANDLES.get(s) in _people_in(ch, ch.public),
    ),
    Action(
        "everyone in the project pins a message in 综合, a mentor and the AI "
        "teammate too",
        "in the project",
        _root,
        lambda ch, h, s: ch.client.put(f"/topics/{ch.root}/pins/{ch.said}", headers=h),
        _answers(**_EVERYONE_IN),
        prepare=_unpinned,
        done=lambda ch, s: _pinned(ch),
    ),
]


def _call(ch: Channels, action: Action, seat: str) -> Response:
    if seat == "anonymous":
        with off_the_street(ch.client):
            return action.act(ch, {}, seat)
    return action.act(ch, ch.headers(seat, action.room(ch)), seat)


@pytest.mark.parametrize("action", ACTIONS, ids=lambda a: f"{a.permission}: {a.rule}")
def test_who_may_act_in_a_channel(channels: Channels, action: Action) -> None:
    """Every seat refused gets its pinned answer and changes nothing; every
    seat that may gets its pinned answer and its change is there to read."""
    if action.prepare is not None:
        action.prepare(channels)
    refused = [s for s in SEATS if action.answers[s] >= 400]
    allowed = [s for s in SEATS if action.answers[s] < 400]
    for seat in refused:
        resp = _call(channels, action, seat)
        assert resp.status_code == action.answers[seat], (
            f"{seat}: {resp.status_code} {resp.text}"
        )
        if action.done is not None:
            assert not action.done(channels, seat), (
                f"{seat}'s refused request took effect"
            )

    for seat in allowed:
        if action.prepare is not None:
            action.prepare(channels)
        resp = _call(channels, action, seat)
        assert resp.status_code == action.answers[seat], (
            f"{seat}: {resp.status_code} {resp.text}"
        )
        if action.done is not None:
            assert action.done(channels, seat), f"{seat}'s request did not take effect"
