import os
import random
from datetime import datetime, timezone, timedelta

import httpx
import pytest

from tests.integration.conftest import UserCreator

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION_TESTS", "").lower() not in ("1", "true"),
        reason="Integration tests require RUN_INTEGRATION_TESTS=1 and a running database",
    ),
]


class TestTaskIntegration:
    @pytest.fixture
    def task_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        participant2 = user_client.create_user()
        participant2.token = user_client.login(
            api_client, participant2.username, participant2.password
        )

        participant3 = user_client.create_user()
        participant3.token = user_client.login(
            api_client, participant3.username, participant3.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Test Space ({suffix})",
                "intro": "A space for testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert space_resp.status_code == 201, f"Failed to create space: {space_resp.text}"
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        deadline_ms = int((datetime.now(timezone.utc).timestamp() + 7 * 24 * 3600) * 1000)

        return {
            "creator": creator,
            "participant": participant,
            "participant2": participant2,
            "participant3": participant3,
            "space_id": space_id,
            "category_id": category_id,
            "task_name": f"Test Task ({suffix})",
            "task_intro": "This is a test task",
            "task_description": '{"type":"doc","content":[]}',
            "deadline_ms": deadline_ms,
        }

    def test_create_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        response = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": task_setup["deadline_ms"],
            },
            headers=headers,
        )
        assert response.status_code == 200, f"Failed: {response.text}"
        data = response.json()
        task = data["data"]["task"]

        assert task["id"] > 0
        assert task["name"] == task_setup["task_name"]
        assert task["intro"] == task_setup["task_intro"]
        assert task["description"] == task_setup["task_description"]
        assert task["submitterType"] == "USER"
        assert task["resubmittable"] is True
        assert task["editable"] is True
        assert task["defaultDeadline"] == 30
        assert task["deadline"] == task_setup["deadline_ms"]
        assert task["approved"] == "NONE"
        assert task["space"]["id"] == task_setup["space_id"]
        assert task["category"]["id"] == task_setup["category_id"]
        assert task["category"]["name"] is not None
        assert task["creator"]["id"] is not None

    def test_get_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        assert create_resp.status_code == 200
        task_id = create_resp.json()["data"]["task"]["id"]

        get_resp = api_client.get(f"/tasks/{task_id}", headers=headers)
        assert get_resp.status_code == 200
        data = get_resp.json()
        task = data["data"]["task"]

        assert task["id"] == task_id
        assert task["name"] == task_setup["task_name"]
        assert task["intro"] == task_setup["task_intro"]
        assert task["description"] == task_setup["task_description"]
        assert task["submitterType"] == "USER"
        assert task["resubmittable"] is True
        assert task["editable"] is True
        assert task["defaultDeadline"] == 30
        assert task["approved"] == "NONE"
        assert task["space"]["id"] == task_setup["space_id"]
        assert task["category"]["id"] == task_setup["category_id"]
        assert task["creator"]["id"] is not None

        get_with_joinability = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true"},
            headers=headers,
        )
        assert get_with_joinability.status_code == 200
        joinability_data = get_with_joinability.json()
        task_with_joinability = joinability_data["data"]["task"]
        assert "participationEligibility" in task_with_joinability
        eligibility = task_with_joinability["participationEligibility"]
        assert eligibility is not None
        assert "user" in eligibility

    def test_update_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        created_task = create_resp.json()["data"]["task"]
        task_id = created_task["id"]
        original_intro = created_task["intro"]
        original_description = created_task["description"]

        updated_name = f"{task_setup['task_name']} (Updated)"
        patch_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"name": updated_name},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        updated_task = patch_resp.json()["data"]["task"]
        assert updated_task["name"] == updated_name
        assert updated_task["id"] == task_id
        assert updated_task["intro"] == original_intro
        assert updated_task["description"] == original_description

        get_resp = api_client.get(f"/tasks/{task_id}", headers=headers)
        assert get_resp.status_code == 200
        get_task = get_resp.json()["data"]["task"]
        assert get_task["name"] == updated_name

    def test_enumerate_tasks(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        list_resp = api_client.get(
            "/tasks",
            params={"spaceId": task_setup["space_id"]},
            headers=headers,
        )
        assert list_resp.status_code == 200
        data = list_resp.json()
        tasks = data["data"]["tasks"]
        task_ids = [t["id"] for t in tasks]

        assert task_id in task_ids

        matching_task = next((t for t in tasks if t["id"] == task_id), None)
        assert matching_task is not None
        assert matching_task["name"] == task_setup["task_name"]
        assert matching_task["intro"] == task_setup["task_intro"]
        assert matching_task["submitterType"] == "USER"
        assert matching_task["approved"] == "NONE"
        assert "creator" in matching_task
        assert matching_task["creator"]["id"] is not None

    def test_approve_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        create_data = create_resp.json()
        task_id = create_data["data"]["task"]["id"]
        assert create_data["data"]["task"]["approved"] == "NONE"

        approve_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers=headers,
        )
        assert approve_resp.status_code == 200
        approve_data = approve_resp.json()
        task = approve_data["data"]["task"]

        assert task["approved"] == "APPROVED"
        assert task["id"] == task_id
        assert task["name"] == task_setup["task_name"]

        get_resp = api_client.get(f"/tasks/{task_id}", headers=headers)
        assert get_resp.status_code == 200
        get_task = get_resp.json()["data"]["task"]
        assert get_task["approved"] == "APPROVED"

    def test_check_participation_eligibility_user_task(
        self, api_client: httpx.Client, task_setup: dict
    ) -> None:
        creator = task_setup["creator"]
        participant = task_setup["participant"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        eligibility_resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert eligibility_resp.status_code == 200
        data = eligibility_resp.json()
        task = data["data"]["task"]

        assert "participationEligibility" in task
        eligibility = task["participationEligibility"]
        assert eligibility is not None

        user_eligibility = eligibility.get("user")
        assert user_eligibility is not None
        assert "eligible" in user_eligibility
        assert isinstance(user_eligibility["eligible"], bool)

        if not user_eligibility["eligible"]:
            assert "reasons" in user_eligibility
            assert isinstance(user_eligibility["reasons"], list)

    def test_check_participation_eligibility_team_task(
        self, api_client: httpx.Client, task_setup: dict
    ) -> None:
        creator = task_setup["creator"]
        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Test Team {task_setup['task_name']}",
                "intro": "Test team intro",
                "description": "Test team description",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert team_resp.status_code in [200, 201], f"Team creation failed: {team_resp.text}"

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"{task_setup['task_name']} (TEAM)",
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "TEAM",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        eligibility_resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert eligibility_resp.status_code == 200
        data = eligibility_resp.json()
        task = data["data"]["task"]

        assert "participationEligibility" in task
        eligibility = task["participationEligibility"]
        assert eligibility is not None

        teams_eligibility = eligibility.get("teams")
        assert teams_eligibility is not None
        assert isinstance(teams_eligibility, list)

        if len(teams_eligibility) > 0:
            for team_status in teams_eligibility:
                assert "team" in team_status
                assert "id" in team_status["team"]
                assert "eligibility" in team_status
                assert "eligible" in team_status["eligibility"]
                assert isinstance(team_status["eligibility"]["eligible"], bool)

    def test_join_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        participant = task_setup["participant"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        pre_join_resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true", "queryParticipation": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert pre_join_resp.status_code == 200
        pre_join_task = pre_join_resp.json()["data"]["task"]
        if "participation" in pre_join_task:
            assert pre_join_task["participation"].get("hasParticipation") is False

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert join_resp.status_code == 200, f"Join failed: {join_resp.text}"

        post_join_resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryParticipation": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert post_join_resp.status_code == 200
        post_join_task = post_join_resp.json()["data"]["task"]
        if "participation" in post_join_task:
            assert post_join_task["participation"].get("hasParticipation") is True

        participants_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert participants_resp.status_code == 200
        member_ids = [p.get("memberId") for p in participants_resp.json()["data"]["participants"]]
        assert participant.user_id in member_ids

    def test_delete_task(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        delete_resp = api_client.delete(f"/tasks/{task_id}", headers=headers)
        assert delete_resp.status_code == 204

        get_resp = api_client.get(f"/tasks/{task_id}", headers=headers)
        assert get_resp.status_code == 404

    def test_get_participants(self, api_client: httpx.Client, task_setup: dict) -> None:
        creator = task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": task_setup["task_name"],
                "intro": task_setup["task_intro"],
                "description": task_setup["task_description"],
                "space": task_setup["space_id"],
                "categoryId": task_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers=headers,
        )

        participant = task_setup["participant"]
        participant_headers = {"Authorization": f"Bearer {participant.token}"}
        join_resp = api_client.post(
            f"/tasks/{task_id}/participations/user",
            json={},
            headers=participant_headers,
        )
        assert join_resp.status_code == 200

        participants_resp = api_client.get(f"/tasks/{task_id}/participants", headers=headers)
        assert participants_resp.status_code == 200
        data = participants_resp.json()
        assert "participants" in data["data"]

        participants = data["data"]["participants"]
        assert isinstance(participants, list)
        assert len(participants) > 0

        participant_found = False
        for p in participants:
            assert "id" in p
            assert "participant" in p
            assert p["participant"]["id"] is not None
            if "username" in p["participant"]:
                assert isinstance(p["participant"]["username"], str)
            if p["participant"].get("username") == participant.username:
                participant_found = True

        assert participant_found, "Joined participant should appear in participants list"


class TestTaskEnumeration:
    @pytest.fixture
    def multi_task_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Test Space ({suffix})",
                "intro": "A space for testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        task_ids = []
        for i in range(5):
            deadline_ms = int(
                (datetime.now(timezone.utc) + timedelta(days=i + 1)).timestamp() * 1000
            )
            create_resp = api_client.post(
                "/tasks",
                json={
                    "name": f"Task {i} ({suffix})",
                    "intro": f"Intro for task {i}",
                    "description": '{"type":"doc","content":[]}',
                    "space": space_id,
                    "categoryId": category_id,
                    "submitterType": "USER",
                    "resubmittable": True,
                    "editable": True,
                    "defaultDeadline": 30,
                    "deadline": deadline_ms,
                },
                headers={"Authorization": f"Bearer {creator.token}"},
            )
            task_ids.append(create_resp.json()["data"]["task"]["id"])

        return {
            "creator": creator,
            "space_id": space_id,
            "category_id": category_id,
            "task_ids": task_ids,
            "suffix": suffix,
        }

    def test_enumerate_tasks_by_owner(
        self, api_client: httpx.Client, multi_task_setup: dict
    ) -> None:
        creator = multi_task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        list_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": multi_task_setup["space_id"],
                "owner": creator.user_id,
            },
            headers=headers,
        )
        assert list_resp.status_code == 200
        tasks = list_resp.json()["data"]["tasks"]
        assert len(tasks) == 5

    def test_enumerate_tasks_by_approved_status(
        self, api_client: httpx.Client, multi_task_setup: dict
    ) -> None:
        creator = multi_task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        api_client.patch(
            f"/tasks/{multi_task_setup['task_ids'][0]}",
            json={"approved": "APPROVED"},
            headers=headers,
        )
        api_client.patch(
            f"/tasks/{multi_task_setup['task_ids'][1]}",
            json={"approved": "APPROVED"},
            headers=headers,
        )

        approved_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": multi_task_setup["space_id"],
                "approved": "APPROVED",
            },
            headers=headers,
        )
        assert approved_resp.status_code == 200
        approved_tasks = approved_resp.json()["data"]["tasks"]
        assert len(approved_tasks) == 2

        none_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": multi_task_setup["space_id"],
                "approved": "NONE",
            },
            headers=headers,
        )
        assert none_resp.status_code == 200
        none_tasks = none_resp.json()["data"]["tasks"]
        assert len(none_tasks) == 3

    def test_enumerate_tasks_pagination(
        self, api_client: httpx.Client, multi_task_setup: dict
    ) -> None:
        creator = multi_task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        page1_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": multi_task_setup["space_id"],
                "pageSize": 2,
            },
            headers=headers,
        )
        assert page1_resp.status_code == 200
        page1_data = page1_resp.json()["data"]
        assert len(page1_data["tasks"]) == 2
        assert page1_data["page"]["hasMore"] is True
        next_start = page1_data["page"]["nextStart"]

        page2_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": multi_task_setup["space_id"],
                "pageSize": 2,
                "pageStart": next_start,
            },
            headers=headers,
        )
        assert page2_resp.status_code == 200
        page2_data = page2_resp.json()["data"]
        assert len(page2_data["tasks"]) == 2

    def test_enumerate_tasks_sort_by_created_at_asc(
        self, api_client: httpx.Client, multi_task_setup: dict
    ) -> None:
        creator = multi_task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        list_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": multi_task_setup["space_id"],
                "sort_by": "createdAt",
                "sort_order": "asc",
            },
            headers=headers,
        )
        assert list_resp.status_code == 200
        tasks = list_resp.json()["data"]["tasks"]
        assert len(tasks) == 5
        created_times = [t["createdAt"] for t in tasks]
        assert created_times == sorted(created_times)

    def test_enumerate_tasks_sort_by_deadline_desc(
        self, api_client: httpx.Client, multi_task_setup: dict
    ) -> None:
        creator = multi_task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        list_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": multi_task_setup["space_id"],
                "sort_by": "deadline",
                "sort_order": "desc",
            },
            headers=headers,
        )
        assert list_resp.status_code == 200
        tasks = list_resp.json()["data"]["tasks"]
        assert len(tasks) == 5

    def test_enumerate_tasks_by_keywords(
        self, api_client: httpx.Client, multi_task_setup: dict
    ) -> None:
        creator = multi_task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        list_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": multi_task_setup["space_id"],
                "keywords": f"Task 0 ({multi_task_setup['suffix']})",
            },
            headers=headers,
        )
        assert list_resp.status_code == 200
        tasks = list_resp.json()["data"]["tasks"]
        assert len(tasks) >= 1


