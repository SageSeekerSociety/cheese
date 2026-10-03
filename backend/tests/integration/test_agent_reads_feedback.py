"""芝士读反馈中心：在做平台本身的房间里读得到，别处读不到，私密的和人一样。

agent 的凭据只认一个房间，所以它读反馈中心时说出房间（`?topic=`）。钉住的是：

* 平台项目里的房间：列表（带筛选）、详情（正文、时间线、评论）都读得到；
* 别的项目里的房间、不是自己的房间：拒；
* 私密和安全问题：agent 看见的和项目里一个不是反馈管理员的成员一样多；
* 工具从头到尾：列出来、读全文、领下来。
"""

import importlib.util
import uuid
from importlib.machinery import SourceFileLoader
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from tests.integration.conftest import (
    add_external_member,
    post_project,
    room_agent_headers,
    room_agent_seat,
)
from tests.integration.conftest import session_auth_headers as signed_in

ADMIN = "fbr-admin"
REPORTER = "fbr-reporter"
#: 平台项目的主人和受邀成员，都不是反馈管理员。
DEV = "fbr-dev"
DEV2 = "fbr-dev2"
STRANGER = "fbr-stranger"

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load_tools():
    loader = SourceFileLoader("cheese_platform_tools_read", str(_CHEESE))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


cheese = _load_tools()


def _project(client, owner: str) -> str:
    return post_project(client, json={"name": "P"}, headers=signed_in(owner)).json()[
        "data"
    ]["id"]


