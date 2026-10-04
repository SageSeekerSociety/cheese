"""领取：一条反馈有人在修，第二个人就领不到。

领取存在只为一件事：别让两个人修同一个问题。所以这里钉的是

* 领到的那一刻，提它的人看到「处理中」，时间线上写着谁、什么时候；
* 第二个人的领取**失败**，并且说出是谁在修 —— 同时到的也一样，只有一个赢；
* 放掉之后别人能领；已经修好、上线、不修复的，领了不动它的状态；
* 能领的是在做这个平台本身的人和 agent，agent 用的就是它手上那样工具。
"""

import asyncio
import importlib.util
import uuid
from datetime import UTC, datetime
from importlib.machinery import SourceFileLoader
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.domain.feedback.repositories import FeedbackRepository
from app.main import app
from tests.integration.conftest import (
    add_external_member,
    post_project,
    room_agent_headers,
    room_agent_seat,
    session_auth_headers,
)

ADMIN = "fbc-admin"
REPORTER = "fbc-reporter"
#: 平台自己那个项目的主人和成员 —— 「在做知是本身的人」。
DEV = "fbc-dev"
DEV2 = "fbc-dev2"
#: 和平台项目无关的人：看得见公开反馈，但不在做这个平台。
STRANGER = "fbc-stranger"

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load_tools():
    loader = SourceFileLoader("cheese_platform_tools_claim", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load_tools()


def _project(client, owner: str) -> str:
    return post_project(
        client, json={"name": "P"}, headers=session_auth_headers(owner)
    ).json()["data"]["id"]


def _topic(client, project: str, owner: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project, "title": "修反馈"},
        headers=session_auth_headers(owner),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


@pytest.fixture
def platform(client, monkeypatch) -> str:
    """The platform's own project: DEV owns it, DEV2 was invited in.

    It counts as the platform's because its repository is the one
    `settings.docs_dev_repositories` names — the test forge gives every project
    the repository `project-<hex>/code`.
    """
    monkeypatch.setattr(settings, "feedback_triage_handles", [ADMIN])
    project = _project(client, DEV)
    monkeypatch.setattr(
        settings, "docs_dev_repositories", [f"project-{uuid.UUID(project).hex}/code"]
    )
    add_external_member(client, project, DEV2, by=DEV)
    return project


def _report(client, **body) -> dict:
    r = client.post(
        "/feedback",
        json={"title": "按钮点了没反应", **body},
        headers=session_auth_headers(REPORTER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _claim(client, who: str, ref: str) -> httpx.Response:
    return client.post(f"/feedback/{ref}/claim", headers=session_auth_headers(who))


def _release(client, who: str, ref: str) -> httpx.Response:
    return client.delete(f"/feedback/{ref}/claim", headers=session_auth_headers(who))


def _view(client, who: str, feedback_id: str) -> dict:
    r = client.get(f"/feedback/{feedback_id}", headers=session_auth_headers(who))
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _set_status(client, feedback_id: str, status: str) -> None:
    r = client.post(
        f"/admin/feedback/{feedback_id}/status",
        json={"status": status},
        headers=session_auth_headers(ADMIN),
    )
    assert r.status_code == 200, r.text


def test_claiming_shows_the_reporter_it_is_in_progress_and_who_took_it(
    client, platform
):
    row = _report(client)
    before = datetime.now(UTC)

    r = _claim(client, DEV, row["id"])

    assert r.status_code == 200, r.text
    seen = _view(client, REPORTER, row["id"])
    assert seen["status"] == "in_progress"
    assert seen["assignee_handle"] == DEV
    step = next(t for t in seen["timeline"] if t["status"] == "in_progress")
    assert step["by_handle"] == DEV
    assert step["note"] == "已领取"
    assert before <= datetime.fromisoformat(step["at"]) <= datetime.now(UTC)


def test_a_second_claim_fails_and_says_who_is_on_it(client, platform):
    row = _report(client)
    assert _claim(client, DEV, row["id"]).status_code == 200

    r = _claim(client, DEV2, row["id"])

    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert DEV in error["message"]
    assert error["data"]["holder"] == DEV
    assert _view(client, REPORTER, row["id"])["assignee_handle"] == DEV


def test_claiming_again_as_the_holder_is_not_a_second_step(client, platform):
    row = _report(client)

    first = _claim(client, DEV, row["id"])
    again = _claim(client, DEV, row["id"])

    assert (first.status_code, again.status_code) == (200, 200)
    seen = _view(client, REPORTER, row["id"])
    assert [t["status"] for t in seen["timeline"]] == ["received", "in_progress"]


def test_claims_that_arrive_together_have_exactly_one_winner(
    client, platform, monkeypatch
):
    """Six developers press 领取 at once: one gets it, five are told who did.

    Left alone, the six requests rarely overlap — the first one is usually done
    and committed before the second has looked. So every claim that gets as far
    as writing its name is held there until all six have arrived (or a second
    has passed): the window between 「没人领着」 and writing the name is as wide
    as it can be. A claim that looks without locking reaches the write six times.
    """
    row = _report(client)
    claimers = [f"fbc-racer-{n}" for n in range(6)]
    for handle in claimers:
        add_external_member(client, platform, handle, by=DEV)

    write_name = FeedbackRepository.set_assignee
    arrived: list[str | None] = []

    async def write_when_everyone_is_here(self, row, assignee):
        arrived.append(assignee)
        deadline = asyncio.get_running_loop().time() + 1.0
        while len(arrived) < len(claimers):
            if asyncio.get_running_loop().time() > deadline:
                break
            await asyncio.sleep(0.01)
        await write_name(self, row, assignee)

    monkeypatch.setattr(FeedbackRepository, "set_assignee", write_when_everyone_is_here)

    async def race() -> list[httpx.Response]:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            return await asyncio.gather(
                *[
                    http.post(
                        f"/feedback/{row['id']}/claim",
                        headers=session_auth_headers(handle),
                    )
                    for handle in claimers
                ]
            )

    answers = client.portal.call(race)

    codes = sorted(a.status_code for a in answers)
    assert codes == [200, 409, 409, 409, 409, 409], [a.text for a in answers]
    winner = next(
        h for h, a in zip(claimers, answers, strict=True) if a.status_code == 200
    )
    assert all(
        a.json()["error"]["data"]["holder"] == winner
        for a in answers
        if a.status_code == 409
    )
    seen = _view(client, REPORTER, row["id"])
    assert seen["assignee_handle"] == winner
    assert [t["status"] for t in seen["timeline"]] == ["received", "in_progress"]


def test_after_the_holder_releases_someone_else_can_claim(client, platform):
    row = _report(client)
    assert _claim(client, DEV, row["id"]).status_code == 200

    released = _release(client, DEV, row["id"])

    assert released.status_code == 200, released.text
    seen = _view(client, REPORTER, row["id"])
    assert seen["assignee_handle"] is None
    # Somebody did work on it; letting go does not unsay that.
    assert seen["status"] == "in_progress"
    assert _claim(client, DEV2, row["id"]).status_code == 200
    assert _view(client, REPORTER, row["id"])["assignee_handle"] == DEV2


def test_only_the_holder_or_a_feedback_admin_can_release(client, platform):
    row = _report(client)
    assert _claim(client, DEV, row["id"]).status_code == 200

    refused = _release(client, DEV2, row["id"])
    assert refused.status_code == 403, refused.text
    assert DEV in refused.json()["error"]["message"]
    assert _view(client, REPORTER, row["id"])["assignee_handle"] == DEV

    assert _release(client, ADMIN, row["id"]).status_code == 200
    assert _view(client, REPORTER, row["id"])["assignee_handle"] is None


@pytest.mark.parametrize("closed", ["declined", "resolved", "deployed"])
def test_claiming_a_finished_report_leaves_its_status_alone(client, platform, closed):
    row = _report(client)
    _set_status(client, row["id"], closed)

    r = _claim(client, DEV, row["id"])

    assert r.status_code == 200, r.text
    seen = _view(client, REPORTER, row["id"])
    assert seen["status"] == closed
    assert seen["assignee_handle"] == DEV
    assert [t["status"] for t in seen["timeline"]] == ["received", closed]


def test_only_people_working_on_the_platform_can_claim(client, platform):
    row = _report(client)

    stranger = _claim(client, STRANGER, row["id"])
    assert stranger.status_code == 403, stranger.text
    nobody = client.post(f"/feedback/{row['id']}/claim")
    assert nobody.status_code == 401, nobody.text
    # A feedback admin is in no project and still may.
    assert _claim(client, ADMIN, row["id"]).status_code == 200


def test_a_report_the_claimer_cannot_see_is_not_found(client, platform):
    hidden = _report(client, visibility="private")

    r = _claim(client, DEV, hidden["id"])

    assert r.status_code == 404, r.text
    assert _claim(client, DEV, "FB-999999").status_code == 404


def test_the_detail_tells_each_reader_whether_they_can_claim_or_release(
    client, platform
):
    row = _report(client)
    assert _view(client, DEV, row["id"])["can_claim"] is True
    assert _view(client, STRANGER, row["id"])["can_claim"] is False

    assert _claim(client, DEV, row["id"]).status_code == 200

    holder = _view(client, DEV, row["id"])
    other = _view(client, DEV2, row["id"])
    admin = _view(client, ADMIN, row["id"])
    assert (holder["can_claim"], holder["can_release"]) == (False, True)
    assert (other["can_claim"], other["can_release"]) == (False, False)
    assert (admin["can_claim"], admin["can_release"]) == (False, True)


# --- 芝士用它手上的工具领 -------------------------------------------------------


class AgentHost:
    """A session host for the agent of `topic`, on its own room credential."""

    def __init__(self, client, project: str, topic: str):
        self.client = client
        self.environ = {"CHEESE_TOPIC": topic, "CHEESE_PROJECT": project}
        self.headers = room_agent_headers(client, topic)
        self.doc_versions: dict = {}

    def request(self, plan):
        response = self.client.request(
            plan["method"], plan["path"], json=plan.get("body"), headers=self.headers
        )
        if not 200 <= response.status_code < 300:
            raise cheese.PlatformHTTPError(response.status_code, response.text)
        return response.json()

    def run(self, tool: str, **args) -> str:
        return cheese.run_platform_tool(tool, args, self)


def test_an_agent_on_the_platform_claims_with_its_tool_and_a_person_is_then_refused(
    client, platform
):
    row = _report(client)
    topic = _topic(client, platform, DEV)
    agent = AgentHost(client, platform, topic)

    said = agent.run("cheese_feedback_claim", feedback=row["display_id"])

    assert row["display_id"] in said
    assert "记在你名下" in said
    seat = room_agent_seat(client, topic)
    seen = _view(client, REPORTER, row["id"])
    assert seen["assignee_handle"] == seat
    assert seen["status"] == "in_progress"
    refused = _claim(client, DEV2, row["id"])
    assert refused.status_code == 409
    assert seat in refused.json()["error"]["message"]

    agent.run("cheese_feedback_release", feedback=row["display_id"])
    assert _view(client, REPORTER, row["id"])["assignee_handle"] is None


def test_an_agent_is_told_not_to_fix_what_someone_else_holds(client, platform):
    row = _report(client)
    assert _claim(client, DEV2, row["id"]).status_code == 200
    topic = _topic(client, platform, DEV)
    agent = AgentHost(client, platform, topic)

    with pytest.raises(cheese.PlatformToolError) as refusal:
        agent.run("cheese_feedback_claim", feedback=row["display_id"])

    assert "不要修" in str(refusal.value)
    assert DEV2 in str(refusal.value)
    assert _view(client, REPORTER, row["id"])["assignee_handle"] == DEV2


def test_an_agent_outside_the_platform_cannot_claim(client, platform):
    row = _report(client)
    elsewhere = _project(client, STRANGER)
    topic = _topic(client, elsewhere, STRANGER)
    agent = AgentHost(client, elsewhere, topic)

    with pytest.raises(cheese.PlatformToolError) as refusal:
        agent.run("cheese_feedback_claim", feedback=row["display_id"])

    assert "不要修" in str(refusal.value)
    assert _view(client, REPORTER, row["id"])["assignee_handle"] is None
