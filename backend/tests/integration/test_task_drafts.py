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
        {**line, "stage": None} for line in FORM["rubric"]
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


def test_opening_a_published_tasks_changes_starts_no_draft(
    api_client: TestClient, board: dict
):
    """Opening the edit page is not changing the 题目: the person's draft list
    stays as it was until they change something."""
    teacher = board["teacher"]
    draft = _start(api_client, board, fields={**FORM, "deadline": _deadline_ms()})
    task = api_client.post(
        f"/task-drafts/{draft['id']}/publish", headers=_headers(teacher)
    ).json()["data"]["task"]
    listing = f"/task-drafts?space={board['space']['id']}"

    opened = api_client.get(
        f"/task-drafts/changes?task={task['id']}", headers=_headers(teacher)
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["data"]["draft"] is None
    assert opened.json()["data"]["form"]["name"] == "Lab 3 缓存模拟器"
    assert (
        api_client.get(listing, headers=_headers(teacher)).json()["data"]["drafts"]
        == []
    )

    # Once there are changes, opening the page opens them.
    changes = api_client.post(
        "/task-drafts", json={"task": task["id"]}, headers=_headers(teacher)
    ).json()["data"]
    api_client.patch(
        f"/task-drafts/{changes['id']}",
        json={"fields": {"name": "Lab 3 缓存模拟器（修订）"}},
        headers=_headers(teacher),
    )
    reopened = api_client.get(
        f"/task-drafts/changes?task={task['id']}", headers=_headers(teacher)
    ).json()["data"]
    assert reopened["draft"]["id"] == changes["id"]
    assert reopened["form"]["name"] == "Lab 3 缓存模拟器（修订）"

    # Only those who may change the 题目 read it.
    refused = api_client.get(
        f"/task-drafts/changes?task={task['id']}",
        headers=_headers(board["stranger"]),
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
        {"prompt": "csim.c", "type": "FILE", "stage": None}
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


def test_cheese_sets_how_the_task_starts_and_the_task_starts_that_way(
    api_client: TestClient, board: dict
):
    """芝士 fills 开始方式 in the form's words; the published 题目 starts that way,
    and a change 芝士 writes to it is saved onto it."""
    teacher = board["teacher"]
    written = api_client.post(
        "/task-drafts/agent/write",
        json={
            "space": board["space"]["id"],
            "name": "Lab 3 缓存模拟器",
            "description": "用 C 写一个缓存模拟器。",
            "difficulty": "中级",
            "form": "个人",
            "start_mode": "直接开始",
            "deadline": "2026-11-08 23:59",
        },
        headers=_as_cheese(teacher),
    )
    assert written.status_code == 200, written.text
    draft = written.json()["data"]["draft"]
    read = api_client.post(
        "/task-drafts/agent/read", json={"draft": draft}, headers=_as_cheese(teacher)
    ).json()["data"]["text"]
    assert "start_mode: 直接开始" in read

    published = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": draft},
        headers=_as_cheese(teacher),
    )
    assert published.status_code == 200, published.text
    task_id = published.json()["data"]["task"]["id"]
    page = api_client.get(f"/tasks/{task_id}", headers=_headers(teacher))
    assert page.json()["data"]["task"]["startMode"] == "DIRECT"

    refused = api_client.post(
        "/task-drafts/agent/write",
        json={"task": task_id, "start_mode": "先到先得"},
        headers=_as_cheese(teacher),
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskStartModeInvalid"

    changed = api_client.post(
        "/task-drafts/agent/write",
        json={"task": task_id, "start_mode": "申请制", "days_to_submit": 10},
        headers=_as_cheese(teacher),
    )
    assert changed.status_code == 200, changed.text
    saved = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": changed.json()["data"]["draft"]},
        headers=_as_cheese(teacher),
    )
    assert saved.status_code == 200, saved.text
    task = api_client.get(f"/tasks/{task_id}", headers=_headers(teacher)).json()[
        "data"
    ]["task"]
    assert (task["startMode"], task["defaultDeadline"]) == ("APPLY", 10)


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
            "team_locking": "申请通过后锁定",
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


def test_cheese_fills_every_ai_setting_and_the_published_task_has_them(
    api_client: TestClient, board: dict
):
    """芝士 writes each field of 「AI 的设置」 in its own words — the default
    teammate and every part of the 题目's guidance — and the 题目 it publishes
    carries each of them."""
    from app.domain.agent_type.library import preset_types

    teacher = board["teacher"]
    space_id = board["space"]["id"]
    uploaded = api_client.post(
        f"/spaces/{space_id}/materials",
        files={
            "file": ("week6.pdf", io.BytesIO(b"%PDF-1.4 week 6"), "application/pdf")
        },
        data={"type": "file"},
        headers=_headers(teacher),
    )
    assert uploaded.status_code == 201, uploaded.text
    handout = next(
        m
        for m in api_client.get(
            f"/spaces/{space_id}/materials", headers=_headers(teacher)
        ).json()["data"]["materials"]
        if m["name"] == "week6.pdf"
    )
    role, preset = next(iter(preset_types().items()))

    written = api_client.post(
        "/task-drafts/agent/write",
        json={
            "space": space_id,
            "name": "Lab 3 缓存模拟器",
            "description": "用 C 写一个缓存模拟器。",
            "difficulty": "中级",
            "form": "个人",
            "submission": [{"name": "csim.c", "kind": "文件"}],
            "ai_teammate": preset.title,
            "ai_mode": "引导为主",
            "ai_guidance": "不要直接给出代码。",
            "ai_private_guidance": "参考答案在讲义第 12 页。",
            "ai_goals": ["能自己写出 LRU", "能讲清组相联"],
            "ai_materials": ["week6.pdf"],
            "ai_skills": ["wolfram"],
        },
        headers=_as_cheese(teacher),
    )
    assert written.status_code == 200, written.text
    draft = written.json()["data"]["draft"]

    published = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": draft},
        headers=_as_cheese(teacher),
    )
    assert published.status_code == 200, published.text
    task_id = published.json()["data"]["task"]["id"]

    task = api_client.get(f"/tasks/{task_id}", headers=_headers(teacher)).json()[
        "data"
    ]["task"]
    assert task["defaultRole"] == role
    teaching = task["teaching"]
    assert teaching["mode"] == "guide"
    assert teaching["systemPrompt"] == "不要直接给出代码。"
    assert teaching["privatePrompt"] == "参考答案在讲义第 12 页。"
    assert teaching["learningGoals"] == ["能自己写出 LRU", "能讲清组相联"]
    assert teaching["materialIds"] == [handout["id"]]
    assert teaching["skills"] == ["wolfram"]


