"""UUID text keeps its meaning in work attribution and reuse."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import UnprocessableEntityError
from app.core.work_context import parse_work_id
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
@pytest.mark.parametrize("raw", [str(_ID), _ID.hex, str(_ID).upper(), "not-a-uuid", ""])
async def test_reuse_resolves_only_a_listed_uuid(monkeypatch, raw):
    project_id = uuid.uuid4()
    row = ProjectArtifact(id=_ID, project_id=project_id, name="Report")
    listed = artifacts.ArtifactSummary(
        id=_ID, name="Report", version=1, delivered_at=None
    )
    monkeypatch.setattr(artifacts, "list_for_project", AsyncMock(return_value=[listed]))
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = row
    session.execute.return_value = result

    if raw in {"not-a-uuid", ""}:
        with pytest.raises(UnprocessableEntityError):
            await artifacts.reuse(session, project_id=project_id, artifact_id=raw)
        # 不是 id 的文字没走到「按这一件取」，清单也没被拉来判对错。
        session.execute.assert_not_awaited()
    else:
        assert (
            await artifacts.reuse(session, project_id=project_id, artifact_id=raw)
            is row
        )
        # 认出一件产物只发一条查询，不是先拉清单、再按 id 取一次。
        session.execute.assert_awaited_once()
        session.get.assert_not_awaited()
