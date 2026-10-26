"""Who may do what on a 题目板, answered over HTTP, one standing at a time.

A board knows five standings, from the top: its owner, an admin the owner
appointed, a member, a signed-in person who is not in it, and nobody signed in.
Three rules divide them:

- **reading** the board is for anyone in it (owner, admin, member);
- **running** it — settings, categories, invite codes, the roster, domain
  groups, announcements, materials, the analytics desk — is for its owner and
  its admins;
- **owning** it — deleting it, appointing or removing admins — is for the
  owner alone.

Each test here takes one action, tries it from every standing below the
lowest one that may take it, and then has that lowest standing take it and
reads the change back. The refusals are pinned exactly as the API answers
them today: a route that checks visibility first answers a stranger 404 (the
board is not confirmed to exist), one that only asks "are you a manager"
answers 403. Cells other files already pin (the owner's own writes, the
stranger's 404 on the read routes) are not repeated.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)

ANON: dict[str, str] = {}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Board:
    """An approved board with one person at each signed-in standing."""

    client: TestClient
    space_id: int
    owner: dict[str, str]
    admin: dict[str, str]
    member: dict[str, str]
    outsider: dict[str, str]
    admin_id: int
    member_id: int
    #: Ids of whatever ``prepare`` created for the action under test.
    made: dict[str, int] = field(default_factory=dict)

    @property
    def base(self) -> str:
        return f"/spaces/{self.space_id}"

    def owner_get(self, suffix: str, **params) -> dict:
        resp = self.client.get(
            f"{self.base}{suffix}", params=params, headers=self.owner
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]


def _person(user_client: UserCreator, client: TestClient):
    user = user_client.create_user()
    token = user_client.login(client, user.username, user.password)
    return user, _auth(token)


@pytest.fixture
def board(api_client: TestClient, user_client: UserCreator) -> Board:
    _, owner = _person(user_client, api_client)
    admin_user, admin = _person(user_client, api_client)
    member_user, member = _person(user_client, api_client)
    _, outsider = _person(user_client, api_client)

    created = create_approved_space(
        api_client,
        json={
            "name": f"Standing Board ({unique_int(10000000, 99999999)})",
            "intro": "before",
            "description": "",
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=owner,
    )
    assert created.status_code == 201, created.text
    space_id = created.json()["data"]["space"]["id"]

    appointed = api_client.post(
        f"/spaces/{space_id}/managers",
        json={"userId": admin_user.user_id, "role": "ADMIN"},
        headers=owner,
    )
    assert appointed.status_code == 201, appointed.text
    added = api_client.post(
        f"/spaces/{space_id}/members",
        json={"userId": member_user.user_id},
        headers=owner,
    )
    assert added.status_code == 201, added.text

    return Board(
        client=api_client,
        space_id=space_id,
        owner=owner,
        admin=admin,
        member=member,
        outsider=outsider,
        admin_id=admin_user.user_id,
        member_id=member_user.user_id,
    )


# -- what the owner sets up before an action is tried ---------------------------


def _make_category(b: Board) -> None:
    resp = b.client.post(
        f"{b.base}/categories", json={"name": "第一单元"}, headers=b.owner
    )
    assert resp.status_code == 201, resp.text
    b.made["category"] = resp.json()["data"]["category"]["id"]


def _make_archived_category(b: Board) -> None:
    _make_category(b)
    resp = b.client.post(
        f"{b.base}/categories/{b.made['category']}/archive", headers=b.owner
    )
    assert resp.status_code == 200, resp.text


def _make_code(b: Board) -> None:
    resp = b.client.post(f"{b.base}/invite-codes", json={"maxUses": 5}, headers=b.owner)
    assert resp.status_code == 201, resp.text
    b.made["code"] = resp.json()["data"]["inviteCode"]["id"]


def _make_group(b: Board) -> None:
    resp = b.client.post(
        f"{b.base}/domain-groups",
        json={"name": "北京大学", "domains": ["pku.edu.cn"]},
        headers=b.owner,
    )
    assert resp.status_code == 201, resp.text
    b.made["group"] = resp.json()["data"]["group"]["id"]


def _make_announcement(b: Board) -> None:
    resp = b.client.post(
        f"{b.base}/announcements",
        json={"title": "期中安排", "content": "<p>见附件</p>"},
        headers=b.owner,
    )
    assert resp.status_code == 201, resp.text
    b.made["announcement"] = resp.json()["data"]["announcement"]["id"]


def _make_material(b: Board) -> None:
    resp = _upload(b, b.owner)
    assert resp.status_code == 201, resp.text
    b.made["material"] = resp.json()["data"]["material"]["id"]


def _make_second_member(b: Board, user_client: UserCreator) -> None:
    user = user_client.create_user()
    resp = b.client.post(
        f"{b.base}/members", json={"userId": user.user_id}, headers=b.owner
    )
    assert resp.status_code == 201, resp.text
    b.made["target"] = user.user_id


def _make_stranger(b: Board, user_client: UserCreator) -> None:
    b.made["target"] = user_client.create_user().user_id


def _upload(b: Board, headers: dict[str, str]) -> Response:
    return b.client.post(
        f"{b.base}/materials",
        files={"file": ("handout.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
        data={"type": "file"},
        headers=headers,
    )


def _roster(b: Board) -> set[int]:
    return {m["userId"] for m in b.owner_get("/members")["members"]}


def _category(b: Board) -> dict | None:
    cats = b.owner_get("/categories", includeArchived="true")["categories"]
    return next((c for c in cats if c["id"] == b.made["category"]), None)


def _code(b: Board) -> dict | None:
    codes = b.owner_get("/invite-codes")["inviteCodes"]
    return next((c for c in codes if c["id"] == b.made.get("code")), None)


def _group_names(b: Board) -> set[str]:
    return {g["name"] for g in b.owner_get("/domain-groups")["groups"]}


def _announcements(b: Board) -> list[dict]:
    data = b.owner_get("/announcements")
    return [*data["current"], *data["expired"]]


def _materials(b: Board) -> list[dict]:
    return b.owner_get("/materials")["materials"]


# -- the manager's desk: owner and admins may, members and strangers may not ----


@dataclass(frozen=True)
class ManagerAction:
    """One thing only a board's owner or admins may do.

    ``act`` is the request; ``done`` reads back, as the owner, whether it took
    effect. ``outsider`` and ``member`` are the statuses main answers them.
    """

    rule: str
    act: Callable[[Board, dict[str, str]], Response]
    allowed: int
    outsider: int
    member: int = 403
    prepare: Callable[[Board, UserCreator], None] | None = None
    done: Callable[[Board], bool] | None = None


def _p(fn: Callable[[Board], None]) -> Callable[[Board, UserCreator], None]:
    return lambda b, _users: fn(b)


MANAGER_ACTIONS = [
    ManagerAction(
        "only a manager changes the board's settings",
        lambda b, h: b.client.patch(b.base, json={"intro": "after"}, headers=h),
        allowed=200,
        outsider=403,
        done=lambda b: b.owner_get("")["space"]["intro"] == "after",
    ),
    ManagerAction(
        "only a manager creates a category",
        lambda b, h: b.client.post(
            f"{b.base}/categories", json={"name": "新单元"}, headers=h
        ),
        allowed=201,
        outsider=403,
        done=lambda b: (
            "新单元" in {c["name"] for c in b.owner_get("/categories")["categories"]}
        ),
    ),
    ManagerAction(
        "only a manager renames a category",
        lambda b, h: b.client.patch(
            f"{b.base}/categories/{b.made['category']}",
            json={"name": "改过的单元"},
            headers=h,
        ),
        allowed=200,
        outsider=403,
        prepare=_p(_make_category),
        done=lambda b: (_category(b) or {}).get("name") == "改过的单元",
    ),
    ManagerAction(
        "only a manager deletes a category",
        lambda b, h: b.client.delete(
            f"{b.base}/categories/{b.made['category']}", headers=h
        ),
        allowed=204,
        outsider=403,
        prepare=_p(_make_category),
        done=lambda b: _category(b) is None,
    ),
    ManagerAction(
        "only a manager archives a category",
        lambda b, h: b.client.post(
            f"{b.base}/categories/{b.made['category']}/archive", headers=h
        ),
        allowed=200,
        outsider=403,
        prepare=_p(_make_category),
        done=lambda b: bool((_category(b) or {}).get("archivedAt")),
    ),
    ManagerAction(
        "only a manager unarchives a category",
        lambda b, h: b.client.delete(
            f"{b.base}/categories/{b.made['category']}/archive", headers=h
        ),
        allowed=200,
        outsider=403,
        prepare=_p(_make_archived_category),
        done=lambda b: not (_category(b) or {"archivedAt": 1}).get("archivedAt"),
    ),
    ManagerAction(
        "only a manager lists the invite codes",
        lambda b, h: b.client.get(f"{b.base}/invite-codes", headers=h),
        allowed=200,
        outsider=403,
    ),
    ManagerAction(
        "only a manager mints an invite code",
        lambda b, h: b.client.post(
            f"{b.base}/invite-codes", json={"maxUses": 3, "note": "第二班"}, headers=h
        ),
        allowed=201,
        outsider=403,
        done=lambda b: (
            "第二班"
            in {c.get("note") for c in b.owner_get("/invite-codes")["inviteCodes"]}
        ),
    ),
    ManagerAction(
        "only a manager edits an invite code",
        lambda b, h: b.client.patch(
            f"{b.base}/invite-codes/{b.made['code']}", json={"maxUses": 6}, headers=h
        ),
        allowed=200,
        outsider=403,
        prepare=_p(_make_code),
        done=lambda b: (_code(b) or {}).get("maxUses") == 6,
    ),
    ManagerAction(
        "only a manager revokes an invite code",
        lambda b, h: b.client.delete(
            f"{b.base}/invite-codes/{b.made['code']}", headers=h
        ),
        allowed=204,
        outsider=403,
        prepare=_p(_make_code),
        done=lambda b: _code(b) is None,
    ),
    ManagerAction(
        "only a manager puts someone on the roster",
        lambda b, h: b.client.post(
            f"{b.base}/members", json={"userId": b.made["target"]}, headers=h
        ),
        allowed=201,
        outsider=404,
        prepare=_make_stranger,
        done=lambda b: b.made["target"] in _roster(b),
    ),
    ManagerAction(
        "only a manager takes someone off the roster",
        lambda b, h: b.client.delete(f"{b.base}/members/{b.made['target']}", headers=h),
        allowed=204,
        outsider=404,
        prepare=_make_second_member,
        done=lambda b: b.made["target"] not in _roster(b),
    ),
    ManagerAction(
        "only a manager creates a domain group",
        lambda b, h: b.client.post(
            f"{b.base}/domain-groups",
            json={"name": "人民大学", "domains": ["ruc.edu.cn"]},
            headers=h,
        ),
        allowed=201,
        outsider=403,
        done=lambda b: "人民大学" in _group_names(b),
    ),
    ManagerAction(
        "only a manager edits a domain group",
        lambda b, h: b.client.patch(
            f"{b.base}/domain-groups/{b.made['group']}",
            json={"name": "北大"},
            headers=h,
        ),
        allowed=200,
        outsider=403,
        prepare=_p(_make_group),
        done=lambda b: _group_names(b) == {"北大"},
    ),
    ManagerAction(
        "only a manager deletes a domain group",
        lambda b, h: b.client.delete(
            f"{b.base}/domain-groups/{b.made['group']}", headers=h
        ),
        allowed=204,
        outsider=403,
        prepare=_p(_make_group),
        done=lambda b: _group_names(b) == set(),
    ),
    ManagerAction(
        "only a manager publishes an announcement",
        lambda b, h: b.client.post(
            f"{b.base}/announcements",
            json={"title": "调课通知", "content": "<p>周三改周四</p>"},
            headers=h,
        ),
        allowed=201,
        outsider=404,
        done=lambda b: "调课通知" in {a["title"] for a in _announcements(b)},
    ),
    ManagerAction(
        "only a manager edits an announcement",
        lambda b, h: b.client.patch(
            f"{b.base}/announcements/{b.made['announcement']}",
            json={"title": "期中安排（更新）"},
            headers=h,
        ),
        allowed=200,
        outsider=404,
        prepare=_p(_make_announcement),
        done=lambda b: [a["title"] for a in _announcements(b)] == ["期中安排（更新）"],
    ),
    ManagerAction(
        "only a manager deletes an announcement",
        lambda b, h: b.client.delete(
            f"{b.base}/announcements/{b.made['announcement']}", headers=h
        ),
        allowed=204,
        outsider=404,
        prepare=_p(_make_announcement),
        done=lambda b: _announcements(b) == [],
    ),
    ManagerAction(
        "only a manager uploads a material",
        _upload,
        allowed=201,
        outsider=404,
        done=lambda b: len(_materials(b)) == 1,
    ),
    ManagerAction(
        "only a manager changes who may see a material",
        lambda b, h: b.client.patch(
            f"{b.base}/materials/{b.made['material']}",
            json={"visibility": "admins"},
            headers=h,
        ),
        allowed=200,
        outsider=404,
        prepare=_p(_make_material),
        done=lambda b: [m["visibility"] for m in _materials(b)] == ["admins"],
    ),
    ManagerAction(
        "only a manager takes a material off the board",
        lambda b, h: b.client.delete(
            f"{b.base}/materials/{b.made['material']}", headers=h
        ),
        allowed=204,
        outsider=404,
        prepare=_p(_make_material),
        done=lambda b: _materials(b) == [],
    ),
    *(
        ManagerAction(
            f"only a manager reads {suffix}",
            lambda b, h, suffix=suffix: b.client.get(f"{b.base}{suffix}", headers=h),
            allowed=200,
            outsider=404,
        )
        for suffix in (
            "/analytics/overview",
            "/analytics/alerts",
            "/analytics/publishers",
            "/analytics/participants",
            "/analytics/people",
            "/analytics/tasks",
            "/analytics/participants/export",
            "/analytics/tasks/export",
            "/analytics/publishers/export",
            "/publishers/participation",
            "/participants/export",
            "/submissions",
        )
    ),
]


@pytest.mark.parametrize("action", MANAGER_ACTIONS, ids=lambda a: a.rule)
def test_a_board_is_run_by_its_owner_and_admins(
    board: Board, user_client: UserCreator, action: ManagerAction
) -> None:
    """An admin the owner appointed may do it; a member, a stranger and nobody
    signed in may not, and their refusal changes nothing."""
    if action.prepare is not None:
        action.prepare(board, user_client)

    refusals = [
        ("anonymous", ANON, 401),
        ("outsider", board.outsider, action.outsider),
        ("member", board.member, action.member),
    ]
    for who, headers, expected in refusals:
        resp = action.act(board, headers)
        assert resp.status_code == expected, f"{who}: {resp.status_code} {resp.text}"
    if action.done is not None:
        assert not action.done(board), "a refused request took effect"

    resp = action.act(board, board.admin)
    assert resp.status_code == action.allowed, resp.text
    if action.done is not None:
        assert action.done(board), "the admin's request did not take effect"


# -- the owner's chair: only the owner, not even an admin -----------------------


def _managers(b: Board) -> dict[int, str]:
    return {m["userId"]: m["role"] for m in b.owner_get("/managers")["managers"]}


OWNER_ACTIONS = [
    (
        "only the owner deletes the board",
        lambda b, h: b.client.delete(b.base, headers=h),
    ),
    (
        "only the owner appoints an admin",
        lambda b, h: b.client.post(
            f"{b.base}/managers",
            json={"userId": b.member_id, "role": "ADMIN"},
            headers=h,
        ),
    ),
    (
        "only the owner changes an admin's role",
        lambda b, h: b.client.patch(
            f"{b.base}/managers/{b.admin_id}", json={"role": "OWNER"}, headers=h
        ),
    ),
    (
        "only the owner removes an admin",
        lambda b, h: b.client.delete(f"{b.base}/managers/{b.admin_id}", headers=h),
    ),
]


@pytest.mark.parametrize(
    "act", [a for _, a in OWNER_ACTIONS], ids=[rule for rule, _ in OWNER_ACTIONS]
)
def test_an_admin_cannot_do_what_only_the_owner_may(
    board: Board, act: Callable[[Board, dict[str, str]], Response]
) -> None:
    """Deleting the board and deciding who manages it is the owner's alone: an
    admin, a member and a stranger get 403, nobody signed in 401, and the board
    and its managers stay as they were. (The owner doing each is pinned in
    ``test_space.py``.)"""
    before = _managers(board)
    for who, headers, expected in [
        ("anonymous", ANON, 401),
        ("outsider", board.outsider, 403),
        ("member", board.member, 403),
        ("admin", board.admin, 403),
    ]:
        resp = act(board, headers)
        assert resp.status_code == expected, f"{who}: {resp.status_code} {resp.text}"
    assert _managers(board) == before


# -- reading: anyone in the board, nobody outside it ----------------------------


def _first_category_path(b: Board) -> str:
    cats = b.owner_get("/categories")["categories"]
    return f"/categories/{cats[0]['id']}"


@pytest.mark.parametrize(
    "suffix",
    [
        lambda b: _first_category_path(b),
        lambda b: "/me/publishing/tasks",
    ],
    ids=["one category", "my published tasks"],
)
def test_a_member_reads_what_a_stranger_is_not_told_exists(
    board: Board, suffix: Callable[[Board], str]
) -> None:
    """A member reads it; a stranger gets 404; nobody signed in gets 401."""
    path = f"{board.base}{suffix(board)}"
    assert board.client.get(path).status_code == 401
    assert board.client.get(path, headers=board.outsider).status_code == 404
    resp = board.client.get(path, headers=board.member)
    assert resp.status_code == 200, resp.text


READS = [
    "",
    "/members",
    "/managers",
    "/categories",
    "/topics",
    "/announcements",
    "/materials",
    "/analytics/learning/filters",
]


@pytest.mark.parametrize("suffix", READS, ids=lambda s: s or "/")
def test_nobody_signed_in_reads_any_part_of_a_board(board: Board, suffix: str) -> None:
    """Every read of a board asks who is reading first: no credentials is 401."""
    assert board.client.get(f"{board.base}{suffix}").status_code == 401


def test_nobody_signed_in_downloads_a_material(board: Board) -> None:
    """A material's bytes need a signed-in reader: no credentials is 401."""
    _make_material(board)
    path = f"{board.base}/materials/{board.made['material']}/download"
    assert board.client.get(path).status_code == 401
    assert board.client.get(path, headers=board.member).status_code == 200


# -- doors not tied to one standing on one board --------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/spaces", None),
        ("POST", "/spaces", {"name": "匿名的板", "intro": "", "description": ""}),
        ("POST", "/spaces/join", {"code": "ANYCODE"}),
        ("GET", "/space-applications", None),
    ],
    ids=["list boards", "create a board", "join with a code", "my applications"],
)
def test_nobody_signed_in_lists_creates_or_joins_a_board(
    api_client: TestClient, method: str, path: str, body: dict | None
) -> None:
    """Listing, creating and joining boards are for signed-in people: 401."""
    resp = api_client.request(method, path, json=body)
    assert resp.status_code == 401, resp.text


def test_the_owner_cannot_leave_and_a_stranger_has_nothing_to_leave(
    board: Board,
) -> None:
    """Leaving is a member's: the owner is refused 403 (a board keeps its owner),
    a stranger gets 404, nobody signed in 401, and a member leaves with 204."""
    path = f"{board.base}/leave"
    assert board.client.post(path).status_code == 401
    assert board.client.post(path, headers=board.outsider).status_code == 404
    assert board.client.post(path, headers=board.owner).status_code == 403
    assert board.client.post(path, headers=board.member).status_code == 204
    assert board.member_id not in _roster(board)
