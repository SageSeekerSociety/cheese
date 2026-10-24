"""Drafts of 题目: saved as the person writes, written into by their 芝士, and
published — a new 题目, or the changes saved onto a published one.

芝士's routes are reached with the credential a question is answered with, and
act as the person who asked: what they may publish, it may publish; a field
the person changed after 芝士 read the draft stays as the person left it.
"""

import io
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.sandbox_auth import mint_delegated_credential
from tests.integration.conftest import UserCreator, create_approved_space, unique_int


def _login(user_client: UserCreator, api_client: TestClient):
    user = user_client.create_user()
    user.token = user_client.login(api_client, user.username, user.password)
    return user


def _headers(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


def _as_cheese(user) -> dict:
    """The credential a question this person asked is answered with."""
    token = mint_delegated_credential(
        user_id=user.user_id,
        handle=user.username,
        work=str(uuid.uuid4()),
        ttl_s=300,
        read_only=False,
    )
    return {"X-Cheese-Token": token}


@pytest.fixture
def board(user_client: UserCreator, api_client: TestClient) -> dict:
    teacher = _login(user_client, api_client)
    stranger = _login(user_client, api_client)
    suffix = unique_int(10000000, 99999999)
    space = create_approved_space(
        api_client,
        json={
            "name": f"Draft Space ({suffix})",
            "intro": "drafts",
            "description": "A lengthy description. " * 20,
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
        },
        headers=_headers(teacher),
    ).json()["data"]["space"]
    return {"teacher": teacher, "stranger": stranger, "space": space}


def _start(api_client, board, **body) -> dict:
    resp = api_client.post(
        "/task-drafts",
        json={"space": board["space"]["id"], **body},
        headers=_headers(board["teacher"]),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _deadline_ms(days: int = 7) -> int:
    return int((datetime.now(UTC).timestamp() + days * 24 * 3600) * 1000)


FORM = {
    "name": "Lab 3 缓存模拟器",
    "description": "用 C 写一个缓存模拟器。",
    "descriptionMarkdown": "用 C 写一个缓存模拟器。",
    "submitterType": "USER",
    "rank": 2,
    "submissionSchema": [{"prompt": "csim.c", "type": "FILE"}],
    "rubric": [{"item": "正确性", "points": 60, "criteria": "全对"}],
}


def test_a_draft_is_saved_as_written_and_published_as_a_task(
    api_client: TestClient, board: dict
):
    draft = _start(api_client, board)
    teacher = board["teacher"]
    # A blank draft lists what it still needs before it can be published.
    assert draft["missing"] == ["name", "description", "form", "difficulty"]

    saved = api_client.patch(
        f"/task-drafts/{draft['id']}",
        json={"fields": {**FORM, "deadline": _deadline_ms()}},
        headers=_headers(teacher),
    )
    assert saved.status_code == 200, saved.text

    listed = api_client.get(
        f"/task-drafts?space={board['space']['id']}", headers=_headers(teacher)
    ).json()["data"]["drafts"]
    assert [d["name"] for d in listed] == ["Lab 3 缓存模拟器"]
    assert listed[0]["missing"] == []

    # Somebody else's draft reads as missing.
    other = api_client.get(
        f"/task-drafts/{draft['id']}", headers=_headers(board["stranger"])
    )
    assert other.status_code == 404

    published = api_client.post(
        f"/task-drafts/{draft['id']}/publish", headers=_headers(teacher)
    )
    assert published.status_code == 200, published.text
    task = published.json()["data"]["task"]
    assert task["name"] == "Lab 3 缓存模拟器"
    assert task["intro"].startswith("用 C 写一个缓存模拟器")
    read = api_client.get(f"/tasks/{task['id']}", headers=_headers(teacher))
    assert read.json()["data"]["task"]["rubric"] == [
        {**line, "stage": 0} for line in FORM["rubric"]
    ]

    gone = api_client.get(f"/task-drafts/{draft['id']}", headers=_headers(teacher))
    assert gone.status_code == 404


def test_changes_to_a_published_task_are_a_draft_until_saved(
    api_client: TestClient, board: dict
):
    teacher = board["teacher"]
    draft = _start(api_client, board, fields={**FORM, "deadline": _deadline_ms()})
    task = api_client.post(
        f"/task-drafts/{draft['id']}/publish", headers=_headers(teacher)
    ).json()["data"]["task"]

    changes = api_client.post(
        "/task-drafts", json={"task": task["id"]}, headers=_headers(teacher)
    ).json()["data"]
    assert changes["taskId"] == task["id"]
    assert changes["fields"]["name"] == "Lab 3 缓存模拟器"
    # Opening the edit page again opens the same changes.
    again = api_client.post(
        "/task-drafts", json={"task": task["id"]}, headers=_headers(teacher)
    ).json()["data"]
    assert again["id"] == changes["id"]

    api_client.patch(
        f"/task-drafts/{changes['id']}",
        json={"fields": {"name": "Lab 3 缓存模拟器（修订）"}},
        headers=_headers(teacher),
    )
    unchanged = api_client.get(f"/tasks/{task['id']}", headers=_headers(teacher))
    assert unchanged.json()["data"]["task"]["name"] == "Lab 3 缓存模拟器"

    saved = api_client.post(
        f"/task-drafts/{changes['id']}/publish", headers=_headers(teacher)
    )
    assert saved.status_code == 200, saved.text
    after = api_client.get(f"/tasks/{task['id']}", headers=_headers(teacher))
    assert after.json()["data"]["task"]["name"] == "Lab 3 缓存模拟器（修订）"

    # Only the publisher and the space's admins may change it.
    refused = api_client.post(
        "/task-drafts", json={"task": task["id"]}, headers=_headers(board["stranger"])
    )
    assert refused.status_code in (403, 404)


def test_cheese_writes_a_new_draft_in_its_own_words(
    api_client: TestClient, board: dict
):
    teacher = board["teacher"]
    resp = api_client.post(
        "/task-drafts/agent/write",
        json={
            "space": board["space"]["id"],
            "source": "Lab3.pdf",
            "name": "Lab 3 缓存模拟器",
            "description": "## 要求\n\n用 C 写一个缓存模拟器。",
            "difficulty": "中级",
            "form": "个人",
            "deadline": "2026-11-08 23:59",
            "submission": [{"name": "csim.c", "kind": "文件"}],
            "rubric": [{"item": "正确性", "points": 60, "criteria": "全对"}],
        },
        headers=_as_cheese(teacher),
    )
    assert resp.status_code == 200, resp.text
    written = resp.json()["data"]
    assert written["created"] is True
    assert written["missing"] == []

    draft = api_client.get(
        f"/task-drafts/{written['draft']}", headers=_headers(teacher)
    ).json()["data"]
    assert draft["source"] == "Lab3.pdf"
    fields = draft["fields"]
    assert fields["rank"] == 2
    assert fields["submitterType"] == "USER"
    assert fields["submissionSchema"] == [
        {"prompt": "csim.c", "type": "FILE", "stage": 0}
    ]
    # On the asker's clock: they never said theirs, so Beijing time.
    assert fields["deadline"] == int(
        datetime(2026, 11, 8, 15, 59, tzinfo=UTC).timestamp() * 1000
    )
    assert fields["descriptionMarkdown"].startswith("## 要求")

    read = api_client.post(
        "/task-drafts/agent/read",
        json={"draft": written["draft"]},
        headers=_as_cheese(teacher),
    ).json()["data"]["text"]
    assert "difficulty: 中级" in read
    assert "deadline: 2026-11-08 23:59" in read


def _upload(api_client: TestClient, user, name: str = "课题说明.pdf") -> int:
    resp = api_client.post(
        "/attachments",
        headers=_headers(user),
        files={"file": (name, io.BytesIO(b"%PDF-1.4 lab topics"), "application/pdf")},
        data={"type": "file"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def test_cheese_attaches_files_the_person_handed_over_and_sets_mentors(
    api_client: TestClient, board: dict
):
    teacher = board["teacher"]
    mine = _upload(api_client, teacher)
    resp = api_client.post(
        "/task-drafts/agent/write",
        json={
            "space": board["space"]["id"],
            "name": "课题 A",
            "attachments": [mine],
            "mentors": [teacher.username],
            "team_locking": "报名通过后锁定",
        },
        headers=_as_cheese(teacher),
    )
    assert resp.status_code == 200, resp.text
    draft = resp.json()["data"]["draft"]
    fields = api_client.get(f"/task-drafts/{draft}", headers=_headers(teacher)).json()[
        "data"
    ]["fields"]
    assert fields["attachmentIds"] == [mine]
    assert fields["attachmentFiles"][0]["name"] == "课题说明.pdf"
    assert fields["mentors"] == [teacher.username]
    assert fields["teamLockingPolicy"] == "LOCK_ON_APPROVAL"

    theirs = _upload(api_client, board["stranger"])
    refused = api_client.post(
        "/task-drafts/agent/write",
        json={"draft": draft, "attachments": [theirs]},
        headers=_as_cheese(teacher),
    )
    assert refused.status_code == 403, refused.text


def test_a_field_the_person_changed_after_cheese_read_stays_theirs(
    api_client: TestClient, board: dict
):
    teacher = board["teacher"]
    draft = _start(api_client, board, fields=FORM)
    read_at = draft["version"]
    api_client.patch(
        f"/task-drafts/{draft['id']}",
        json={"fields": {"name": "老师自己改的名字"}},
        headers=_headers(teacher),
    )
    resp = api_client.post(
        "/task-drafts/agent/write",
        json={
            "draft": draft["id"],
            "version": read_at,
            "name": "芝士起的名字",
            "difficulty": "高级",
        },
        headers=_as_cheese(teacher),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["kept"] == ["name"]
    fields = api_client.get(
        f"/task-drafts/{draft['id']}", headers=_headers(teacher)
    ).json()["data"]["fields"]
    assert fields["name"] == "老师自己改的名字"
    assert fields["rank"] == 3


def test_cheese_is_told_what_to_write_instead(api_client: TestClient, board: dict):
    teacher = board["teacher"]
    draft = _start(api_client, board, fields=FORM)
    for bad in (
        {"category": "不存在的分类"},
        {"deadline": "下周五"},
        {"difficulty": "很难"},
        {"description_edits": [{"find": "没有这句", "replace": "x"}]},
    ):
        resp = api_client.post(
            "/task-drafts/agent/write",
            json={"draft": draft["id"], **bad},
            headers=_as_cheese(teacher),
        )
        assert resp.status_code == 400, (bad, resp.text)
        assert resp.json()["error"]["message"], bad

    edited = api_client.post(
        "/task-drafts/agent/write",
        json={
            "draft": draft["id"],
            "description_edits": [{"find": "缓存模拟器", "replace": "LRU 缓存模拟器"}],
        },
        headers=_as_cheese(teacher),
    )
    assert edited.status_code == 200, edited.text
    fields = api_client.get(
        f"/task-drafts/{draft['id']}", headers=_headers(teacher)
    ).json()["data"]["fields"]
    assert fields["descriptionMarkdown"] == "用 C 写一个LRU 缓存模拟器。"


def test_cheese_publishes_as_the_person_and_only_their_own_drafts(
    api_client: TestClient, board: dict
):
    teacher = board["teacher"]
    draft = _start(api_client, board, fields={**FORM, "deadline": _deadline_ms()})

    theirs = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": draft["id"]},
        headers=_as_cheese(board["stranger"]),
    )
    assert theirs.status_code == 404, theirs.text

    published = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": draft["id"]},
        headers=_as_cheese(teacher),
    )
    assert published.status_code == 200, published.text
    assert published.json()["data"]["task"]["name"] == "Lab 3 缓存模拟器"


def test_an_incomplete_draft_is_not_published(api_client: TestClient, board: dict):
    draft = _start(api_client, board, fields={"name": "只有名字"})
    resp = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": draft["id"]},
        headers=_as_cheese(board["teacher"]),
    )
    assert resp.status_code == 400
    assert "description" in resp.json()["error"]["message"], resp.text


def test_the_page_routes_do_not_take_cheeses_credential(
    api_client: TestClient, board: dict
):
    draft = _start(api_client, board, fields=FORM)
    resp = api_client.patch(
        f"/task-drafts/{draft['id']}",
        json={"fields": {"name": "x"}},
        headers=_as_cheese(board["teacher"]),
    )
    assert resp.status_code == 403
