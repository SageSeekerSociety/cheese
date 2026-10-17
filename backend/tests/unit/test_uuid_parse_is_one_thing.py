"""UUID text keeps its meaning in work attribution, credential scope and reuse."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ValidationError
from app.core.work_context import parse_work_id
from app.domain.agent_credential.services import ProjectAgentCredentialService
from app.domain.deliverables import artifacts
from app.domain.project.models import ProjectArtifact

_ID = uuid.UUID("934bfa5a-193d-4d51-a29b-96771eaf4aa6")


@pytest.mark.parametrize("raw", [str(_ID), _ID.hex, str(_ID).upper()])
def test_work_id_accepts_uuid_text(raw):
    assert parse_work_id(raw) == _ID


@pytest.mark.parametrize("raw", ["not-a-uuid", "", None])
def test_work_id_without_valid_uuid_has_no_attribution(raw):
    assert parse_work_id(raw) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw, expected",
    [(str(_ID), _ID), (_ID.hex, _ID), ("not-a-uuid", None), ("", None), (None, None)],
)
async def test_project_path_controls_credential_scope(raw, expected):
    service = ProjectAgentCredentialService(AsyncMock())
    assert await service.project_of_request(project_id=raw, topic_id=None) == expected


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ["not-a-uuid", ""])
async def test_invalid_topic_has_no_credential_scope(raw):
    session = AsyncMock()
    service = ProjectAgentCredentialService(session)
    assert await service.project_of_request(project_id=None, topic_id=raw) is None
    session.execute.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("raw", [str(_ID), _ID.hex, str(_ID).upper(), "not-a-uuid", ""])
async def test_reuse_resolves_only_a_listed_uuid(monkeypatch, raw):
    project_id = uuid.uuid4()
    row = ProjectArtifact(id=_ID, project_id=project_id, name="Report")
    listed = artifacts.ArtifactSummary(
        id=_ID, name="Report", version=1, delivered_at=None
    )
    monkeypatch.setattr(artifacts, "list_for_project", AsyncMock(return_value=[listed]))
    session = AsyncMock()
    session.get.return_value = row

    if raw in {"not-a-uuid", ""}:
        with pytest.raises(ValidationError):
            await artifacts.reuse(session, project_id=project_id, artifact_id=raw)
        session.get.assert_not_awaited()
    else:
        assert (
            await artifacts.reuse(session, project_id=project_id, artifact_id=raw)
            is row
        )
        session.get.assert_awaited_once_with(ProjectArtifact, _ID)
