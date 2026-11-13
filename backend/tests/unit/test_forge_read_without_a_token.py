"""A repository read that cannot get its GitHub token says why, as a 503.

Reading a project's repository (the file browser, the diff, the work summary)
first mints the GitHub App installation token. When GitHub cannot be reached at
that moment, the person is to be told the code repository is unreachable for
now, the same answer the read itself gives when GitHub drops it, not handed an
unexplained server error.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import register_exception_handlers
from app.domain.project import forge
from app.domain.project.github_app import GitHubAppError


def _app_reading_the_repo(monkeypatch, mint_failure: Exception) -> TestClient:
    monkeypatch.setattr(
        forge,
        "binding_for_project",
        AsyncMock(
            return_value=SimpleNamespace(
                api_url="https://api.github.com", repo="acme/widgets"
            )
        ),
    )
    tokens = SimpleNamespace(installation_token=AsyncMock(side_effect=mint_failure))
    monkeypatch.setattr(forge, "tokens_for_project", AsyncMock(return_value=tokens))

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/read")
    async def read() -> dict:
        await forge.repository_data(uuid.uuid4(), None, "/contents/README.md")  # type: ignore[arg-type]
        return {}

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("mint_failure", "said"),
    [
        (httpx.ConnectError("Connection reset by peer"), "forgeUnreachable"),
        (httpx.ConnectTimeout("handshake timed out"), "forgeTimeout"),
    ],
)
def test_github_out_of_reach_at_the_token_says_the_repository_is_unreachable(
    monkeypatch, mint_failure, said
):
    r = _app_reading_the_repo(monkeypatch, mint_failure).get("/read")

    assert r.status_code == 503
    assert r.json()["error"]["i18n"]["key"] == said


def test_github_refusing_the_token_is_a_503_not_a_server_error(monkeypatch):
    refused = GitHubAppError("GitHub refused the token mint (HTTP 403): suspended")

    r = _app_reading_the_repo(monkeypatch, refused).get("/read")

    assert r.status_code == 503
    assert r.json()["error"]["i18n"]["key"] == "forgeRepositoryUnavailable"
