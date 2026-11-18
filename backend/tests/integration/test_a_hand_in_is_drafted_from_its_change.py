"""The 说明 of a member's hand-in is drafted from the change it hands in.

The project's own model reads the diff and writes the title and body, on the
project's key and charged to the project. When the model cannot be reached the
hand-in is not held up: the title is a line made from the changed files.
"""

import json
import uuid

import httpx

from app.core.config import settings
from app.domain.agent import hand_in_draft
from app.domain.agent.model_catalog import catalog
from app.domain.agent.supply import GATEWAY
from app.domain.project.models import Project
from tests.integration.conftest import post_project, session_auth_headers

DIFF = """diff --git a/index.html b/index.html
--- a/index.html
+++ b/index.html
@@ -1 +1 @@
-<h1>Welcome</h1>
+<h1>Sign up for the workshop</h1>
"""


class _Gateway:
    """The project's key on the gateway, and how many times it was charged."""

    def __init__(self) -> None:
        self.charged = 0

    async def project_gateway_key(self, project_id):
        return "sk-project"

    async def charge_turn_spend(self, project_id, topic_id, turn_id):
        self.charged += 1


def _project_on_a_gateway_model(client) -> tuple[str, str]:
    project = post_project(
        client, json={"name": "P"}, headers=session_auth_headers("alice")
    ).json()["data"]["id"]

    async def choose() -> str:
        async with client.test_factory() as session:
            row = await session.get(Project, uuid.UUID(project))
            ids = [
                i for i, c in catalog(row.settings).items() if c["supply"] == GATEWAY
            ]
            row.settings = {**(row.settings or {}), "default_model": ids[-1]}
            await session.commit()
            return ids[-1]

    return project, client.portal.call(choose)


def _draft(client, project: str, gateway, transport):
    async def go():
        async with client.test_factory() as session:
            return await hand_in_draft.draft(
                session,
                project_id=uuid.UUID(project),
                task_title="Workshop page",
                diff=DIFF,
                files=["index.html", "docs/README.md"],
                recent_subjects=["feat(page): add the schedule"],
                models=gateway,
                transport=transport,
            )

    return client.portal.call(go)


def test_the_projects_model_drafts_the_title_and_body(client, monkeypatch):
    project, chosen = _project_on_a_gateway_model(client)
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        content = (
            "feat(page): invite visitors to sign up\n\nThe heading asks for sign-ups."
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway.test")
    gateway = _Gateway()

    drafted = _draft(client, project, gateway, httpx.MockTransport(answer))

    assert drafted.drafted is True
    assert drafted.subject == "feat(page): invite visitors to sign up"
    assert drafted.body == "The heading asks for sign-ups."
    request = json.loads(sent[0].content)
    assert request["model"] == chosen
    assert "Sign up for the workshop" in request["messages"][1]["content"]
    assert sent[0].headers["authorization"] == "Bearer sk-project"
    assert gateway.charged == 1


def test_a_model_that_fails_leaves_a_line_from_the_files(client, monkeypatch):
    project, _ = _project_on_a_gateway_model(client)

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"error": "upstream"})

    monkeypatch.setattr(settings, "llm_gateway_admin_base", "http://gateway.test")

    drafted = _draft(client, project, _Gateway(), httpx.MockTransport(broken))

    assert drafted.drafted is False
    assert drafted.subject == "修改 index.html、README.md"
    assert drafted.body == ""