def test_a_published_tasks_private_guidance_is_read_only_by_who_may_teach_it(
    api_client: TestClient, board: dict
):
    """Anyone who can read a 题目 can have 芝士 read it; its private guidance
    goes only to its publisher and the space's admins."""
    teacher = board["teacher"]
    written = api_client.post(
        "/task-drafts/agent/write",
        json={
            "space": board["space"]["id"],
            "name": "Lab 3 缓存模拟器",
            "description": "用 C 写一个缓存模拟器。",
            "difficulty": "中级",
            "form": "个人",
            "submission": [{"name": "csim.c", "kind": "文件"}],
            "ai_private_guidance": "SECRET_ANSWER_4777",
        },
        headers=_as_cheese(teacher),
    )
    assert written.status_code == 200, written.text
    published = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": written.json()["data"]["draft"]},
        headers=_as_cheese(teacher),
    )
    assert published.status_code == 200, published.text
    task_id = published.json()["data"]["task"]["id"]
    approved = api_client.patch(
        f"/tasks/{task_id}", json={"approved": "APPROVED"}, headers=_headers(teacher)
    )
    assert approved.status_code == 200, approved.text

    def read_as(user) -> str:
        resp = api_client.post(
            "/task-drafts/agent/read", json={"task": task_id}, headers=_as_cheese(user)
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["data"]["text"]

    assert "SECRET_ANSWER_4777" in read_as(teacher)
    # A plain member of the space: reads the 题目, but is not one who teaches it.
    joined = api_client.post(
        f"/spaces/{board['space']['id']}/members",
        json={"userId": board["stranger"].user_id},
        headers=_headers(teacher),
    )
    assert joined.status_code == 201, joined.text
    seen = read_as(board["stranger"])
    assert "Lab 3 缓存模拟器" in seen
    assert "SECRET_ANSWER_4777" not in seen


def test_an_admin_who_lost_the_board_no_longer_reads_a_drafts_private_guidance(
    api_client: TestClient, board: dict
):
    """Changes to a published 题目 carry its private guidance only while their
    writer may still teach it: a board admin who opened them and was then
    removed reads them without it, on the page and through 芝士."""
    teacher, admin = board["teacher"], board["stranger"]
    space_id = board["space"]["id"]
    draft = _start(
        api_client,
        board,
        fields={
            **FORM,
            "teaching": {"privatePrompt": "SECRET_ANSWER_4777"},
            "teachingOwn": True,
        },
    )
    published = api_client.post(
        f"/task-drafts/{draft['id']}/publish", headers=_headers(teacher)
    )
    assert published.status_code == 200, published.text
    task_id = published.json()["data"]["task"]["id"]
    made = api_client.post(
        f"/spaces/{space_id}/managers",
        json={"userId": admin.user_id, "role": "ADMIN"},
        headers=_headers(teacher),
    )
    assert made.status_code == 201, made.text
    changes = api_client.post(
        "/task-drafts", json={"task": task_id}, headers=_headers(admin)
    )
    assert changes.status_code == 201, changes.text
    changes_id = changes.json()["data"]["id"]
    assert "SECRET_ANSWER_4777" in changes.text

    removed = api_client.delete(
        f"/spaces/{space_id}/managers/{admin.user_id}", headers=_headers(teacher)
    )
    assert removed.status_code in (200, 204), removed.text

    page = api_client.get(f"/task-drafts/{changes_id}", headers=_headers(admin))
    assert page.status_code == 200, page.text
    assert "SECRET_ANSWER_4777" not in page.text
    read = api_client.post(
        "/task-drafts/agent/read", json={"draft": changes_id}, headers=_as_cheese(admin)
    )
    assert read.status_code == 200, read.text
    assert "SECRET_ANSWER_4777" not in read.text


def test_inheriting_the_default_teammate_brings_back_the_categorys(
    api_client: TestClient, board: dict, db_session, _portal
):
    """A 题目 that names its own default teammate, set back to 沿用上层设置 by
    芝士, gets its 项目集's default again — not "no teammate"."""
    from app.domain.agent_type.library import preset_types
    from app.domain.space.models import SpaceCategory
    from app.domain.task.models import Task

    own, category_role = list(preset_types())[:2]
    teacher = board["teacher"]
    draft = _start(api_client, board, fields={**FORM, "defaultRole": own})
    published = api_client.post(
        f"/task-drafts/{draft['id']}/publish", headers=_headers(teacher)
    )
    assert published.status_code == 200, published.text
    task_id = published.json()["data"]["task"]["id"]

    async def put_in_a_category() -> None:
        now = datetime.now(UTC)
        category = SpaceCategory(
            space_id=board["space"]["id"],
            name="课程",
            description="",
            display_order=0,
            default_role=category_role,
            created_at=now,
            updated_at=now,
        )
        db_session.add(category)
        await db_session.flush()
        (await db_session.get(Task, task_id)).category_id = category.id
        await db_session.commit()

    _portal.call(put_in_a_category)
    written = api_client.post(
        "/task-drafts/agent/write",
        json={"task": task_id, "ai_teammate": "沿用上层设置"},
        headers=_as_cheese(teacher),
    )
    assert written.status_code == 200, written.text
    saved = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": written.json()["data"]["draft"]},
        headers=_as_cheese(teacher),
    )
    assert saved.status_code == 200, saved.text

    found = api_client.get(f"/tasks/{task_id}/inheritance", headers=_headers(teacher))
    assert found.status_code == 200, found.text
    assert (found.json()["data"]["agent"] or {}).get("typeName") == category_role