def _topic(client, project: str, owner: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": project, "title": "修反馈"},
        headers=signed_in(owner),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


@pytest.fixture
def platform(client, monkeypatch) -> str:
    """The platform's own project (its repository is the one the settings name)."""
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
        headers=signed_in(REPORTER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _comment(client, who: str, feedback_id: str, text: str) -> None:
    r = client.post(
        f"/feedback/{feedback_id}/comments", json={"body": text}, headers=signed_in(who)
    )
    assert r.status_code == 200, r.text


def _as_agent(client, topic: str, method: str, path: str, **params) -> httpx.Response:
    return client.request(
        method,
        path,
        params={"topic": topic, **params},
        headers=room_agent_headers(client, topic),
    )


def _human_detail(client, who: str, ref: str) -> httpx.Response:
    return client.get(f"/feedback/{ref}", headers=signed_in(who))


def _filed_in_room(client, project: str, topic: str, by: str, **body) -> dict:
    """The room's agent proposes, `by` presses 提交反馈 — the one way a report
    gets a room of origin."""
    proposed = client.post(
        f"/topics/{topic}/feedback-proposals",
        json={
            "title": "沙箱里 make 装不上依赖",
            "what_happened": "make 停在 could not resolve host",
            "user_said": "用户没有就这个问题说过话，以上是芝士自己观察到的",
        },
        headers=room_agent_headers(client, topic),
    )
    assert proposed.status_code == 200, proposed.text
    block = proposed.json()["data"]["block_id"]
    sent = client.post(
        f"/topics/{topic}/feedback-proposals/{block}/accept",
        json={"title": "沙箱里 make 装不上依赖", **body},
        headers=signed_in(by),
    )
    assert sent.status_code == 200, sent.text
    return sent.json()["data"]


# --- 路由 -------------------------------------------------------------------


def test_an_agent_in_a_platform_room_lists_searches_and_reads_a_report(
    client, platform
):
    wanted = _report(client, problem="点保存没有任何反应", kind="bug")
    other = _report(client, title="希望能导出 PDF", kind="suggestion")
    _comment(client, DEV2, wanted["id"], "我这里也复现了")
    topic = _topic(client, platform, DEV)

    listed = _as_agent(client, topic, "GET", "/feedback")
    assert listed.status_code == 200, listed.text
    ids = [c["display_id"] for c in listed.json()["data"]["data"]]
    assert {wanted["display_id"], other["display_id"]} <= set(ids)

    suggestions = _as_agent(client, topic, "GET", "/feedback", kind="suggestion")
    assert [c["display_id"] for c in suggestions.json()["data"]["data"]] == [
        other["display_id"]
    ]
    searched = _as_agent(client, topic, "GET", "/feedback", q="导出")
    assert [c["display_id"] for c in searched.json()["data"]["data"]] == [
        other["display_id"]
    ]

    detail = _as_agent(client, topic, "GET", f"/feedback/{wanted['display_id']}")
    assert detail.status_code == 200, detail.text
    seen = detail.json()["data"]
    assert seen["problem"] == "点保存没有任何反应"
    assert [t["status"] for t in seen["timeline"]] == ["received"]
    assert [c["body"] for c in seen["thread"]] == ["我这里也复现了"]
    assert seen["can_claim"] is True

    comments = _as_agent(client, topic, "GET", f"/feedback/{wanted['id']}/comments")
    assert comments.status_code == 200, comments.text
    assert [c["body"] for c in comments.json()["data"]["items"]] == ["我这里也复现了"]


def test_an_agent_outside_the_platform_is_refused_and_told_why(client, platform):
    row = _report(client)
    elsewhere = _project(client, STRANGER)
    topic = _topic(client, elsewhere, STRANGER)

    for path in (
        "/feedback",
        f"/feedback/{row['display_id']}",
        f"/feedback/{row['id']}/comments",
    ):
        r = _as_agent(client, topic, "GET", path)
        assert r.status_code == 403, (path, r.text)
        assert "代码仓库" in r.json()["error"]["message"], r.text


def test_an_agent_cannot_read_from_a_room_that_is_not_its_own(client, platform):
    row = _report(client)
    mine = _topic(client, platform, DEV)
    theirs = _topic(client, platform, DEV)

    r = client.get(
        f"/feedback/{row['display_id']}",
        params={"topic": theirs},
        headers=room_agent_headers(client, mine),
    )

    assert r.status_code == 403, r.text


def test_an_agent_that_names_no_room_is_still_refused(client, platform):
    topic = _topic(client, platform, DEV)

    r = client.get("/feedback", headers=room_agent_headers(client, topic))

    assert r.status_code == 403, r.text


def test_people_read_the_center_as_before_without_naming_a_room(client, platform):
    row = _report(client)

    assert client.get("/feedback").status_code == 200
    assert _human_detail(client, STRANGER, row["display_id"]).status_code == 200
    assert _human_detail(client, STRANGER, row["id"]).status_code == 200


# --- 私密：agent 看见的和一个不是反馈管理员的成员一样 ---------------------------------


def test_a_private_report_someone_else_filed_is_hidden_from_agent_and_developer_alike(
    client, platform
):
    hidden = _report(client, visibility="private")
    topic = _topic(client, platform, DEV)

    agent = _as_agent(client, topic, "GET", f"/feedback/{hidden['display_id']}")

    assert agent.status_code == 404, agent.text
    assert _human_detail(client, DEV, hidden["display_id"]).status_code == 404
    assert _human_detail(client, ADMIN, hidden["display_id"]).status_code == 200
    listed = _as_agent(client, topic, "GET", "/feedback").json()["data"]["data"]
    assert hidden["display_id"] not in [c["display_id"] for c in listed]


def test_a_report_flagged_as_a_security_matter_is_hidden_from_agent_and_developer(
    client, platform
):
    row = _report(client)
    flagged = client.patch(
        f"/admin/feedback/{row['id']}",
        json={"security": True},
        headers=signed_in(ADMIN),
    )
    assert flagged.status_code == 200, flagged.text
    topic = _topic(client, platform, DEV)

    agent = _as_agent(client, topic, "GET", f"/feedback/{row['display_id']}")

    assert agent.status_code == 404, agent.text
    assert _human_detail(client, DEV, row["display_id"]).status_code == 404


def test_a_private_report_the_agent_proposed_is_read_by_it_and_its_room_only(
    client, platform
):
    """The agent proposed it and DEV sent it from DEV's room: both read it, as
    any author and any person in that room would.

    DEV2 is in the project but was not in that room: 404, as for a stranger.
    """
    room = _topic(client, platform, DEV)
    row = _filed_in_room(client, platform, room, DEV, visibility="private")

    agent = _as_agent(client, room, "GET", f"/feedback/{row['display_id']}")

    assert agent.status_code == 200, agent.text
    assert _human_detail(client, DEV, row["display_id"]).status_code == 200
    assert _human_detail(client, DEV2, row["display_id"]).status_code == 404


# --- 芝士用它手上的工具读 -------------------------------------------------------


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


def test_an_agent_finds_reads_and_then_claims_a_report_with_its_tools(client, platform):
    row = _report(client, title="保存按钮失灵", problem="点保存没有任何反应")
    _report(client, title="希望能导出 PDF", kind="suggestion")
    _comment(client, DEV2, row["id"], "我这里也复现了")
    topic = _topic(client, platform, DEV)
    agent = AgentHost(client, platform, topic)

    listed = agent.run("cheese_feedback_list", query="保存")
    assert row["display_id"] in listed
    assert "导出 PDF" not in listed

    read = agent.run("cheese_feedback_get", feedback=row["display_id"])
    assert "点保存没有任何反应" in read
    assert "我这里也复现了" in read
    assert "已收录" in read
    assert "cheese_feedback_claim" in read

    claimed = agent.run("cheese_feedback_claim", feedback=row["display_id"])
    assert row["display_id"] in claimed

    again = agent.run("cheese_feedback_get", feedback=row["display_id"])
    assert "处理中" in again
    assert room_agent_seat(client, topic) in again


def test_the_read_tools_say_why_an_agent_outside_the_platform_reads_nothing(
    client, platform
):
    row = _report(client)
    elsewhere = _project(client, STRANGER)
    agent = AgentHost(client, elsewhere, _topic(client, elsewhere, STRANGER))

    with pytest.raises(cheese.PlatformToolError) as listing:
        agent.run("cheese_feedback_list")
    with pytest.raises(cheese.PlatformToolError) as reading:
        agent.run("cheese_feedback_get", feedback=row["display_id"])

    assert "代码仓库" in str(listing.value)
    assert "代码仓库" in str(reading.value)


def test_the_read_tool_says_a_hidden_report_is_not_there(client, platform):
    hidden = _report(client, visibility="private")
    agent = AgentHost(client, platform, _topic(client, platform, DEV))

    with pytest.raises(cheese.PlatformToolError) as refusal:
        agent.run("cheese_feedback_get", feedback=hidden["display_id"])

    assert "不要修" in str(refusal.value)
