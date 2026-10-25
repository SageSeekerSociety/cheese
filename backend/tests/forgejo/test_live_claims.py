"""Opt-in: claims against the pinned, isolated Forgejo container (#4767)."""

import asyncio
import os
import uuid
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.domain.project.forge import prepare_repository

pytestmark = pytest.mark.skipif(
    not os.environ.get("FORGEJO_TEST_TOKEN_FILE"),
    reason="isolated Forgejo not configured",
)


def test_one_team_claiming_twice_at_once_makes_one_forgejo_repository(
    client, monkeypatch
):
    """#4767: two claims by one team at the same moment, against the real forge:
    one project, one account, one repository; the second claim is told to ask
    again and then finds the first's project."""
    from app.common.auth import verify_access_token
    from app.domain.agent_instance.adapters import default_teammate
    from app.domain.living_doc.overview import seed_overview
    from app.domain.project import forge
    from app.domain.project.ports import Claim
    from app.domain.project.services import ProjectService
    from app.domain.task.adapters import TaskTerms, as_brief
    from app.domain.task.models import Task
    from app.domain.team.services import team_service
    from app.domain.usage.adapters import ComputeCredits
    from tests.conftest import seed_user
    from tests.integration.test_starter_claims_at_once import _board, _create_task

    url = "http://127.0.0.1:33086"
    admin = Path(os.environ["FORGEJO_TEST_TOKEN_FILE"]).read_text().strip()
    monkeypatch.setattr(settings, "forgejo_url", url)
    monkeypatch.setattr(settings, "forgejo_api_url", url + "/api/v1")
    monkeypatch.setattr(settings, "forgejo_admin_token", admin)
    monkeypatch.setattr(settings, "forge_webhook_url", "")
    # The real forge, not the test one the client fixture puts in.
    monkeypatch.setattr(forge, "prepare_repository", prepare_repository)

    def accounts() -> set[str]:
        response = httpx.get(
            url + "/api/v1/admin/users",
            params={"limit": 50},
            headers={"Authorization": "token " + admin},
        )
        response.raise_for_status()
        return {row["login"] for row in response.json()}

    board = _board(client)
    task_id = _create_task(client, board, name="一个团队同时领两次")
    handle = f"student{uuid.uuid4().hex[:8]}"
    user_id = verify_access_token(seed_user(client, handle)).user_id
    before = accounts()

    async def claim() -> uuid.UUID:
        async with client.test_request_factory() as session:
            task = await session.get(Task, task_id)
            project = await ProjectService(
                session,
                default_teammate=default_teammate,
                compute_credits=ComputeCredits(session),
                overview=seed_overview,
                task_terms=TaskTerms(session),
            ).for_participation(
                task=as_brief(task),
                claim=Claim(member_id=user_id, is_team=False, approved=False),
                owner_handle=handle,
            )
            await session.commit()
            return project.id

    async def both():
        async with client.test_request_factory() as session:
            await team_service(session).ensure_personal_team(user_id)
            await session.commit()
        return await asyncio.gather(claim(), claim(), return_exceptions=True)

    outcomes = client.portal.call(both)
    opened = [one for one in outcomes if isinstance(one, uuid.UUID)]
    assert len(opened) == 1, outcomes
    assert client.portal.call(claim) == opened[0]
    assert accounts() - before == {f"cheese-{opened[0].hex}"}
