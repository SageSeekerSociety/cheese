"""学生交了作业，评测排上队、派到评测主机、写回结果、记在空间所有者头上（方案
§一次评测怎么跑、§算力与费用、§边界情况）。

评测主机是一个假的 hub（``device_hub.device_hub`` 换成它）：它不跑任何东西，按每道题
定好的样子回一份评测脚本的结果，或者像掉线的主机那样抛错。代码从学生项目的本地仓库
真取（``code.remote_for`` 指到那个仓库），其余都是真的：队列、租约、计费、结果读回。
这里只看数据库里落下了什么、学生读到了什么。
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import settings
from app.core.device_failures import DeviceOffline
from app.domain.agent import device_hub as hub_module
from app.domain.judge import code, dispatch
from app.domain.project.starter import Remote
from app.domain.task.judging_models import TaskJudgeRun
from app.domain.usage.ledger import payer_for_person
from app.domain.usage.models import ResourceUsage
from tests.integration.conftest import UserCreator, set_free_plan_credits
from tests.integration.test_task_files import (
    _auth,
    _create_task,
    _member_of,
    _new_board,
    upload_root,  # noqa: F401 — 建题要的那个夹具
)
from tests.support import git_store

HOST = "judge-host-1"
PRICE = 2.0  # credits per minute of judging

SUITE = [
    {
        "kind": "IO",
        "runtime": "python3.12",
        "runCmd": "python3 main.py",
        "cases": [
            {
                "name": "样例",
                "points": 60,
                "visibility": "SAMPLE",
                "input": "1 2\n",
                "expected": "3\n",
            },
            {
                "name": "边界",
                "points": 40,
                "visibility": "HIDDEN",
                "input": "100 200\n",
                "expected": "300\n",
            },
        ],
    }
]


class FakeHub:
    """A judge host: online, and answering each judging with ``answer``."""

    def __init__(self) -> None:
        self.online = {HOST}
        self.answer = self.first_case_passes

    def is_online(self, device_id: str) -> bool:
        return device_id in self.online

    async def exec(self, device_id, argv, *, stdin=None, timeout=60, **_):
        return await self.answer(json.loads(stdin or "{}"))

    @staticmethod
    async def first_case_passes(job: dict) -> dict:
        cases = [
            {
                "index": case["index"],
                "status": "ok" if case["index"] == 0 else "wrong",
                "ms": 10 + case["index"],
                "memoryKb": 4096,
                "actual": case["expected"] if case["index"] == 0 else "500\n",
            }
            for case in job["cases"]
        ]
        reply = {
            "ok": True,
            "compile": {"status": "ok", "output": ""},
            "cases": cases,
            "machineMs": 61_000,
        }
        return {"stdout": json.dumps(reply), "stderr": "", "exit": 0}


@pytest.fixture
def hub(monkeypatch) -> FakeHub:
    fake = FakeHub()
    monkeypatch.setattr(hub_module, "device_hub", fake)
    monkeypatch.setattr(settings, "judge_device_ids", [HOST])
    monkeypatch.setattr(settings, "judge_slots_per_device", 1)
    monkeypatch.setattr(settings, "judge_credits_per_minute", PRICE)

    async def local_repository(project_id, session):
        return Remote(
            url=f"file://{git_store.path(project_id)}", username="x", password="y"
        )

    monkeypatch.setattr(code, "remote_for", local_repository)
    return fake


@pytest.fixture
def judge(db_session, _portal):
    """Run one tick of the judge clock."""

    @asynccontextmanager
    async def sessions():
        yield db_session

    async def tick():
        return await dispatch.run_due(sessions)

    return lambda: _portal.call(tick)


@pytest.fixture
def credits(db_session, _portal):
    """Give every Free-plan team ``n`` credits a month."""
    return lambda n: _portal.call(set_free_plan_credits, db_session, n)


def _setup(api_client: TestClient, user_client: UserCreator, *, suite=SUITE):
    board = _new_board(user_client, api_client)
    task_id = _create_task(api_client, board, name="评测", testSuite=suite)
    _, token = _member_of(user_client, api_client, board)
    started = api_client.post(
        f"/tasks/{task_id}/participations/user", json={}, headers=_auth(token)
    )
    assert started.status_code == 200, started.text
    data = started.json()["data"]
    return board, task_id, data["participant"]["id"], data["project"]["id"], token


def _push(project_id: str, text: str) -> str:
    repo = git_store.ensure_repo(uuid.UUID(project_id))
    (repo / "main.py").write_text(text)
    git_store.git(repo, "add", "main.py")
    git_store.git(repo, "commit", "-qm", "solve")
    return git_store.head(uuid.UUID(project_id))


def _submit(api_client: TestClient, task_id: int, participant: int, token: str) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participants/{participant}/submissions",
        json=[{"text": "我的作业"}],
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["submission"]["id"]


def _judge_of(api_client, task_id, participant, submission, token) -> dict | None:
    resp = api_client.get(
        f"/tasks/{task_id}/participants/{participant}/submissions/{submission}/judge",
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["judge"]


def _judge_charges(db_session, _portal) -> list[ResourceUsage]:
    async def read():
        return list(
            await db_session.scalars(
                select(ResourceUsage).where(ResourceUsage.kind == "judge")
            )
        )

    return _portal.call(read)


def _runs(db_session, _portal, task_id: int) -> list[TaskJudgeRun]:
    async def read():
        rows = await db_session.scalars(
            select(TaskJudgeRun)
            .where(TaskJudgeRun.task_id == task_id)
            .order_by(TaskJudgeRun.id)
            .execution_options(populate_existing=True)
        )
        return list(rows)

    return _portal.call(read)


def _ready(api_client, user_client, credits, *, suite=SUITE):
    board, task_id, participant, project_id, token = _setup(
        api_client, user_client, suite=suite
    )
    credits(100)
    _push(project_id, "a, b = map(int, input().split())\nprint(a + b)\n")
    submission = _submit(api_client, task_id, participant, token)
    return board, task_id, participant, project_id, token, submission


def test_a_handed_in_version_is_judged_and_the_space_owner_pays(
    api_client, user_client, db_session, _portal, hub, judge, credits
):
    board, task_id, participant, project_id, token, submission = _ready(
        api_client, user_client, credits
    )
    queued = _judge_of(api_client, task_id, participant, submission, token)
    assert queued is not None and queued["state"] == "queued"

    judge()

    result = _judge_of(api_client, task_id, participant, submission, token)
    assert result["state"] == "done"
    assert (result["passedPoints"], result["totalPoints"]) == (60, 100)
    assert [c["status"] for c in result["cases"]] == ["ok", "wrong"]
    # The hidden case's expected output never reaches the student.
    assert "expected" not in result["cases"][1]

    (charge,) = _judge_charges(db_session, _portal)
    owner_team = _portal.call(payer_for_person, db_session, board["creator"].user_id)
    # 61 s on the host is two started minutes, charged to the owner's own team,
    # and to no project: never the student's.
    assert charge.credits == pytest.approx(2 * PRICE)
    assert charge.team_id == owner_team.team_id
    assert charge.project_id is None
    assert str(charge.project_id) != project_id


def test_the_same_commit_handed_in_again_is_not_judged_twice(
    api_client, user_client, db_session, _portal, hub, judge, credits
):
    _, task_id, participant, _, token, first = _ready(api_client, user_client, credits)
    judge()
    again = _submit(api_client, task_id, participant, token)

    reused = _judge_of(api_client, task_id, participant, again, token)
    assert reused["state"] == "done"
    assert (reused["passedPoints"], reused["totalPoints"]) == (60, 100)
    judge()
    assert len(_judge_charges(db_session, _portal)) == 1


def test_a_stage_without_test_points_queues_nothing(
    api_client, user_client, hub, credits
):
    _, task_id, participant, _, token, submission = _ready(
        api_client, user_client, credits, suite=[]
    )
    assert _judge_of(api_client, task_id, participant, submission, token) is None


def test_an_owner_without_credits_gets_a_failed_judging_that_says_why(
    api_client, user_client, db_session, _portal, hub, judge, credits
):
    _, task_id, participant, _, token, submission = _ready(
        api_client, user_client, credits
    )
    credits(0)
    judge()

    result = _judge_of(api_client, task_id, participant, submission, token)
    assert result["state"] == "failed"
    assert result["error"]["key"].startswith("credits")
    assert _judge_charges(db_session, _portal) == []


@pytest.mark.parametrize(
    ("setting", "value", "key"),
    [
        ("judge_device_ids", [], "judgeUnavailable"),
        ("judge_credits_per_minute", None, "judgeUnpriced"),
    ],
)
def test_without_a_judge_host_or_a_price_judging_is_unavailable_and_free(
    api_client,
    user_client,
    db_session,
    _portal,
    hub,
    judge,
    credits,
    monkeypatch,
    setting,
    value,
    key,
):
    _, task_id, participant, _, token, submission = _ready(
        api_client, user_client, credits
    )
    monkeypatch.setattr(settings, setting, value)
    judge()

    result = _judge_of(api_client, task_id, participant, submission, token)
    assert result["state"] == "failed"
    assert result["error"]["key"] == key
    assert _judge_charges(db_session, _portal) == []


def test_a_platform_failure_is_tried_once_more_then_fails_costing_nothing(
    api_client, user_client, db_session, _portal, hub, judge, credits
):
    _, task_id, participant, _, token, submission = _ready(
        api_client, user_client, credits
    )

    async def gone(job):
        raise DeviceOffline(HOST)

    hub.answer = gone
    judge()
    waiting = _judge_of(api_client, task_id, participant, submission, token)
    assert waiting["state"] == "queued"

    async def due_now():
        await db_session.execute(
            update(TaskJudgeRun)
            .where(TaskJudgeRun.task_id == task_id)
            .values(retry_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    _portal.call(due_now)
    judge()

    result = _judge_of(api_client, task_id, participant, submission, token)
    assert result["state"] == "failed"
    assert result["error"]["key"] == "judgePlatformFailed"
    assert _judge_charges(db_session, _portal) == []


def test_a_worker_whose_lease_was_taken_over_writes_nothing(
    api_client, user_client, db_session, _portal, hub, judge, credits
):
    _, task_id, participant, _, token, submission = _ready(
        api_client, user_client, credits
    )
    other = uuid.uuid4()
    passes = hub.answer

    async def taken_over_meanwhile(job):
        await db_session.execute(
            update(TaskJudgeRun)
            .where(TaskJudgeRun.task_id == task_id)
            .values(
                claim_token=other,
                lease_until=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        return await passes(job)

    hub.answer = taken_over_meanwhile
    judge()

    (run,) = _runs(db_session, _portal, task_id)
    assert run.state == "running" and run.claim_token == other
    assert run.results is None
    assert _judge_charges(db_session, _portal) == []
    assert _judge_of(api_client, task_id, participant, submission, token)["state"] == (
        "running"
    )


def test_two_workers_never_lease_the_same_judging(
    api_client, user_client, db_session, _portal, hub, credits
):
    _, task_id, participant, project_id, token, _ = _ready(
        api_client, user_client, credits
    )
    _push(project_id, "print(3)\n")
    _submit(api_client, task_id, participant, token)

    @asynccontextmanager
    async def sessions():
        yield db_session

    first = _portal.call(dispatch.claim_next, sessions, HOST)
    second = _portal.call(dispatch.claim_next, sessions, HOST)
    assert first is not None and second is not None
    assert first[0] != second[0]
    assert _portal.call(dispatch.claim_next, sessions, HOST) is None

    async def lease_runs_out():
        await db_session.execute(
            update(TaskJudgeRun)
            .where(TaskJudgeRun.id == first[0])
            .values(lease_until=datetime.now(UTC) - timedelta(seconds=1))
        )

    _portal.call(lease_runs_out)
    again = _portal.call(dispatch.claim_next, sessions, HOST)
    assert again is not None and again[0] == first[0] and again[1] != first[1]
    runs = {run.id: run for run in _runs(db_session, _portal, task_id)}
    assert runs[first[0]].attempts == 2
