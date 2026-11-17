"""出题人用参考答案试跑一套测试点（方案 §出题人：配测试点）。

谁能排：能在这个空间出题的人；看不到这个空间的人连它在不在都不知道（404）。谁能读：
只有发起的人。试跑和学生提交的评测走同一条队列（评测主机是假的 hub，见
``test_judge_dispatch``），读回来的结果给出每个用例的输入、期望和实际输出，以及最慢的
用例用了多久、时限多少。
"""

from __future__ import annotations

import base64
import io
import zipfile
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.domain.judge import dispatch
from tests.integration.conftest import UserCreator, set_free_plan_credits
from tests.integration.test_judge_dispatch import (
    SUITE,
    hub,  # noqa: F401 — the fake judge host
)
from tests.integration.test_task_files import (
    _auth,
    _login,
    _member_of,
    _new_board,
    upload_root,  # noqa: F401 — 建空间要的那个夹具
)

FILES = [{"path": "main.py", "content": "print(sum(map(int, input().split())))\n"}]


def _trial(api_client: TestClient, token: str, space_id: int, **body):
    return api_client.post(
        f"/spaces/{space_id}/test-trials",
        json={"testSuite": SUITE[0], **body},
        headers=_auth(token),
    )


def _read(api_client: TestClient, token: str, run_id: int):
    return api_client.get(f"/tasks/test-trials/{run_id}", headers=_auth(token))


@pytest.mark.usefixtures("hub")
def test_a_setter_tries_reference_code_and_reads_how_it_did(
    api_client: TestClient, user_client: UserCreator, db_session, _portal
):
    board = _new_board(user_client, api_client)
    _portal.call(set_free_plan_credits, db_session, 100)
    sent = _trial(api_client, board["creator_token"], board["space_id"], files=FILES)
    assert sent.status_code == 200, sent.text
    trial = sent.json()["data"]["trial"]
    assert trial["state"] == "queued" and trial["purpose"] == "TRIAL"

    @asynccontextmanager
    async def sessions():
        yield db_session

    async def tick():
        return await dispatch.run_due(sessions)

    _portal.call(tick)

    read = _read(api_client, board["creator_token"], trial["runId"])
    assert read.status_code == 200, read.text
    done = read.json()["data"]["trial"]
    assert done["state"] == "done"
    assert [c["status"] for c in done["cases"]] == ["ok", "wrong"]
    # The setter sees even the hidden case in full.
    assert done["cases"][1]["expected"] == "300\n"
    assert done["cases"][1]["actual"] == "500\n"
    assert done["timeLimitMs"] == 500 and done["slowestMs"] == 11


def test_a_member_of_the_space_may_try_but_only_the_one_who_did_reads_it(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    _, member = _member_of(user_client, api_client, board)
    sent = _trial(api_client, member, board["space_id"], files=FILES)
    assert sent.status_code == 200, sent.text
    run_id = sent.json()["data"]["trial"]["runId"]

    assert _read(api_client, member, run_id).status_code == 200
    assert _read(api_client, board["creator_token"], run_id).status_code == 404


def test_someone_outside_the_space_cannot_try_there(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    outsider = _login(user_client, api_client, user_client.create_user())
    sent = _trial(api_client, outsider, board["space_id"], files=FILES)
    assert sent.status_code == 404, sent.text


def test_reference_code_can_come_as_a_small_zip(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("src/main.py", FILES[0]["content"])
    sent = _trial(
        api_client,
        board["creator_token"],
        board["space_id"],
        zip=base64.b64encode(buffer.getvalue()).decode(),
    )
    assert sent.status_code == 200, sent.text


def test_a_path_that_climbs_out_is_refused_saying_so(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    sent = _trial(
        api_client,
        board["creator_token"],
        board["space_id"],
        files=[{"path": "../etc/passwd", "content": "x"}],
    )
    assert sent.status_code == 400, sent.text
    assert sent.json()["error"]["i18n"]["key"] == "judgeTrialBadPath"


def test_a_trial_without_code_is_refused(
    api_client: TestClient, user_client: UserCreator
):
    board = _new_board(user_client, api_client)
    sent = _trial(api_client, board["creator_token"], board["space_id"])
    assert sent.status_code == 400, sent.text
    assert sent.json()["error"]["i18n"]["key"] == "judgeTrialNoCode"
