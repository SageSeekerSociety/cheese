"""学情分析那条只读推送：这块板变了就说一声，说给能看见这块板的人。

按浏览器会收到的帧来断（``WS /spaces/{id}/learning-analysis/live``）：

- 能看见这块板的人订阅得上，第一帧说「现在是这样」（只有这块板的 id，不带分析结果）；
- 板外的人被拒；
- 「这块板变了」那条帧里也不带分析结果 —— 谁能看哪一条判断仍由那两条 GET 判。
"""

from __future__ import annotations

import json

import pytest
from starlette.websockets import WebSocketDisconnect

from app.domain.space import learning_live
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    session_token,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _board(user_client: UserCreator, api_client, db_session) -> dict:
    creator = user_client.create_user()
    token = user_client.login(api_client, creator.username, creator.password)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Live Board ({unique_int()})",
            "intro": "一门课",
            "description": "一个题目板",
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return {
        "token": token,
        "handle": creator.username,
        "space_id": resp.json()["data"]["space"]["id"],
    }


def test_a_watcher_is_told_the_board_changed(api_client, user_client, db_session):
    board = _board(user_client, api_client, db_session)
    token = session_token(board["handle"])
    with api_client.websocket_connect(
        f"/spaces/{board['space_id']}/learning-analysis/live?token={token}"
    ) as ws:
        opening = ws.receive_json()
        # 第一帧就是「现在是这样」：不带任何分析结果，只说这块板。
        assert opening["type"] == learning_live.FRAME_TYPE
        assert opening["spaceId"] == board["space_id"]


def test_the_frame_carries_no_analysis(api_client, user_client, db_session):
    board = _board(user_client, api_client, db_session)
    frame = learning_live.frame(board["space_id"])
    assert set(frame) == {"type", "spaceId"}
    assert json.loads(json.dumps(frame)) == frame


def test_someone_outside_the_board_is_refused(api_client, user_client, db_session):
    board = _board(user_client, api_client, db_session)
    outsider = user_client.create_user()
    outsider_token = user_client.login(
        api_client, outsider.username, outsider.password
    )
    with pytest.raises(WebSocketDisconnect):
        with api_client.websocket_connect(
            f"/spaces/{board['space_id']}/learning-analysis/live?token={outsider_token}"
        ) as ws:
            ws.receive_json()
