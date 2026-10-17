"""What migration ddde8e77a6af does to what is already there (#3774).

The rules, stated before it was written:

- a project's document belongs to that project's team;
- each live entry of a team's knowledge base becomes a document of that team,
  carrying its text or its link; a deleted entry does not come back;
- the release still serving while it runs creates documents without a team,
  and those keep being accepted, with their project's team.
"""

import uuid

import pytest

from tests.integration.migration_replay import database_at, seed_room

BEFORE = "1478f65e1004"
AFTER = "ddde8e77a6af"


@pytest.fixture
def db():
    with database_at(BEFORE) as replay:
        yield replay


def _team_of(db, project) -> int:
    return db.fetchval("SELECT team_id FROM projects WHERE id = $1", project)


def _document(db, project, title: str) -> uuid.UUID:
    doc = uuid.uuid4()
    db.execute(
        "INSERT INTO documents (id, project_id, title, author, created_at,"
        " updated_at) VALUES ($1, $2, $3, 'alice', now(), now())",
        doc,
        project,
        title,
    )
    return doc


def _entry(db, team: int, name: str, kind: str, content: str, deleted=False):
    db.execute(
        "INSERT INTO knowledge (id, name, description, type, content, team_id,"
        " created_by_id, source_type, created_at, updated_at, deleted_at)"
        " VALUES ((SELECT coalesce(max(id), 0) + 1 FROM knowledge), $1, '', $2,"
        " $3::jsonb, $4, 1, 'MANUAL', now(), now(),"
        " CASE WHEN $5 THEN now() END)",
        name,
        kind,
        content,
        team,
        deleted,
    )


def test_a_projects_document_takes_its_projects_team(db, tmp_path):
    project, _room = seed_room(db)
    doc = _document(db, project, "开题报告")

    db.upgrade(AFTER, {"WORKSPACE_ROOT": str(tmp_path)})

    assert db.fetchval("SELECT team_id FROM documents WHERE id = $1", doc) == (
        _team_of(db, project)
    )


def test_live_knowledge_entries_become_the_teams_documents(db, tmp_path):
    project, _room = seed_room(db)
    team = _team_of(db, project)
    _entry(db, team, "新人须知", "TEXT", '{"text": "第一周领工位。"}')
    _entry(db, team, "组会录屏", "LINK", '{"url": "https://example.com/v"}')
    _entry(db, team, "旧条目", "TEXT", '{"text": "已删除"}', deleted=True)

    db.upgrade(AFTER, {"WORKSPACE_ROOT": str(tmp_path)})

    rows = {
        r["title"]: r["content"]
        for r in db.fetch(
            "SELECT title, content FROM documents"
            " WHERE team_id = $1 AND project_id IS NULL",
            team,
        )
    }
    assert set(rows) == {"新人须知", "组会录屏"}
    assert "第一周领工位。" in rows["新人须知"]
    assert "https://example.com/v" in rows["组会录屏"]


def test_the_release_still_serving_keeps_creating_documents(db, tmp_path):
    project, _room = seed_room(db)

    db.upgrade(AFTER, {"WORKSPACE_ROOT": str(tmp_path)})
    doc = _document(db, project, "部署时新建的")

    assert db.fetchval("SELECT team_id FROM documents WHERE id = $1", doc) == (
        _team_of(db, project)
    )
