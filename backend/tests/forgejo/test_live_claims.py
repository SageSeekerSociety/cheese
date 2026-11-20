"""Opt-in: claims against the pinned, isolated Forgejo container (#4767)."""

import asyncio
import itertools
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

    board = _board(client)
    task_id = _create_task(client, board, name="一个团队同时领两次")
    handle = f"student{uuid.uuid4().hex[:8]}"
    user_id = verify_access_token(seed_user(client, handle)).user_id
    before = _forge_accounts(admin)

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
                # Only an approved claim is started, and starting makes the project.
                claim=Claim(member_id=user_id, is_team=False, approved=True),
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
    assert _forge_accounts(admin) - before == {f"cheese-{opened[0].hex}"}


def _real_forge(monkeypatch) -> str:
    from app.domain.project import forge

    url = "http://127.0.0.1:33086"
    admin = Path(os.environ["FORGEJO_TEST_TOKEN_FILE"]).read_text().strip()
    monkeypatch.setattr(settings, "forgejo_url", url)
    monkeypatch.setattr(settings, "forgejo_api_url", url + "/api/v1")
    monkeypatch.setattr(settings, "forgejo_admin_token", admin)
    monkeypatch.setattr(settings, "forge_webhook_url", "")
    monkeypatch.setattr(forge, "prepare_repository", prepare_repository)
    return admin


def _forge_accounts(admin: str) -> set[str]:
    """Every account on the forge, read page by page: Forgejo answers at most
    50 a page, and a forge that outlived earlier runs holds more."""
    accounts: set[str] = set()
    for page in itertools.count(1):
        response = httpx.get(
            "http://127.0.0.1:33086/api/v1/admin/users",
            params={"limit": 50, "page": page},
            headers={"Authorization": "token " + admin},
        )
        response.raise_for_status()
        if not response.json():
            return accounts
        accounts |= {row["login"] for row in response.json()}


@pytest.mark.parametrize("as_team", [False, True], ids=["person", "shared-team"])
def test_claiming_at_once_and_again_over_http_makes_one_forgejo_repository(
    client, monkeypatch, as_team
):
    """#4767, through the real HTTP routes and the real forge: two claims at the
    same moment (one person twice, or two teammates of a shared team), then
    each asking again. One claim, one project, one account and repository;
    the second at-once claim is a retryable 409, never a server error, and
    asking again answers with the project."""
    from tests.conftest import seed_user
    from tests.integration.test_claiming_twice import (
        _at_once,
        _claim,
        _live_claims,
        _on_board,
        _project,
        _real_requests,
        _shared_team,
    )
    from tests.integration.test_starter_claims_at_once import _board, _create_task

    admin = _real_forge(monkeypatch)
    _real_requests(client, monkeypatch)
    board = _board(client)
    first = seed_user(client, f"student{uuid.uuid4().hex[:8]}")
    _on_board(client, board, first)
    if as_team:
        task_id = _create_task(
            client, board, name="队友同时领", submitterType="TEAM", maxTeamSize=5
        )
        second = seed_user(client, f"mate{uuid.uuid4().hex[:8]}")
        team_id = _shared_team(client, first, _on_board(client, board, second))
        tokens = [first, second]
    else:
        task_id = _create_task(client, board, name="一个人同时点两次")
        team_id = None
        tokens = [first, first]
    before = _forge_accounts(admin)

    responses = _at_once(client, task_id, tokens, team_id)

    assert sorted(r.status_code for r in responses) == [200, 409], [
        r.text for r in responses
    ]
    opened = _project(next(r for r in responses if r.status_code == 200))
    for token in tokens:
        assert _project(_claim(client, task_id, token, team_id)) == opened
    assert _live_claims(client, task_id) == 1
    assert _forge_accounts(admin) - before == {f"cheese-{uuid.UUID(opened).hex}"}