class TestTaskApprovalWorkflow:
    @pytest.fixture
    def approval_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Test Space ({suffix})",
                "intro": "A space for testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        return {
            "creator": creator,
            "participant": participant,
            "space_id": space_id,
            "category_id": category_id,
            "suffix": suffix,
        }

    def test_disapprove_task(self, api_client: httpx.Client, approval_setup: dict) -> None:
        creator = approval_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Disapprove Test ({approval_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": approval_setup["space_id"],
                "categoryId": approval_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        disapprove_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "DISAPPROVED", "rejectReason": "Test rejection"},
            headers=headers,
        )
        assert disapprove_resp.status_code == 200
        disapproved_task = disapprove_resp.json()["data"]["task"]
        assert disapproved_task["approved"] == "DISAPPROVED"
        assert disapproved_task["id"] == task_id

        get_resp = api_client.get(f"/tasks/{task_id}", headers=headers)
        assert get_resp.status_code == 200
        get_task = get_resp.json()["data"]["task"]
        assert get_task["approved"] == "DISAPPROVED"

    def test_resubmit_task(self, api_client: httpx.Client, approval_setup: dict) -> None:
        creator = approval_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Resubmit Test ({approval_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": approval_setup["space_id"],
                "categoryId": approval_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "DISAPPROVED", "rejectReason": "Test rejection"},
            headers=headers,
        )

        resubmit_resp = api_client.post(
            f"/tasks/{task_id}/resubmit",
            headers=headers,
        )
        assert resubmit_resp.status_code == 200
        assert resubmit_resp.json()["data"]["task"]["approved"] == "NONE"

    def test_update_task_with_empty_request(
        self, api_client: httpx.Client, approval_setup: dict
    ) -> None:
        creator = approval_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Empty Update Test ({approval_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": approval_setup["space_id"],
                "categoryId": approval_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]
        original_name = create_resp.json()["data"]["task"]["name"]

        patch_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["data"]["task"]["name"] == original_name

    def test_update_task_removing_deadline(
        self, api_client: httpx.Client, approval_setup: dict
    ) -> None:
        creator = approval_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        deadline_ms = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp() * 1000)

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Remove Deadline Test ({approval_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": approval_setup["space_id"],
                "categoryId": approval_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": deadline_ms,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        patch_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"hasDeadline": False},
            headers=headers,
        )
        assert patch_resp.status_code == 200


class TestParticipantManagement:
    @pytest.fixture
    def participant_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant1 = user_client.create_user()
        participant1.token = user_client.login(
            api_client, participant1.username, participant1.password
        )

        participant2 = user_client.create_user()
        participant2.token = user_client.login(
            api_client, participant2.username, participant2.password
        )

        participant3 = user_client.create_user()
        participant3.token = user_client.login(
            api_client, participant3.username, participant3.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Test Space ({suffix})",
                "intro": "A space for testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Participant Test Task ({suffix})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        return {
            "creator": creator,
            "participant1": participant1,
            "participant2": participant2,
            "participant3": participant3,
            "space_id": space_id,
            "task_id": task_id,
        }

    def test_add_multiple_participants(
        self, api_client: httpx.Client, participant_setup: dict
    ) -> None:
        creator = participant_setup["creator"]
        task_id = participant_setup["task_id"]

        for p in [
            participant_setup["participant1"],
            participant_setup["participant2"],
            participant_setup["participant3"],
        ]:
            join_resp = api_client.post(
                f"/tasks/{task_id}/participants",
                json={},
                headers={"Authorization": f"Bearer {p.token}"},
            )
            assert join_resp.status_code == 200, f"Join failed for user: {join_resp.text}"

        participants_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert participants_resp.status_code == 200
        participants = participants_resp.json()["data"]["participants"]
        assert len(participants) == 3

        participant_ids = {p["participant"]["id"] for p in participants}
        assert participant_setup["participant1"].user_id in participant_ids
        assert participant_setup["participant2"].user_id in participant_ids
        assert participant_setup["participant3"].user_id in participant_ids

    def test_approve_participant(self, api_client: httpx.Client, participant_setup: dict) -> None:
        creator = participant_setup["creator"]
        participant1 = participant_setup["participant1"]
        task_id = participant_setup["task_id"]

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant1.token}"},
        )
        participant_id = join_resp.json()["data"]["participant"]["id"]

        approve_resp = api_client.patch(
            f"/tasks/{task_id}/participants/{participant_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert approve_resp.status_code == 200

    def test_disapprove_participant(
        self, api_client: httpx.Client, participant_setup: dict
    ) -> None:
        creator = participant_setup["creator"]
        participant1 = participant_setup["participant1"]
        task_id = participant_setup["task_id"]

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant1.token}"},
        )
        participant_id = join_resp.json()["data"]["participant"]["id"]

        disapprove_resp = api_client.patch(
            f"/tasks/{task_id}/participants/{participant_id}",
            json={"approved": "DISAPPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert disapprove_resp.status_code == 200

    def test_list_participants_by_approved_status(
        self, api_client: httpx.Client, participant_setup: dict
    ) -> None:
        creator = participant_setup["creator"]
        task_id = participant_setup["task_id"]

        for p in [
            participant_setup["participant1"],
            participant_setup["participant2"],
            participant_setup["participant3"],
        ]:
            api_client.post(
                f"/tasks/{task_id}/participants",
                json={},
                headers={"Authorization": f"Bearer {p.token}"},
            )

        participants_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        participants = participants_resp.json()["data"]["participants"]

        api_client.patch(
            f"/tasks/{task_id}/participants/{participants[0]['id']}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        api_client.patch(
            f"/tasks/{task_id}/participants/{participants[1]['id']}",
            json={"approved": "DISAPPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        approved_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            params={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert approved_resp.status_code == 200
        assert len(approved_resp.json()["data"]["participants"]) == 1

        disapproved_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            params={"approved": "DISAPPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert disapproved_resp.status_code == 200
        assert len(disapproved_resp.json()["data"]["participants"]) == 1

        none_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            params={"approved": "NONE"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert none_resp.status_code == 200
        assert len(none_resp.json()["data"]["participants"]) == 1

    def test_update_participant_deadline(
        self, api_client: httpx.Client, participant_setup: dict
    ) -> None:
        creator = participant_setup["creator"]
        participant1 = participant_setup["participant1"]
        task_id = participant_setup["task_id"]

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant1.token}"},
        )
        participant_id = join_resp.json()["data"]["participant"]["id"]

        new_deadline_ms = int((datetime.now(timezone.utc) + timedelta(days=14)).timestamp() * 1000)

        update_resp = api_client.patch(
            f"/tasks/{task_id}/participants/{participant_id}",
            json={"deadline": new_deadline_ms},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert update_resp.status_code == 200, f"Expected 200, got {update_resp.status_code}"

    def test_remove_participant(self, api_client: httpx.Client, participant_setup: dict) -> None:
        participant1 = participant_setup["participant1"]
        task_id = participant_setup["task_id"]

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant1.token}"},
        )
        participant_id = join_resp.json()["data"]["participant"]["id"]

        delete_resp = api_client.delete(
            f"/tasks/{task_id}/participants/{participant_id}",
            headers={"Authorization": f"Bearer {participant1.token}"},
        )
        assert delete_resp.status_code == 204

    def test_remove_participant_by_member(
        self, api_client: httpx.Client, participant_setup: dict
    ) -> None:
        participant1 = participant_setup["participant1"]
        task_id = participant_setup["task_id"]

        api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant1.token}"},
        )

        delete_resp = api_client.delete(
            f"/tasks/{task_id}/participants",
            params={"member": participant1.user_id},
            headers={"Authorization": f"Bearer {participant1.token}"},
        )
        assert delete_resp.status_code == 204

    def test_update_participant_by_member(
        self, api_client: httpx.Client, participant_setup: dict
    ) -> None:
        creator = participant_setup["creator"]
        participant1 = participant_setup["participant1"]
        task_id = participant_setup["task_id"]

        api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant1.token}"},
        )

        update_resp = api_client.patch(
            f"/tasks/{task_id}/participants",
            params={"member": participant1.user_id},
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert update_resp.status_code == 200
        participants = update_resp.json()["data"]["participants"]
        assert len(participants) >= 1

    def test_duplicate_participant_fails(
        self, api_client: httpx.Client, participant_setup: dict
    ) -> None:
        participant1 = participant_setup["participant1"]
        task_id = participant_setup["task_id"]

        join_resp1 = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant1.token}"},
        )
        assert join_resp1.status_code == 200, f"First join failed: {join_resp1.text}"

        join_resp2 = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant1.token}"},
        )
        assert (
            join_resp2.status_code == 400
        ), f"Expected 400 for duplicate join, got {join_resp2.status_code}"