def test_cheese_is_refused_an_ai_setting_the_space_does_not_offer(
    api_client: TestClient, board: dict
):
    """A skill, a file or a teammate the 题目 cannot have is refused, not
    written."""
    teacher = board["teacher"]
    for field, value in (
        ("ai_skills", ["no-such-skill"]),
        ("ai_materials", ["missing.pdf"]),
        ("ai_teammate", "no-such-teammate"),
    ):
        refused = api_client.post(
            "/task-drafts/agent/write",
            json={"space": board["space"]["id"], "name": "题", field: value},
            headers=_as_cheese(teacher),
        )
        assert refused.status_code == 400, (field, refused.text)


def test_cheese_cannot_change_how_a_task_starts_once_someone_holds_a_claim(
    api_client: TestClient, board: dict
):
    teacher, student = board["teacher"], board["stranger"]
    written = api_client.post(
        "/task-drafts/agent/write",
        json={
            "space": board["space"]["id"],
            "name": "Lab 4 有人开始做了",
            "description": "用 C 写一个分配器。",
            "difficulty": "中级",
            "form": "个人",
        },
        headers=_as_cheese(teacher),
    )
    assert written.status_code == 200, written.text
    published = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": written.json()["data"]["draft"]},
        headers=_as_cheese(teacher),
    )
    assert published.status_code == 200, published.text
    task_id = published.json()["data"]["task"]["id"]
    enrolled = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": student.user_id},
        json={},
        headers=_headers(teacher),
    )
    assert enrolled.status_code == 200, enrolled.text

    changed = api_client.post(
        "/task-drafts/agent/write",
        json={"task": task_id, "start_mode": "申请制", "days_to_submit": 10},
        headers=_as_cheese(teacher),
    )
    assert changed.status_code == 200, changed.text
    refused = api_client.post(
        "/task-drafts/agent/publish",
        json={"draft": changed.json()["data"]["draft"]},
        headers=_as_cheese(teacher),
    )

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["i18n"]["key"] == "taskStartModeLocked"
    task = api_client.get(f"/tasks/{task_id}", headers=_headers(teacher)).json()[
        "data"
    ]["task"]
    assert task["startMode"] == "DIRECT"


