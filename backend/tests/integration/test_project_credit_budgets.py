"""Project caps cover all spending; member caps cover attributed task work."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete

from app.api.routes import llm_proxy
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.chat import ChatService
from app.domain.agent.repositories import AgentTurnRepository
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.conversation.models import Conversation
from app.domain.project.models import ProjectMember
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task.repositories import TaskRepository
from app.domain.topic_membership.services import TopicMemberService
from app.domain.usage.ledger import Ledger, payer_for_project
from tests.conftest import seed_user
from tests.integration.conftest import free_plan_credits, post_project


@pytest.fixture
def project(client):
    client.headers["Authorization"] = f"Bearer {seed_user(client, 'budget-owner')}"
    free_plan_credits(client, 1000)
    response = post_project(client, json={"name": "Budgeted project"})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _save(client, project, limit=100, members=None):
    response = client.put(
        f"/projects/{project['id']}/credit-budget",
        json={"credits_limit": limit, "member_limits": members or {}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _spend(client, project, credits, *, task_id=None, turn_id=None):
    async def run():
        async with client.test_factory() as session:
            await Ledger(session).record(
                await payer_for_project(session, uuid.UUID(project["id"])),
                credits=credits,
                model="example-model",
                input_tokens=10,
                output_tokens=10,
                cost_usd=credits * 0.01,
                route="gateway",
                topic_id=task_id or uuid.UUID(project["root_topic_id"]),
                turn_id=turn_id,
            )
            await session.commit()

    asyncio.run(run())


def _open_task(
    client, project, owner="budget-owner", creator="budget-owner", agent=None
):
    async def run():
        async with client.test_factory() as session:
            task = await TaskRepository(session).add(
                project_id=uuid.UUID(project["id"]),
                room_id=uuid.UUID(project["root_topic_id"]),
                title="A budgeted piece of work",
                owner_handle=owner,
                created_by=creator,
            )
            turn_id = uuid.uuid4()
            await AgentTurnRepository(session).open(
                turn_id=turn_id,
                conversation_id=task.id,
                continuation_id=turn_id,
                author="budget-owner",
                content="Do this task",
                is_resume=False,
                resendable=True,
                started_at=datetime.now(UTC),
                agent_handle=agent,
            )
            await session.commit()
            return task.id, turn_id

    return asyncio.run(run())


def test_public_spending_uses_only_the_project_budget(client, project):
    _save(client, project, members={"budget-owner": 20})
    _spend(client, project, 30)
    data = client.get(f"/projects/{project['id']}/credit-budget").json()["data"]
    assert data["credits_used"] == pytest.approx(30)
    assert data["public_credits"] == pytest.approx(30)
    assert data["members"]["budget-owner"]["credits_used"] == 0
    assert data["credits_remaining"] == pytest.approx(70)


def test_admission_issues_the_requesting_tasks_execution_id(client, project):
    async def agent_handle():
        async with client.test_factory() as session:
            row = await ProjectRepository(session).get(uuid.UUID(project["id"]))
            agent = await AgentInstanceService(session).for_project(row)
            await TopicMemberService(session).seed(
                uuid.UUID(project["root_topic_id"]),
                owner_handle="budget-owner",
                agent_handle=agent.handle,
            )
            await session.commit()
            return agent.handle

    handle = asyncio.run(agent_handle())
    task_id, turn_id = _open_task(client, project, agent=handle)
    token = mint_scoped_token(
        project_id=project["id"], topic_id=str(task_id), agent_handle=handle
    )
    result = client.post("/llm/admission", headers={"Authorization": f"Bearer {token}"})
    assert result.status_code == 200, result.text
    assert result.json()["data"]["allow"] is True, result.text
    assert result.json()["data"]["supply"]["work_id"] == str(turn_id)


def test_task_spending_counts_once_against_its_owner_and_project(client, project):
    _save(client, project, members={"budget-owner": 20})
    task_id, turn_id = _open_task(client, project)
    _spend(client, project, 15, task_id=task_id, turn_id=turn_id)
    data = client.get(f"/projects/{project['id']}/credit-budget").json()["data"]
    assert data["credits_used"] == pytest.approx(15)
    assert data["members"]["budget-owner"]["credits_used"] == pytest.approx(15)
    assert data["public_credits"] == 0
    balance = client.get(f"/projects/{project['id']}/credits").json()["data"]
    assert balance["credits_remaining"] == pytest.approx(85)
    personal = client.get("/users/me/credits/usage").json()["data"]
    assert personal["period"]["credits_used"] == pytest.approx(15)


def test_late_usage_keeps_the_owner_from_when_the_task_started(client, project):
    _save(client, project, members={"budget-owner": 20})
    seed_user(client, "budget-next")
    task_id, turn_id = _open_task(client, project)

    async def transfer():
        async with client.test_factory() as session:
            task = await TaskRepository(session).get(task_id)
            task.owner_handle = "budget-next"
            await session.commit()

    asyncio.run(transfer())
    _spend(client, project, 12, task_id=task_id, turn_id=turn_id)
    data = client.get(f"/projects/{project['id']}/credit-budget").json()["data"]
    assert data["members"]["budget-owner"]["credits_used"] == pytest.approx(12)
    assert data["members"].get("budget-next", {}).get("credits_used", 0) == 0

    async def run_again():
        async with client.test_factory() as session:
            new_turn = uuid.uuid4()
            await AgentTurnRepository(session).open(
                turn_id=new_turn,
                conversation_id=task_id,
                continuation_id=turn_id,
                author="budget-next",
                content="Continue",
                is_resume=True,
                resendable=True,
                started_at=datetime.now(UTC),
            )
            await session.commit()
            return new_turn

    next_turn = asyncio.run(run_again())
    _spend(client, project, 3, task_id=task_id, turn_id=next_turn)
    data = client.get(f"/projects/{project['id']}/credit-budget").json()["data"]
    assert data["members"]["budget-owner"]["credits_used"] == pytest.approx(12)
    assert data["members"]["budget-next"]["credits_used"] == pytest.approx(3)


@pytest.mark.parametrize("creator", ["budget-owner", "cheese"])
def test_ai_owned_work_uses_its_human_creator_or_project_owner(
    client, project, creator
):
    _save(client, project, members={"budget-owner": 10})
    task_id, turn_id = _open_task(client, project, owner="cheese", creator=creator)
    _spend(client, project, 10, task_id=task_id, turn_id=turn_id)
    data = client.get(f"/projects/{project['id']}/credit-budget").json()["data"]
    assert data["members"]["budget-owner"]["credits_used"] == pytest.approx(10)
    token = mint_scoped_token(project_id=project["id"], topic_id=str(task_id))
    response = client.post(
        "/llm/admission", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.json()["data"]["allow"] is False


def test_a_project_member_can_read_but_cannot_change_budgets(client, project):
    token = seed_user(client, "budget-member")

    async def join():
        async with client.test_factory() as session:
            session.add(
                ProjectMember(
                    project_id=uuid.UUID(project["id"]), user_handle="budget-member"
                )
            )
            await session.commit()

    asyncio.run(join())
    headers = {"Authorization": f"Bearer {token}"}
    url = f"/projects/{project['id']}/credit-budget"
    assert client.get(url, headers=headers).status_code == 200
    assert (
        client.put(url, headers=headers, json={"credits_limit": 999}).status_code == 403
    )


def test_a_project_budget_cannot_be_changed_by_an_outsider(client, project):
    _save(client, project)
    outsider = seed_user(client, "budget-outsider")
    response = client.put(
        f"/projects/{project['id']}/credit-budget",
        headers={"Authorization": f"Bearer {outsider}"},
        json={"credits_limit": 100, "member_limits": {}},
    )
    assert response.status_code in (403, 404)


@pytest.mark.parametrize("limit", [-1, float("inf")])
def test_limits_must_be_finite_and_non_negative(client, project, limit):
    response = client.put(
        f"/projects/{project['id']}/credit-budget",
        content='{"credits_limit":'
        + str(limit).replace("inf", "Infinity")
        + ',"member_limits":{}}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400


def test_project_exhaustion_refuses_further_model_calls(client, project):
    _save(client, project, limit=10)
    _spend(client, project, 10)
    token = mint_scoped_token(project_id=project["id"])
    response = client.post(
        "/llm/admission", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["allow"] is False
    assert "本项目" in data["reason"]


def test_member_exhaustion_refuses_task_calls_but_not_public_conversation(
    client, project
):
    _save(client, project, members={"budget-owner": 10})
    task_id, turn_id = _open_task(client, project)
    _spend(client, project, 10, task_id=task_id, turn_id=turn_id)
    task_token = mint_scoped_token(project_id=project["id"], topic_id=str(task_id))
    task = client.post(
        "/llm/admission", headers={"Authorization": f"Bearer {task_token}"}
    )
    assert task.status_code == 200, task.text
    assert task.json()["data"]["allow"] is False
    assert "budget-owner" in task.json()["data"]["reason"]
    public_token = mint_scoped_token(
        project_id=project["id"], topic_id=project["root_topic_id"]
    )
    public = client.post(
        "/llm/admission", headers={"Authorization": f"Bearer {public_token}"}
    )
    assert public.status_code == 200, public.text
    assert public.json()["data"]["allow"] is True


def test_direct_proxy_requests_obey_member_limits_before_calling_the_provider(
    client, project, monkeypatch
):
    _save(client, project, members={"budget-owner": 0})
    task_id, _ = _open_task(client, project)
    token = mint_scoped_token(project_id=project["id"], topic_id=str(task_id))
    monkeypatch.setattr(
        llm_proxy.settings, "anthropic_base_url", "https://model.invalid"
    )

    async def key(
        self,
        project_id,
        *,
        dream=False,
        task_id=None,
        credit_owner_handle=None,
        machine_conversation_id=None,
    ):
        return "test-key"

    monkeypatch.setattr(ChatService, "project_gateway_key", key)
    response = client.post(
        "/llm/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        json={"model": "example-model", "messages": [], "max_tokens": 1},
    )
    assert response.status_code == 400, response.text
    assert "cheese project budget:" in response.json()["error"]["message"]


def test_unattributed_task_history_is_not_assigned_to_its_current_owner(
    client, project
):
    _save(client, project, members={"budget-owner": 10})
    task_id, _ = _open_task(client, project)
    _spend(client, project, 7, task_id=task_id)
    data = client.get(f"/projects/{project['id']}/credit-budget").json()["data"]
    assert data["credits_used"] == pytest.approx(7)
    assert data["unattributed_credits"] == pytest.approx(7)
    assert data["members"]["budget-owner"]["credits_used"] == 0


def test_deleting_a_task_cannot_restore_its_spent_budget(client, project):
    _save(client, project, limit=20, members={"budget-owner": 10})
    task_id, turn_id = _open_task(client, project)
    _spend(client, project, 10, task_id=task_id, turn_id=turn_id)

    async def remove():
        async with client.test_factory() as session:
            await session.execute(
                delete(Conversation).where(Conversation.id == task_id)
            )
            await session.commit()

    asyncio.run(remove())
    data = client.get(f"/projects/{project['id']}/credit-budget").json()["data"]
    assert data["credits_used"] == pytest.approx(10)
    assert data["credits_remaining"] == pytest.approx(10)
    assert data["members"]["budget-owner"]["credits_used"] == pytest.approx(10)


def test_budget_edits_are_partial_and_can_remove_a_member_cap(client, project):
    _save(client, project, members={"budget-owner": 10})
    response = client.put(
        f"/projects/{project['id']}/credit-budget",
        json={"member_limits": {"budget-owner": None}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["credits_limit"] == 100
    assert response.json()["data"]["members"]["budget-owner"]["credits_limit"] is None