class TestTaskSubmission:
    @pytest.fixture
    def submission_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Test Space ({suffix})",
                "intro": "A space for testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Submission Test Task ({suffix})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        participant_id = join_resp.json()["data"]["participant"]["id"]

        api_client.patch(
            f"/tasks/{task_id}/participants/{participant_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        return {
            "creator": creator,
            "participant": participant,
            "task_id": task_id,
            "participant_id": participant_id,
        }

    def test_create_submission(self, api_client: httpx.Client, submission_setup: dict) -> None:
        participant = submission_setup["participant"]
        task_id = submission_setup["task_id"]
        participant_id = submission_setup["participant_id"]

        submission_resp = api_client.post(
            f"/tasks/{task_id}/participants/{participant_id}/submissions",
            json=[{"text": "My submission content"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert submission_resp.status_code == 200
        assert "submission" in submission_resp.json()["data"]

    def test_list_submissions(self, api_client: httpx.Client, submission_setup: dict) -> None:
        participant = submission_setup["participant"]
        task_id = submission_setup["task_id"]
        participant_id = submission_setup["participant_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{participant_id}/submissions",
            json=[{"text": "My submission content"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        list_resp = api_client.get(
            f"/tasks/{task_id}/participants/{participant_id}/submissions",
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert list_resp.status_code == 200
        assert "submissions" in list_resp.json()["data"]


class TestTaskReview:
    @pytest.fixture
    def review_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Test Space ({suffix})",
                "intro": "A space for testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Review Test Task ({suffix})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        participant_id = join_resp.json()["data"]["participant"]["id"]

        api_client.patch(
            f"/tasks/{task_id}/participants/{participant_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        submission_resp = api_client.post(
            f"/tasks/{task_id}/participants/{participant_id}/submissions",
            json=[{"text": "My submission content"}],
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        submission_id = submission_resp.json()["data"]["submission"]["id"]

        return {
            "creator": creator,
            "participant": participant,
            "task_id": task_id,
            "participant_id": participant_id,
            "submission_id": submission_id,
        }

    def test_create_review(self, api_client: httpx.Client, review_setup: dict) -> None:
        creator = review_setup["creator"]
        task_id = review_setup["task_id"]
        participant_id = review_setup["participant_id"]
        submission_id = review_setup["submission_id"]

        review_resp = api_client.post(
            f"/tasks/{task_id}/participants/{participant_id}/submissions/{submission_id}/review",
            json={
                "accepted": True,
                "score": 90,
                "comment": "Good work!",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert review_resp.status_code == 200
        assert "review" in review_resp.json()["data"]

    def test_update_review(self, api_client: httpx.Client, review_setup: dict) -> None:
        creator = review_setup["creator"]
        task_id = review_setup["task_id"]
        participant_id = review_setup["participant_id"]
        submission_id = review_setup["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{participant_id}/submissions/{submission_id}/review",
            json={
                "accepted": True,
                "score": 90,
                "comment": "Good work!",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        update_resp = api_client.patch(
            f"/tasks/{task_id}/participants/{participant_id}/submissions/{submission_id}/review",
            json={
                "score": 95,
                "comment": "Excellent work!",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert update_resp.status_code == 200

    def test_delete_review(self, api_client: httpx.Client, review_setup: dict) -> None:
        creator = review_setup["creator"]
        task_id = review_setup["task_id"]
        participant_id = review_setup["participant_id"]
        submission_id = review_setup["submission_id"]

        api_client.post(
            f"/tasks/{task_id}/participants/{participant_id}/submissions/{submission_id}/review",
            json={
                "accepted": True,
                "score": 90,
                "comment": "Good work!",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        delete_resp = api_client.delete(
            f"/tasks/{task_id}/participants/{participant_id}/submissions/{submission_id}/review",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert delete_resp.status_code == 200


class TestTeamTask:
    @pytest.fixture
    def team_task_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Test Space ({suffix})",
                "intro": "A space for testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Test Team ({suffix})",
                "intro": "A team for testing",
                "description": "Test description",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = team_resp.json()["data"]["team"]["id"]

        return {
            "creator": creator,
            "space_id": space_id,
            "category_id": category_id,
            "team_id": team_id,
            "suffix": suffix,
        }

    def test_create_team_task(self, api_client: httpx.Client, team_task_setup: dict) -> None:
        creator = team_task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Team Task ({team_task_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": team_task_setup["space_id"],
                "categoryId": team_task_setup["category_id"],
                "submitterType": "TEAM",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "minTeamSize": 2,
                "maxTeamSize": 5,
            },
            headers=headers,
        )
        assert create_resp.status_code == 200
        task = create_resp.json()["data"]["task"]
        assert task["id"] > 0

    def test_add_team_to_task(self, api_client: httpx.Client, team_task_setup: dict) -> None:
        creator = team_task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Team Task ({team_task_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": team_task_setup["space_id"],
                "categoryId": team_task_setup["category_id"],
                "submitterType": "TEAM",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers=headers,
        )

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": team_task_setup["team_id"]},
            json={},
            headers=headers,
        )
        assert join_resp.status_code == 200, f"Join failed: {join_resp.text}"

    def test_get_teams_for_task(self, api_client: httpx.Client, team_task_setup: dict) -> None:
        creator = team_task_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Team Task ({team_task_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": team_task_setup["space_id"],
                "categoryId": team_task_setup["category_id"],
                "submitterType": "TEAM",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        teams_resp = api_client.get(
            f"/tasks/{task_id}/teams",
            headers=headers,
        )
        assert teams_resp.status_code == 200
        assert "teams" in teams_resp.json()["data"]


class TestCategoryIntegration:
    @pytest.fixture
    def category_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Category Test Space ({suffix})",
                "intro": "A space for testing categories",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        default_category_id = space_data.get("defaultCategoryId")

        return {
            "creator": creator,
            "space_id": space_id,
            "default_category_id": default_category_id,
            "suffix": suffix,
        }

    def test_create_category(self, api_client: httpx.Client, category_setup: dict) -> None:
        creator = category_setup["creator"]
        space_id = category_setup["space_id"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={
                "name": f"Custom Category ({category_setup['suffix']})",
                "description": "A custom category for testing",
            },
            headers=headers,
        )
        assert create_resp.status_code == 201, f"Create category failed: {create_resp.text}"
        assert "category" in create_resp.json()["data"]

    def test_list_categories(self, api_client: httpx.Client, category_setup: dict) -> None:
        creator = category_setup["creator"]
        space_id = category_setup["space_id"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        list_resp = api_client.get(
            f"/spaces/{space_id}/categories",
            headers=headers,
        )
        assert list_resp.status_code == 200
        assert "categories" in list_resp.json()["data"]


class TestTaskPermissions:
    @pytest.fixture
    def permission_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)

        other_user = user_client.create_user()
        other_user.token = user_client.login(api_client, other_user.username, other_user.password)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Permission Test Space ({suffix})",
                "intro": "A space for testing permissions",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        return {
            "owner": owner,
            "other_user": other_user,
            "space_id": space_id,
            "category_id": category_id,
            "suffix": suffix,
        }

    def test_delete_task_forbidden_for_non_owner(
        self, api_client: httpx.Client, permission_setup: dict
    ) -> None:
        owner = permission_setup["owner"]
        other_user = permission_setup["other_user"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Delete Permission Test ({permission_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": permission_setup["space_id"],
                "categoryId": permission_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        delete_resp = api_client.delete(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {other_user.token}"},
        )
        assert (
            delete_resp.status_code == 403
        ), f"Expected 403 Forbidden, got {delete_resp.status_code}"

    def test_delete_task_success_for_owner(
        self, api_client: httpx.Client, permission_setup: dict
    ) -> None:
        owner = permission_setup["owner"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Delete Owner Test ({permission_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": permission_setup["space_id"],
                "categoryId": permission_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        delete_resp = api_client.delete(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert delete_resp.status_code == 204

    def test_update_task_forbidden_for_non_owner(
        self, api_client: httpx.Client, permission_setup: dict
    ) -> None:
        owner = permission_setup["owner"]
        other_user = permission_setup["other_user"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Update Permission Test ({permission_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": permission_setup["space_id"],
                "categoryId": permission_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        update_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"name": "Should Fail"},
            headers={"Authorization": f"Bearer {other_user.token}"},
        )
        assert (
            update_resp.status_code == 403
        ), f"Expected 403 Forbidden, got {update_resp.status_code}"

    def test_join_unapproved_task_fails(
        self, api_client: httpx.Client, permission_setup: dict
    ) -> None:
        owner = permission_setup["owner"]
        other_user = permission_setup["other_user"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Unapproved Join Test ({permission_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": permission_setup["space_id"],
                "categoryId": permission_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {other_user.token}"},
        )
        assert join_resp.status_code == 400, f"Expected 400, got {join_resp.status_code}"

    def test_enumerate_unapproved_tasks_as_space_admin(
        self, api_client: httpx.Client, permission_setup: dict
    ) -> None:
        """Space admin can enumerate unapproved tasks with approved=NONE."""
        owner = permission_setup["owner"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Unapproved Enum Test ({permission_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": permission_setup["space_id"],
                "categoryId": permission_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert create_resp.status_code == 200

        enum_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": permission_setup["space_id"],
                "approved": "NONE",
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert (
            enum_resp.status_code == 200
        ), f"Expected 200, got {enum_resp.status_code}: {enum_resp.text}"
        tasks = enum_resp.json()["data"]["tasks"]
        assert len(tasks) >= 1

    def test_enumerate_unapproved_tasks_fails_for_non_admin(
        self, api_client: httpx.Client, permission_setup: dict
    ) -> None:
        """Non-admin cannot enumerate unapproved tasks with approved=NONE."""
        owner = permission_setup["owner"]
        other_user = permission_setup["other_user"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Unapproved Enum Fail Test ({permission_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": permission_setup["space_id"],
                "categoryId": permission_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert create_resp.status_code == 200

        enum_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": permission_setup["space_id"],
                "approved": "NONE",
            },
            headers={"Authorization": f"Bearer {other_user.token}"},
        )
        assert (
            enum_resp.status_code == 403
        ), f"Expected 403, got {enum_resp.status_code}: {enum_resp.text}"

    def test_get_unapproved_task_as_space_admin(
        self, api_client: httpx.Client, permission_setup: dict
    ) -> None:
        """Space admin can get an unapproved task."""
        owner = permission_setup["owner"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Unapproved Get Admin Test ({permission_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": permission_setup["space_id"],
                "categoryId": permission_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        get_resp = api_client.get(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        assert (
            get_resp.status_code == 200
        ), f"Expected 200, got {get_resp.status_code}: {get_resp.text}"
        assert get_resp.json()["data"]["task"]["approved"] == "NONE"

    def test_get_unapproved_task_fails_for_non_admin(
        self, api_client: httpx.Client, permission_setup: dict
    ) -> None:
        """Non-admin cannot get an unapproved task."""
        owner = permission_setup["owner"]
        other_user = permission_setup["other_user"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Unapproved Get Fail Test ({permission_setup['suffix']})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": permission_setup["space_id"],
                "categoryId": permission_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        get_resp = api_client.get(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {other_user.token}"},
        )
        assert (
            get_resp.status_code == 403
        ), f"Expected 403, got {get_resp.status_code}: {get_resp.text}"


class TestTaskJoinedFilter:
    @pytest.fixture
    def joined_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Joined Filter Space ({suffix})",
                "intro": "A space for testing joined filter",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        task_ids = []
        for i in range(3):
            create_resp = api_client.post(
                "/tasks",
                json={
                    "name": f"Joined Filter Task {i} ({suffix})",
                    "intro": f"Intro for task {i}",
                    "description": '{"type":"doc","content":[]}',
                    "space": space_id,
                    "categoryId": category_id,
                    "submitterType": "USER",
                    "resubmittable": True,
                    "editable": True,
                    "defaultDeadline": 30,
                },
                headers={"Authorization": f"Bearer {creator.token}"},
            )
            task_ids.append(create_resp.json()["data"]["task"]["id"])

            api_client.patch(
                f"/tasks/{task_ids[-1]}",
                json={"approved": "APPROVED"},
                headers={"Authorization": f"Bearer {creator.token}"},
            )

        api_client.post(
            f"/tasks/{task_ids[0]}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )

        return {
            "creator": creator,
            "participant": participant,
            "space_id": space_id,
            "task_ids": task_ids,
        }

    def test_enumerate_tasks_joined_true(
        self, api_client: httpx.Client, joined_setup: dict
    ) -> None:
        participant = joined_setup["participant"]
        space_id = joined_setup["space_id"]

        list_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": space_id,
                "joined": "true",
            },
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert list_resp.status_code == 200
        tasks = list_resp.json()["data"]["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["id"] == joined_setup["task_ids"][0]

    def test_enumerate_tasks_joined_false(
        self, api_client: httpx.Client, joined_setup: dict
    ) -> None:
        participant = joined_setup["participant"]
        space_id = joined_setup["space_id"]

        list_resp = api_client.get(
            "/tasks",
            params={
                "spaceId": space_id,
                "joined": "false",
            },
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert list_resp.status_code == 200
        tasks = list_resp.json()["data"]["tasks"]
        assert len(tasks) == 2
        task_ids = [t["id"] for t in tasks]
        assert joined_setup["task_ids"][0] not in task_ids


class TestParticipantEdgeCases:
    @pytest.fixture
    def edge_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Edge Case Space ({suffix})",
                "intro": "A space for edge case testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Edge Case Task ({suffix})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        return {
            "creator": creator,
            "participant": participant,
            "task_id": task_id,
        }

    def test_owner_can_add_participant_with_deadline(
        self, api_client: httpx.Client, edge_setup: dict
    ) -> None:
        creator = edge_setup["creator"]
        participant = edge_setup["participant"]
        task_id = edge_setup["task_id"]

        deadline_ms = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000)

        add_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": participant.user_id},
            json={"deadline": deadline_ms, "email": "test@example.com"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert add_resp.status_code == 200, f"Add participant failed: {add_resp.text}"

    def test_get_single_participant(self, api_client: httpx.Client, edge_setup: dict) -> None:
        creator = edge_setup["creator"]
        participant = edge_setup["participant"]
        task_id = edge_setup["task_id"]

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        participant_id = join_resp.json()["data"]["participant"]["id"]

        get_resp = api_client.get(
            f"/tasks/{task_id}/participants/{participant_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["participant"]["id"] == participant_id

    def test_remove_self_from_task(self, api_client: httpx.Client, edge_setup: dict) -> None:
        participant = edge_setup["participant"]
        task_id = edge_setup["task_id"]

        join_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            json={},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        participant_id = join_resp.json()["data"]["participant"]["id"]

        delete_resp = api_client.delete(
            f"/tasks/{task_id}/participants/{participant_id}",
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert delete_resp.status_code == 204


class TestTaskFullUpdate:
    @pytest.fixture
    def full_update_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Full Update Space ({suffix})",
                "intro": "A space for full update testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        return {
            "creator": creator,
            "space_id": space_id,
            "category_id": category_id,
            "suffix": suffix,
        }

    def test_update_task_with_full_request(
        self, api_client: httpx.Client, full_update_setup: dict
    ) -> None:
        creator = full_update_setup["creator"]
        headers = {"Authorization": f"Bearer {creator.token}"}

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Full Update Test ({full_update_setup['suffix']})",
                "intro": "Original intro",
                "description": '{"type":"doc","content":[]}',
                "space": full_update_setup["space_id"],
                "categoryId": full_update_setup["category_id"],
                "submitterType": "USER",
                "resubmittable": False,
                "editable": False,
                "defaultDeadline": 30,
            },
            headers=headers,
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        new_deadline_ms = int((datetime.now(timezone.utc) + timedelta(days=14)).timestamp() * 1000)

        patch_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={
                "name": f"Updated Name ({full_update_setup['suffix']})",
                "intro": "Updated intro",
                "description": '{"type":"doc","content":[{"type":"paragraph"}]}',
                "resubmittable": True,
                "editable": True,
                "deadline": new_deadline_ms,
                "defaultDeadline": 60,
            },
            headers=headers,
        )
        assert patch_resp.status_code == 200
        task = patch_resp.json()["data"]["task"]
        assert "Updated Name" in task["name"]
        assert task["intro"] == "Updated intro"
        assert task["resubmittable"] is True
        assert task["editable"] is True


class TestParticipantPermissions:
    @pytest.fixture
    def perm_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        owner = user_client.create_user()
        owner.token = user_client.login(api_client, owner.username, owner.password)

        user1 = user_client.create_user()
        user1.token = user_client.login(api_client, user1.username, user1.password)

        user2 = user_client.create_user()
        user2.token = user_client.login(api_client, user2.username, user2.password)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Perm Test Space ({suffix})",
                "intro": "A space for permission testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Perm Test Task ({suffix})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {owner.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {owner.token}"},
        )

        return {
            "owner": owner,
            "user1": user1,
            "user2": user2,
            "task_id": task_id,
        }

    def test_non_owner_add_participant_with_deadline(
        self, api_client: httpx.Client, perm_setup: dict
    ) -> None:
        user1 = perm_setup["user1"]
        user2 = perm_setup["user2"]
        task_id = perm_setup["task_id"]

        deadline_ms = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000)

        add_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": user2.user_id},
            json={"deadline": deadline_ms, "email": "test@example.com"},
            headers={"Authorization": f"Bearer {user1.token}"},
        )
        assert add_resp.status_code == 403, f"Expected 403, got {add_resp.status_code}"


class TestTeamParticipant:
    @pytest.fixture
    def team_participant_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Team Part Space ({suffix})",
                "intro": "A space for team participant testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        team_resp = api_client.post(
            "/teams",
            json={
                "name": f"Test Team ({suffix})",
                "intro": "A team for testing",
                "description": "Test description",
                "avatarId": 1,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        team_id = team_resp.json()["data"]["team"]["id"]

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Team Part Task ({suffix})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": category_id,
                "submitterType": "TEAM",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        return {
            "creator": creator,
            "team_id": team_id,
            "task_id": task_id,
        }

    def test_add_team_to_task(self, api_client: httpx.Client, team_participant_setup: dict) -> None:
        creator = team_participant_setup["creator"]
        team_id = team_participant_setup["team_id"]
        task_id = team_participant_setup["task_id"]

        add_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": team_id},
            json={},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert add_resp.status_code == 200, f"Add team participant failed: {add_resp.text}"

    def test_add_team_again_fails(
        self, api_client: httpx.Client, team_participant_setup: dict
    ) -> None:
        creator = team_participant_setup["creator"]
        team_id = team_participant_setup["team_id"]
        task_id = team_participant_setup["task_id"]

        api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": team_id},
            json={},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        add_again_resp = api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": team_id},
            json={},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert (
            add_again_resp.status_code == 400
        ), f"Expected 400 for duplicate add, got {add_again_resp.status_code}"

    def test_list_team_participants(
        self, api_client: httpx.Client, team_participant_setup: dict
    ) -> None:
        creator = team_participant_setup["creator"]
        team_id = team_participant_setup["team_id"]
        task_id = team_participant_setup["task_id"]

        api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": team_id},
            json={},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        list_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert list_resp.status_code == 200
        participants = list_resp.json()["data"]["participants"]
        assert len(participants) == 1

    def test_remove_team_from_task(
        self, api_client: httpx.Client, team_participant_setup: dict
    ) -> None:
        creator = team_participant_setup["creator"]
        team_id = team_participant_setup["team_id"]
        task_id = team_participant_setup["task_id"]

        api_client.post(
            f"/tasks/{task_id}/participants",
            params={"member": team_id},
            json={},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        delete_resp = api_client.delete(
            f"/tasks/{task_id}/participants",
            params={"member": team_id},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert delete_resp.status_code == 204

        list_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert list_resp.status_code == 200
        participants = list_resp.json()["data"]["participants"]
        assert len(participants) == 0


class TestParticipantWorkflow:
    @pytest.fixture
    def workflow_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        users = []
        for _ in range(4):
            user = user_client.create_user()
            user.token = user_client.login(api_client, user.username, user.password)
            users.append(user)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Workflow Space ({suffix})",
                "intro": "A space for workflow testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        category_id = space_data.get("defaultCategoryId")

        create_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Workflow Task ({suffix})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = create_resp.json()["data"]["task"]["id"]

        api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        return {
            "creator": creator,
            "users": users,
            "task_id": task_id,
        }

    def test_full_participant_workflow(
        self, api_client: httpx.Client, workflow_setup: dict
    ) -> None:
        creator = workflow_setup["creator"]
        users = workflow_setup["users"]
        task_id = workflow_setup["task_id"]

        participant_ids = []
        for user in users:
            join_resp = api_client.post(
                f"/tasks/{task_id}/participants",
                json={},
                headers={"Authorization": f"Bearer {user.token}"},
            )
            assert join_resp.status_code == 200, f"Join failed: {join_resp.text}"
            participant_ids.append(join_resp.json()["data"]["participant"]["id"])

        list_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert list_resp.status_code == 200
        assert len(list_resp.json()["data"]["participants"]) == 4

        api_client.patch(
            f"/tasks/{task_id}/participants/{participant_ids[0]}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        api_client.patch(
            f"/tasks/{task_id}/participants/{participant_ids[1]}",
            json={"approved": "DISAPPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        approved_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            params={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert approved_resp.status_code == 200
        approved = approved_resp.json()["data"]["participants"]
        assert len(approved) == 1

        disapproved_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            params={"approved": "DISAPPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert disapproved_resp.status_code == 200
        disapproved = disapproved_resp.json()["data"]["participants"]
        assert len(disapproved) == 1

        none_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            params={"approved": "NONE"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert none_resp.status_code == 200
        none_status = none_resp.json()["data"]["participants"]
        assert len(none_status) == 2

    def test_participants_remove_self(self, api_client: httpx.Client, workflow_setup: dict) -> None:
        creator = workflow_setup["creator"]
        users = workflow_setup["users"]
        task_id = workflow_setup["task_id"]

        for user in users[:2]:
            api_client.post(
                f"/tasks/{task_id}/participants",
                json={},
                headers={"Authorization": f"Bearer {user.token}"},
            )

        for user in users[:2]:
            delete_resp = api_client.delete(
                f"/tasks/{task_id}/participants",
                params={"member": user.user_id},
                headers={"Authorization": f"Bearer {user.token}"},
            )
            assert delete_resp.status_code == 204

        list_resp = api_client.get(
            f"/tasks/{task_id}/participants",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert list_resp.status_code == 200
        assert len(list_resp.json()["data"]["participants"]) == 0


class TestCategoryDeletion:
    @pytest.fixture
    def category_delete_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Cat Delete Space ({suffix})",
                "intro": "A space for category deletion testing",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        default_category_id = space_data.get("defaultCategoryId")

        custom_cat_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={
                "name": f"Custom Cat ({suffix})",
                "description": "A custom category",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        custom_category_id = None
        if custom_cat_resp.status_code in (200, 201):
            custom_category_id = custom_cat_resp.json()["data"]["category"]["id"]

        empty_cat_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={
                "name": f"Empty Cat ({suffix})",
                "description": "An empty category",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        empty_category_id = None
        if empty_cat_resp.status_code in (200, 201):
            empty_category_id = empty_cat_resp.json()["data"]["category"]["id"]

        api_client.post(
            "/tasks",
            json={
                "name": f"Task in Custom ({suffix})",
                "intro": "Test intro",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": custom_category_id or default_category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )

        return {
            "creator": creator,
            "space_id": space_id,
            "default_category_id": default_category_id,
            "custom_category_id": custom_category_id,
            "empty_category_id": empty_category_id,
        }

    def test_delete_default_category_fails(
        self, api_client: httpx.Client, category_delete_setup: dict
    ) -> None:
        data = category_delete_setup
        creator = data["creator"]
        space_id = data["space_id"]
        default_category_id = data["default_category_id"]

        resp = api_client.delete(
            f"/spaces/{space_id}/categories/{default_category_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_delete_category_with_tasks_fails(
        self, api_client: httpx.Client, category_delete_setup: dict
    ) -> None:
        data = category_delete_setup
        creator = data["creator"]
        space_id = data["space_id"]
        custom_category_id = data["custom_category_id"]

        if custom_category_id is None:
            pytest.skip("Custom category was not created")

        resp = api_client.delete(
            f"/spaces/{space_id}/categories/{custom_category_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_delete_empty_category_succeeds(
        self, api_client: httpx.Client, category_delete_setup: dict
    ) -> None:
        data = category_delete_setup
        creator = data["creator"]
        space_id = data["space_id"]
        empty_category_id = data["empty_category_id"]

        if empty_category_id is None:
            pytest.skip("Empty category was not created")

        resp = api_client.delete(
            f"/spaces/{space_id}/categories/{empty_category_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 204, f"Expected 204, got {resp.status_code}: {resp.text}"

        get_resp = api_client.get(
            f"/spaces/{space_id}/categories/{empty_category_id}",
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert get_resp.status_code == 404, "Deleted category should return 404"


class TestArchivedCategoryAndRejectReason:
    @pytest.fixture
    def archived_category_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        non_admin = user_client.create_user()
        non_admin.token = user_client.login(api_client, non_admin.username, non_admin.password)

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Archived Cat Space ({suffix})",
                "intro": "Space for archived category tests",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert space_resp.status_code == 201
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        default_category_id = space_data.get("defaultCategoryId")

        archived_cat_resp = api_client.post(
            f"/spaces/{space_id}/categories",
            json={
                "name": f"Archived Cat ({suffix})",
                "description": "This will be archived",
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        archived_category_id = None
        if archived_cat_resp.status_code in (200, 201):
            archived_category_id = archived_cat_resp.json()["data"]["category"]["id"]
            api_client.patch(
                f"/spaces/{space_id}/categories/{archived_category_id}",
                json={"archivedAt": int(datetime.now(timezone.utc).timestamp() * 1000)},
                headers={"Authorization": f"Bearer {creator.token}"},
            )

        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Test Task for Reject ({suffix})",
                "intro": "Task intro",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": default_category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        task_id = task_resp.json()["data"]["task"]["id"]

        return {
            "creator": creator,
            "non_admin": non_admin,
            "space_id": space_id,
            "default_category_id": default_category_id,
            "archived_category_id": archived_category_id,
            "task_id": task_id,
            "suffix": suffix,
        }

    def test_create_task_in_archived_category_fails(
        self, api_client: httpx.Client, archived_category_setup: dict
    ) -> None:
        data = archived_category_setup
        creator = data["creator"]
        archived_category_id = data["archived_category_id"]

        if archived_category_id is None:
            pytest.skip("Could not create archived category")

        resp = api_client.post(
            "/tasks",
            json={
                "name": f"Task in Archived ({data['suffix']})",
                "intro": "Task intro",
                "description": '{"type":"doc","content":[]}',
                "space": data["space_id"],
                "categoryId": archived_category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"

    def test_list_categories_including_archived(
        self, api_client: httpx.Client, archived_category_setup: dict
    ) -> None:
        data = archived_category_setup
        creator = data["creator"]

        resp = api_client.get(
            f"/spaces/{data['space_id']}/categories",
            params={"includeArchived": "true"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200
        categories = resp.json()["data"]["categories"]
        archived_ids = [c["id"] for c in categories if c.get("archivedAt") is not None]
        assert data["archived_category_id"] in archived_ids

    def test_patch_reject_reason_fails_for_non_admin(
        self, api_client: httpx.Client, archived_category_setup: dict
    ) -> None:
        data = archived_category_setup
        non_admin = data["non_admin"]
        task_id = data["task_id"]

        resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"rejectReason": "Some reason"},
            headers={"Authorization": f"Bearer {non_admin.token}"},
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_patch_reject_reason_success_for_admin(
        self, api_client: httpx.Client, archived_category_setup: dict
    ) -> None:
        data = archived_category_setup
        creator = data["creator"]
        task_id = data["task_id"]

        resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"rejectReason": "Needs more details"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        task = resp.json()["data"]["task"]
        assert task.get("rejectReason") == "Needs more details"

    def test_update_task_category_to_archived_fails(
        self, api_client: httpx.Client, archived_category_setup: dict
    ) -> None:
        data = archived_category_setup
        creator = data["creator"]
        task_id = data["task_id"]
        archived_category_id = data["archived_category_id"]

        if archived_category_id is None:
            pytest.skip("Could not create archived category")

        resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"categoryId": archived_category_id},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"


class TestRegistrationStartTime:
    @pytest.fixture
    def registration_start_setup(self, user_client: UserCreator, api_client: httpx.Client) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)

        participant = user_client.create_user()
        participant.token = user_client.login(
            api_client, participant.username, participant.password
        )

        suffix = random.randint(10000000, 99999999)

        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Registration Start Space ({suffix})",
                "intro": "Space for registration start time tests",
                "description": "Test description",
                "avatarId": 1,
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert space_resp.status_code == 201
        space_data = space_resp.json()["data"]["space"]
        space_id = space_data["id"]
        default_category_id = space_data.get("defaultCategoryId")

        registration_start_at = int(
            (datetime.now(timezone.utc) + timedelta(days=2)).timestamp() * 1000
        )
        deadline_ms = int((datetime.now(timezone.utc) + timedelta(days=10)).timestamp() * 1000)

        return {
            "creator": creator,
            "participant": participant,
            "space_id": space_id,
            "category_id": default_category_id,
            "registration_start_at": registration_start_at,
            "deadline_ms": deadline_ms,
            "suffix": suffix,
        }

    def test_registration_start_time_gates_participation(
        self, api_client: httpx.Client, registration_start_setup: dict
    ) -> None:
        data = registration_start_setup
        creator = data["creator"]
        participant = data["participant"]

        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Task with Registration Start ({data['suffix']})",
                "intro": "Task with future registration start",
                "description": '{"type":"doc","content":[]}',
                "space": data["space_id"],
                "categoryId": data["category_id"],
                "submitterType": "USER",
                "registrationStartAt": data["registration_start_at"],
                "deadline": data["deadline_ms"],
                "defaultDeadline": 30,
                "resubmittable": True,
                "editable": True,
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert task_resp.status_code == 200, f"Task creation failed: {task_resp.text}"
        task_id = task_resp.json()["data"]["task"]["id"]

        approve_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"approved": "APPROVED"},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert approve_resp.status_code == 200

        eligibility_resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert eligibility_resp.status_code == 200
        task_data = eligibility_resp.json()["data"]["task"]
        eligibility = task_data.get("participationEligibility")

        assert eligibility is not None, "Participation eligibility should be present"
        user_eligibility = eligibility.get("user")
        assert user_eligibility is not None, "User eligibility should be available"
        assert (
            user_eligibility.get("eligible") is False
        ), "User should be ineligible before registration start time"

        reasons = user_eligibility.get("reasons", [])
        has_registration_not_started = any(
            r.get("code") == "REGISTRATION_NOT_STARTED" for r in reasons
        )
        assert (
            has_registration_not_started
        ), "Expected REGISTRATION_NOT_STARTED reason when start time is in the future"

        clear_start_resp = api_client.patch(
            f"/tasks/{task_id}",
            json={"hasRegistrationStart": False},
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert clear_start_resp.status_code == 200
        patched_task = clear_start_resp.json()["data"]["task"]
        assert (
            patched_task.get("registrationStartAt") is None
        ), "Registration start should be cleared after patch"

        post_eligibility_resp = api_client.get(
            f"/tasks/{task_id}",
            params={"queryJoinability": "true"},
            headers={"Authorization": f"Bearer {participant.token}"},
        )
        assert post_eligibility_resp.status_code == 200
        post_task_data = post_eligibility_resp.json()["data"]["task"]
        post_eligibility = post_task_data.get("participationEligibility")

        assert post_eligibility is not None, "Eligibility should still be returned"
        post_user_eligibility = post_eligibility.get("user")
        assert (
            post_user_eligibility is not None
        ), "User eligibility should be available after clearing start"
        assert (
            post_user_eligibility.get("eligible") is True
        ), "User should become eligible once registration start is cleared"