# What the publish page's form holds whichever form is chosen: it shows the team
# fields only for 团队, but keeps their values (its defaults are 1 and 10) so
# switching back to 团队 brings them back, and it saves them into the draft.
FORM_TEAM_FIELDS = {"minTeamSize": 1, "maxTeamSize": 10, "teamLockingPolicy": "NO_LOCK"}


def _task(api_client, teacher, task_id: int) -> dict:
    return api_client.get(f"/tasks/{task_id}", headers=_headers(teacher)).json()[
        "data"
    ]["task"]


def test_a_personal_task_publishes_from_a_draft_holding_the_forms_team_sizes(
    api_client: TestClient, board: dict
):
    """A 个人 题目 has no team: the team sizes the form keeps in the draft do not
    stop it from publishing, and the 题目 it publishes has none."""
    teacher = board["teacher"]
    draft = _start(
        api_client,
        board,
        fields={**FORM, **FORM_TEAM_FIELDS, "deadline": _deadline_ms()},
    )

    published = api_client.post(
        f"/task-drafts/{draft['id']}/publish", headers=_headers(teacher)
    )

    assert published.status_code == 200, published.text
    task = _task(api_client, teacher, published.json()["data"]["task"]["id"])
    assert task["submitterType"] == "USER"
    assert task["minTeamSize"] is None
    assert task["maxTeamSize"] is None


def test_a_team_task_publishes_with_the_team_sizes_its_draft_holds(
    api_client: TestClient, board: dict
):
    teacher = board["teacher"]
    draft = _start(
        api_client,
        board,
        fields={
            **FORM,
            "submitterType": "TEAM",
            "minTeamSize": 2,
            "maxTeamSize": 4,
            "teamLockingPolicy": "LOCK_ON_APPROVAL",
            "deadline": _deadline_ms(),
        },
    )

    published = api_client.post(
        f"/task-drafts/{draft['id']}/publish", headers=_headers(teacher)
    )

    assert published.status_code == 200, published.text
    task = _task(api_client, teacher, published.json()["data"]["task"]["id"])
    assert task["submitterType"] == "TEAM"
    assert (task["minTeamSize"], task["maxTeamSize"]) == (2, 4)
    assert task["teamLockingPolicy"] == "LOCK_ON_APPROVAL"


def test_switching_a_draft_to_personal_and_back_to_team_keeps_its_team_sizes(
    api_client: TestClient, board: dict
):
    teacher = board["teacher"]
    draft = _start(
        api_client,
        board,
        fields={
            **FORM,
            "submitterType": "TEAM",
            "minTeamSize": 2,
            "maxTeamSize": 4,
            "deadline": _deadline_ms(),
        },
    )
    for form in ("USER", "TEAM"):
        switched = api_client.patch(
            f"/task-drafts/{draft['id']}",
            json={"fields": {"submitterType": form}},
            headers=_headers(teacher),
        )
        assert switched.status_code == 200, switched.text

    published = api_client.post(
        f"/task-drafts/{draft['id']}/publish", headers=_headers(teacher)
    )

    assert published.status_code == 200, published.text
    task = _task(api_client, teacher, published.json()["data"]["task"]["id"])
    assert (task["minTeamSize"], task["maxTeamSize"]) == (2, 4)


def test_changes_to_a_personal_task_save_from_a_draft_holding_the_forms_team_sizes(
    api_client: TestClient, board: dict
):
    """The edit page keeps the same form: saving changes to a published 个人 题目
    is not refused over the team sizes the form holds."""
    teacher = board["teacher"]
    draft = _start(api_client, board, fields={**FORM, "deadline": _deadline_ms()})
    task_id = api_client.post(
        f"/task-drafts/{draft['id']}/publish", headers=_headers(teacher)
    ).json()["data"]["task"]["id"]
    changes = api_client.post(
        "/task-drafts", json={"task": task_id}, headers=_headers(teacher)
    ).json()["data"]
    api_client.patch(
        f"/task-drafts/{changes['id']}",
        json={"fields": {**FORM_TEAM_FIELDS, "name": "Lab 3 缓存模拟器（修订）"}},
        headers=_headers(teacher),
    )

    saved = api_client.post(
        f"/task-drafts/{changes['id']}/publish", headers=_headers(teacher)
    )

    assert saved.status_code == 200, saved.text
    task = _task(api_client, teacher, task_id)
    assert task["name"] == "Lab 3 缓存模拟器（修订）"
    assert task["minTeamSize"] is None
