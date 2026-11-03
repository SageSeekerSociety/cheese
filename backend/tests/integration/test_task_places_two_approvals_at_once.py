"""两份通过同时进来，名额也只给出一份。

名额只被通过了的申请占，通过时数一遍还剩几个。两个管理员（或同一个人在两个标签页）
同时点「批准」时，两次请求各自数、各自写，要是数和写不是一步，两边都数到「还剩一个」，
上限 1 的题就通过了两个人。这里让两次通过真的并发跑（各用各的数据库会话，和线上一样），
看结果：一份 200，一份 ``taskPlacesFull``，题目上通过的只有一个。
"""

from __future__ import annotations

import asyncio

import httpx

from app.db.session import get_db
from tests.integration.test_task_files import (
    _auth,
    _claim,
    _create_task,
    _member_of,
    _new_board,
)


def test_two_approvals_at_once_on_the_last_place_let_only_one_in(
    api_client, user_client, db_session, db_connection, _portal, app
):
    board = _new_board(user_client, api_client)
    task = _create_task(
        api_client, board, name="只剩一个名额", participantLimit=1, startMode="APPLY"
    )
    claims = []
    for _ in range(2):
        _, token = _member_of(user_client, api_client, board)
        claims.append(_claim(api_client, board, task, token, approve=False))
    # The two approvals must run on sessions of their own, as two requests do in
    # production: commit what the setup wrote and let the app open real sessions.
    _portal.call(db_session.commit)
    _portal.call(db_connection.commit)
    shared = app.dependency_overrides.pop(get_db)

    async def approve_both(task: int, claims: list[int]) -> tuple[list, dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as browser:
            responses = await asyncio.gather(
                *(
                    browser.patch(
                        f"/tasks/{task}/participants/{claim}",
                        json={"approved": "APPROVED"},
                        headers=_auth(board["creator_token"]),
                    )
                    for claim in claims
                )
            )
            detail = await browser.get(
                f"/tasks/{task}", headers=_auth(board["creator_token"])
            )
            assert detail.status_code == 200, detail.text
            return responses, detail.json()["data"]["task"]["participants"]

    try:
        responses, participants = _portal.call(approve_both, task, claims)
        assert sorted(r.status_code for r in responses) == [200, 403], [
            r.text for r in responses
        ]
        refused = next(r for r in responses if r.status_code == 403)
        assert refused.json()["error"]["i18n"]["key"] == "taskPlacesFull"
        assert participants["approved"] == 1
    finally:
        app.dependency_overrides[get_db] = shared
