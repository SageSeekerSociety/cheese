"""cheese_skill search / read over the real HTTP stack and database.

What is pinned: an AI teammate in a room finds the skills it can already load —
the platform's shipped skills and this project's **confirmed** skills — and
reads one whole. A teammate's still-pending draft is NOT installed, so it is
never returned; and search answers names and descriptions only, never a body
(which is what keeps the library out of the prompt).
"""

import uuid

from app.domain.skill_library import native_skill_doc, shipped_skill_names
from tests.conftest import seed_user
from tests.integration.conftest import (
    post_project,
    room_agent_headers,
    session_auth_headers,
)

SEARCH = "/agent/skills/search"
READ = "/agent/skills/read"


def _room(client) -> tuple[str, str, dict[str, str]]:
    handle = f"skill-reader-{uuid.uuid4().hex[:6]}"
    headers = session_auth_headers(handle)
    project = post_project(client, json={"name": "S"}, owner=handle).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "找技能"},
        headers=headers,
    ).json()["data"]
    return project["id"], topic["id"], headers


def _confirmed_skill(client, project_id: str, headers: dict) -> None:
    r = client.post(
        f"/projects/{project_id}/skills",
        json={
            "name": "weekly-report",
            "title": "周报",
            "description": "用户要这周的周报时",
            "body": "先写变坏的指标，再写下周计划。",
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text


def test_a_room_finds_a_project_skill_and_reads_it_whole(client):
    project_id, topic, headers = _room(client)
    _confirmed_skill(client, project_id, headers)
    agent = room_agent_headers(client, topic)

    found = client.post(SEARCH, json={"topic": topic, "query": "周报"}, headers=agent)
    assert found.status_code == 200, found.text
    [hit] = found.json()["data"]["skills"]
    assert hit["name"] == "weekly-report"
    assert hit["description"] == "用户要这周的周报时"
    # 搜索只回名字和说明，绝不回正文。
    assert "先写变坏的指标" not in found.text

    read = client.post(
        READ, json={"topic": topic, "name": "weekly-report"}, headers=agent
    )
    assert read.status_code == 200, read.text
    data = read.json()["data"]
    assert data["found"] is True
    assert "先写变坏的指标，再写下周计划。" in data["markdown"]


def test_the_platforms_shipped_skills_are_installed_for_every_room(client):
    """发货技能随每个会话在，搜索就该找得到——它不挂在项目上。"""
    project_id, topic, headers = _room(client)
    agent = room_agent_headers(client, topic)

    # They are installed for every agent even though none were manually added
    # to its per-agent configuration.
    catalog = client.get(
        f"/projects/{project_id}/skill-catalog",
        headers=headers,
    )
    assert catalog.status_code == 200, catalog.text
    expected = {skill["name"] for skill in catalog.json()["data"]["data"]}
    # 期望值取自平台自己的发货清单，而不是写死的条数：加一颗技能要不要改这里，
    # 由「它有没有 SKILL.md」决定（没有正文的名字上不了目录，见 _platform_catalog），
    # 写死条数只会让每次加技能都得来改一个魔数。
    assert expected == {
        name for name in shipped_skill_names() if native_skill_doc(name) is not None
    }

    found_names = set()
    for name in expected:
        found = client.post(
            SEARCH,
            json={"topic": topic, "query": name},
            headers=agent,
        )
        assert found.status_code == 200, found.text
        found_names.update(s["name"] for s in found.json()["data"]["skills"])
    assert found_names == expected


def test_a_publisher_with_no_project_picks_from_the_same_shipped_skills(client):
    """出题人写题时还没有项目：给题目挑技能读的是同一份发货目录，登录就能读。"""
    project_id, _, headers = _room(client)
    in_project = client.get(f"/projects/{project_id}/skill-catalog", headers=headers)
    token = seed_user(client, f"skill-publisher-{uuid.uuid4().hex[:6]}")

    catalog = client.get("/skill-catalog", headers={"Authorization": f"Bearer {token}"})

    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["data"]["data"] == in_project.json()["data"]["data"]
    assert client.get("/skill-catalog").status_code == 401


def test_a_teammate_draft_is_not_installed_and_so_is_not_returned(client):
    """队友还没被确认的草稿不算「已装」：搜索结果里不许出现它。"""
    _, topic, _ = _room(client)
    agent = room_agent_headers(client, topic)
    proposed = client.post(
        f"/topics/{topic}/skills",
        json={
            "name": "half-baked",
            "title": "半成品",
            "description": "用户提到半成品时",
            "body": "还没被任何人保存。",
            "taught": ["用户说了一句"],
            "accepted": "还没做",
        },
        headers=agent,
    )
    assert proposed.status_code == 200, proposed.text

    found = client.post(SEARCH, json={"topic": topic, "query": "半成品"}, headers=agent)
    assert found.status_code == 200, found.text
    assert found.json()["data"]["skills"] == []


def test_reading_a_name_that_is_not_installed_says_found_false(client):
    _, topic, _ = _room(client)
    agent = room_agent_headers(client, topic)

    read = client.post(READ, json={"topic": topic, "name": "nope"}, headers=agent)
    assert read.status_code == 200, read.text
    assert read.json()["data"]["found"] is False


def test_an_outsider_cannot_name_a_room_they_cannot_reach(client):
    """命名的房间不是调用者的，就拒——不能借别人的房间读别人的技能。"""
    _, topic, _ = _room(client)
    stranger = _room(client)[1]
    # 拿这个房间自己的 agent 凭据去问另一个房间：authorize_topic 会拒。
    headers = room_agent_headers(client, topic)
    refused = client.post(
        SEARCH, json={"topic": stranger, "query": "周报"}, headers=headers
    )
    assert refused.status_code in (401, 403, 404), refused.text
