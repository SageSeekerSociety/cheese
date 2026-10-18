"""A list endpoint must cost the same at 30 rows as at 3.

The failure this catches returns perfect JSON. A roster that reads one user per
member, a board list that asks for each board's admins in turn, a submission
list that re-reads its author — every one of them passes every functional test
there is. Only the number of round trips tells them apart from the batched
version, and only a comparison at two sizes tells you it is the *size* that
drives the count rather than the request.

`test_hot_path_queries.py` pinned three endpoints by hand, each with its own
counting block. This file is the general form: seed the list to 3, count; seed
it to 30, count; assert the difference is a constant. Adding the next endpoint
is one call to :func:`assert_does_not_scale`, not another hand-written fake
session.

The assertion is on the **difference**, never on either absolute count. The
absolute number is a property of the endpoint — a route that has to check a
permission costs one more statement forever — and pinning it would make every
unrelated change to the session cost a test edit. Growth is the bug.
"""

import time

import pytest
from sqlalchemy import text

from tests.integration.conftest import create_approved_space, unique_int
from tests.support.counting_session import assert_does_not_scale, count_sql

OWNER = "scale-owner"
SUBMITTER = "scale-submitter"


def _auth(client, handle: str) -> dict[str, str]:
    """Headers for a real, DB-backed person — every list route below resolves
    the viewer's ``user_id``."""
    from tests.conftest import seed_user

    return {"Authorization": f"Bearer {seed_user(client, handle)}"}


def _rows(response, key: str) -> int:
    return len(response.json()["data"][key])


# --------------------------------------------------------------------------
# GET /tags
# --------------------------------------------------------------------------


