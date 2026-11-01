"""A problem's page asks who the reader is to its space, and to the problem,
once per request.

Opening a problem runs several checks that each need the same facts: seeing
it (``TaskVisibilityService``), teaching it (``task_roles.teaches``, asked by
the route three times for its flags), the space row for its limit and its
payload, the reader's own claim and their teams' claims, and the problem's
stages. Each of those is one row or one set that cannot change within the
request, so each is read once; the next request reads it again, so a seat
taken away is refused there.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event

from tests.integration.conftest import UserCreator
from tests.integration.test_task_files import (
    _add,
    _auth,
    _create_task,
    _member_of,
    _new_board,
    upload_root,  # noqa: F401  (fixture)
)
from tests.integration.test_task_materials_follow_the_claim import (
    _claim_as_user,
    _decide,
)

_ADMIN_ROW = re.compile(
    r"FROM space_admin_relation WHERE space_admin_relation\.space_id = \$1::BIGINT AND space_admin_relation\.user_id"
)
_SPACE_ROW = re.compile(r"FROM space WHERE space\.id = \$1::BIGINT")
_SPACE_MEMBER = re.compile(
    r"FROM space_member WHERE space_member\.space_id = \$1::BIGINT AND space_member\.user_id"
)
_OWN_CLAIM = re.compile(
    r"FROM task_membership WHERE task_membership\.task_id = \$1::BIGINT AND task_membership\.member_id"
)
_TEAM_CLAIMS = re.compile(
    r"FROM task_membership WHERE task_membership\.task_id = \$1::BIGINT AND task_membership\.is_team IS true"
)
_STAGES = re.compile(r"FROM task_stages WHERE task_stages\.task_id = \$1::BIGINT")


def _count(statements: list[str], pattern: re.Pattern[str]) -> int:
    return sum(1 for s in statements if pattern.search(s))


def _fresh_get(api_client: TestClient, db_session, url: str, token: str):
    """One GET on a session holding nothing, as a request in production starts
    (``api_client`` shares the test's session)."""
    db_session.expunge_all()
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    engine = db_session.bind.sync_engine
    event.listen(engine, "after_cursor_execute", _record)
    try:
        response = api_client.get(url, headers=_auth(token))
    finally:
        event.remove(engine, "after_cursor_execute", _record)
    return seen, response


def test_a_claimants_problem_pages_ask_each_fact_once(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
    db_session,
):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="只问一次", startMode="APPLY")
    _add(api_client, task_id, board["creator_token"], "数据.csv", b"a,b\n")
    _, token = _member_of(user_client, api_client, board)
    participant, _ = _claim_as_user(api_client, task_id, token)
    _decide(api_client, board, task_id, participant, "APPROVED")

    # The first open makes the claimant's personal team (its writes rightly
    # make the request read again); the pages below are what every later
    # open costs.
    _fresh_get(api_client, db_session, f"/tasks/{task_id}", token)
    for url in (
        f"/tasks/{task_id}",
        f"/tasks/{task_id}/files",
        f"/tasks/{task_id}/progress",
        f"/spaces/{board['space_id']}",
    ):
        statements, r = _fresh_get(api_client, db_session, url, token)
        assert r.status_code == 200, (url, r.text)
        for pattern in (
            _ADMIN_ROW,
            _SPACE_ROW,
            _SPACE_MEMBER,
            _OWN_CLAIM,
            _TEAM_CLAIMS,
            _STAGES,
        ):
            assert _count(statements, pattern) <= 1, (url, pattern.pattern, statements)

    statements, r = _fresh_get(api_client, db_session, f"/tasks/{task_id}", token)
    assert r.json()["data"]["task"]["id"] == task_id
    assert len(statements) <= 20, statements


def test_a_member_taken_off_the_board_is_refused_on_the_next_request(
    api_client: TestClient,
    user_client: UserCreator,
    upload_root: Path,  # noqa: F811
    db_session,
):
    """Remembered for the request, not beyond it."""
    board = _new_board(user_client, api_client)
    _create_task(api_client, board, name="下一次就拒", startMode="APPLY")
    user, token = _member_of(user_client, api_client, board)
    space = board["space_id"]

    statements, r = _fresh_get(api_client, db_session, f"/spaces/{space}", token)
    assert r.status_code == 200, r.text

    removed = api_client.delete(
        f"/spaces/{space}/members/{user.user_id}",
        headers=_auth(board["creator_token"]),
    )
    assert removed.status_code in (200, 204), removed.text

    statements, r = _fresh_get(api_client, db_session, f"/spaces/{space}", token)
    assert r.status_code in (403, 404), r.text