def test_the_tag_list_does_not_scale(client) -> None:
    headers = _auth(client, OWNER)
    made: list[int] = []

    def seed(n: int) -> None:
        while len(made) < n:
            r = client.post(
                "/tags",
                json={"name": f"sc{len(made)}-{unique_int()}"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            made.append(r.json()["data"]["id"])

    def request():
        # `q` is required: with no keyword the service answers an empty page.
        return client.get(
            "/tags", params={"q": "sc", "page_size": 100}, headers=headers
        )

    assert_does_not_scale(client, seed=seed, request=request, what="GET /tags")
    assert _rows(request(), "topics") >= 30


# --------------------------------------------------------------------------
# GET /teams
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known N+1, measured 13 -> 121 statements for 3 -> 30 teams: the route "
        "reads each team's members and their user maps in turn. Batch both "
        "across the page; when that lands this XPASSes and the marker must go."
    ),
)
def test_the_team_list_does_not_scale(client) -> None:
    headers = _auth(client, OWNER)
    word = f"zz{unique_int()}"[:8]
    made: list[int] = []

    def seed(n: int) -> None:
        while len(made) < n:
            r = client.post(
                "/teams",
                json={"name": f"{word} {len(made)}"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            made.append(r.json()["data"]["team"]["id"])

    def request():
        return client.get(
            "/teams",
            params={"query": word, "pageSize": 100},
            headers=headers,
        )

    assert_does_not_scale(client, seed=seed, request=request, what="GET /teams")
    assert _rows(request(), "teams") >= 30


# --------------------------------------------------------------------------
# GET /spaces
# --------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known N+1, measured 15 -> 123 statements for 3 -> 30 spaces: the route "
        "asks for each space's admins, then each admin's user and profile. "
        "Batch all three across the page; when that lands this XPASSes and the "
        "marker must go."
    ),
)
def test_the_space_list_does_not_scale(client) -> None:
    headers = _auth(client, OWNER)
    suffix = unique_int()
    made: list[int] = []

    def seed(n: int) -> None:
        while len(made) < n:
            r = create_approved_space(
                client,
                json={"name": f"Scale board {suffix} {len(made)}"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            made.append(r.json()["data"]["space"]["id"])

    def request():
        return client.get("/spaces", params={"pageSize": 100}, headers=headers)

    assert_does_not_scale(client, seed=seed, request=request, what="GET /spaces")
    assert _rows(request(), "spaces") >= 30


# --------------------------------------------------------------------------
# GET /comments/{commentableType}/{commentableId}
# --------------------------------------------------------------------------


def test_the_comment_list_does_not_scale(client) -> None:
    headers = _auth(client, OWNER)
    # ANSWER takes any id: the route validates the target only for QUESTION.
    target = unique_int()
    made: list[int] = []

    def seed(n: int) -> None:
        while len(made) < n:
            r = client.post(
                f"/comments/ANSWER/{target}",
                json={"content": f"comment {len(made)}"},
                headers=headers,
            )
            assert r.status_code == 201, r.text
            made.append(len(made))

    def request():
        return client.get(
            f"/comments/ANSWER/{target}",
            params={"page_size": 100},
            headers=headers,
        )

    assert_does_not_scale(
        client, seed=seed, request=request, what="GET /comments/ANSWER/{id}"
    )
    assert _rows(request(), "comments") >= 30


# --------------------------------------------------------------------------
# GET /tasks/{taskId}/participants/{participantId}/submissions
# --------------------------------------------------------------------------


def _board(client, headers: dict[str, str], name: str) -> tuple[int, int]:
    r = create_approved_space(
        client,
        json={"name": name, "intro": "t", "description": "d", "avatarId": 1},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    space = r.json()["data"]["space"]
    return space["id"], space["defaultCategoryId"]


def _task(client, headers: dict[str, str], space: int, category: int, name: str):
    r = client.post(
        "/tasks",
        json={
            "name": name,
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "intro": "t",
            "description": "d",
            "space": space,
            "categoryId": category,
            "deadline": int((time.time() + 7 * 24 * 3600) * 1000),
            "submissionSchema": [{"prompt": "Text Entry", "type": "TEXT"}],
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["data"]["task"]["id"]
    r = client.patch(
        f"/tasks/{task_id}", json={"approved": "APPROVED"}, headers=headers
    )
    assert r.status_code == 200, r.text
    return task_id


def _participant(client, owner: dict, submitter: dict, task_id: int) -> int:
    r = client.post(f"/tasks/{task_id}/participations/user", json={}, headers=submitter)
    assert r.status_code == 200, r.text
    participant_id = r.json()["data"]["participant"]["id"]
    r = client.patch(
        f"/tasks/{task_id}/participants/{participant_id}",
        json={"approved": "APPROVED"},
        headers=owner,
    )
    assert r.status_code == 200, r.text
    return participant_id


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known N+1, measured 9 -> 36 statements for 3 -> 30 submissions: one "
        "statement per row in the list's hydration. Batch it; when that lands "
        "this XPASSes and the marker must go."
    ),
)
def test_the_submission_list_does_not_scale(client) -> None:
    owner = _auth(client, OWNER)
    submitter = _auth(client, SUBMITTER)
    space, category = _board(client, owner, f"Submissions {unique_int()}")

    def seeded(n: int) -> tuple[int, int, list[int]]:
        task_id = _task(client, owner, space, category, f"Scale {n} {unique_int()}")
        participant = _participant(client, owner, submitter, task_id)
        sent: list[int] = []
        while len(sent) < n:
            r = client.post(
                f"/tasks/{task_id}/participants/{participant}/submissions",
                json=[{"text": f"version {len(sent)}"}],
                headers=submitter,
            )
            assert r.status_code == 200, r.text
            sent.append(r.json()["data"]["submission"]["id"])
        return task_id, participant, sent

    small_task, small_participant, _ = seeded(3)
    large_task, large_participant, _ = seeded(30)

    def up_to(task_id: int, participant: int):
        return client.get(
            f"/tasks/{task_id}/participants/{participant}/submissions",
            params={"allVersions": "true", "pageSize": 100},
            headers=submitter,
        )

    small, large = 3, 30

    small_count, small_response = count_sql(
        client, lambda: up_to(small_task, small_participant)
    )
    assert small_response.status_code == 200, small_response.text
    large_count, large_response = count_sql(
        client, lambda: up_to(large_task, large_participant)
    )
    assert large_response.status_code == 200, large_response.text
    assert _rows(large_response, "submissions") >= large

    grew = large_count - small_count
    assert grew <= 2, (
        f"submissions grew from {small} to {large} rows and the statement count "
        f"went {small_count} -> {large_count} (+{grew}); something is querying "
        "per row."
    )


# --------------------------------------------------------------------------
# The assertion itself must bite
# --------------------------------------------------------------------------


def _select_many(client, n: int) -> None:
    """Issue ``n`` statements on the engine the app's requests are served from.

    Through the TestClient's own portal: the engine is an asyncpg one bound to
    that loop, so a plain ``engine.connect()`` here would be a greenlet error
    rather than a query.
    """

    async def run() -> None:
        # `test_app_engine` is the engine requests are served from; the
        # `test_factory` next to it is a different (NullPool) one, so a query
        # through that would not be the thing under test.
        async with client.test_app_engine.connect() as connection:
            for _ in range(n):
                await connection.execute(text("SELECT 1"))

    client.portal.call(run)


def test_the_scale_assertion_fails_a_per_row_loop(client) -> None:
    """Proof the helper above is not decoration.

    A deliberately looping implementation — one statement per listed row —
    must be reported, and a batched one must not, so that a green test says
    something about the endpoint rather than about the assertion being asleep.
    """
    rows: list[int] = []

    def seed(n: int) -> None:
        del rows[:]
        rows.extend(range(n))

    def per_row():
        _select_many(client, len(rows))
        return _FakeResponse()

    def batched():
        _select_many(client, 1)
        return _FakeResponse()

    with pytest.raises(AssertionError, match="querying per row"):
        assert_does_not_scale(client, seed=seed, request=per_row)
    # ...and the batched shape passes, so the assertion is not simply always red.
    assert_does_not_scale(client, seed=seed, request=batched)


class _FakeResponse:
    status_code = 200
    text = ""
