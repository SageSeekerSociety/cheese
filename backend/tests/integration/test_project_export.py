"""Actual HTTP archive can be read and cloned after all sources go offline."""

import asyncio
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select

from app.core.config import settings
from app.domain.deliverables.export import _versions_of
from app.domain.library import records as library_records
from app.domain.library.service import artifact_snapshot_path
from app.domain.library.shelf import Shelf
from app.domain.living_doc.models import Document, DocumentNode
from app.domain.project.models import Project, ProjectArtifact, ProjectForge
from app.domain.project.services import ProjectService
from app.domain.review.models import AcceptCard, AcceptStatus, DeliverableKind
from app.domain.room_task.models import Task
from app.domain.topic.repositories import TopicRepository
from tests.conftest import seed_user
from tests.integration.conftest import post_project


def git(path, *args):
    return subprocess.check_output(["git", *args], cwd=path).decode().strip()


@pytest.fixture
def exported_project(client, monkeypatch, tmp_path):
    token = seed_user(client, "export-owner")
    headers = {"Authorization": f"Bearer {token}"}
    response = post_project(client, json={"name": "Export"}, headers=headers)
    assert response.status_code == 200, response.text
    pid = uuid.UUID(response.json()["data"]["id"])
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(settings, "workspace_root", str(workspace))
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    (source / "readme.txt").write_text("Offline source\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "Initial fixture")
    git(source, "tag", "v1")
    head = git(source, "rev-parse", "HEAD")
    minter = AsyncMock()
    minter.installation_token.return_value = ("fixture-secret-do-not-export", None)
    monkeypatch.setattr(
        "app.domain.project.forge.tokens_for_project", AsyncMock(return_value=minter)
    )

    async def seed():
        async with client.test_factory() as db:
            # A channel's number and its environment are the project's to hand
            # out, so this test asks it the same way the service does.
            project = await db.get(Project, pid)
            start = ProjectService(db).channel_start
            number, environment = await start(project)
            room = await TopicRepository(db).add(
                project_id=pid,
                number=number,
                environment=environment,
                title="Public room",
            )
            private_number, environment = await start(project)
            private = await TopicRepository(db).add(
                project_id=pid,
                number=private_number,
                environment=environment,
                title="Other person's private room",
            )
            private.is_private = True
            binding = await db.scalar(
                select(ProjectForge).where(ProjectForge.project_id == pid)
            )
            assert binding is not None
            binding.kind = "forgejo"
            binding.url = str(source)
            binding.api_url = "https://forge.invalid"
            binding.repo = "fixture/project"
            binding.default_branch = "main"
            # Each a task's living document: one in the public room, one in
            # the private room.
            team = await db.scalar(select(Project.team_id).where(Project.id == pid))
            doc = Document(
                project_id=pid,
                team_id=team,
                author="export-owner",
                content="# Offline document\n",
                version=1,
            )
            secret = Document(
                project_id=pid,
                team_id=team,
                author="other",
                content="PRIVATE-SECRET",
                version=1,
            )
            db.add_all([doc, secret])
            await db.flush()
            db.add_all(
                [
                    Task(
                        project_id=pid,
                        room_id=room.id,
                        title="Public task",
                        document_id=doc.id,
                    ),
                    Task(
                        project_id=pid,
                        room_id=private.id,
                        title="Private task",
                        document_id=secret.id,
                    ),
                ]
            )
            artifact = ProjectArtifact(
                project_id=pid, name="Report", about="Report fixture"
            )
            db.add(artifact)
            await db.flush()
            card = AcceptCard(
                topic_id=room.id,
                artifact_id=artifact.id,
                status=AcceptStatus.accepted,
                deliverable_kind=DeliverableKind.file,
                deliverable_name="report.txt",
            )
            db.add(card)
            await library_records.add(
                db,
                Shelf.of_project(pid, with_task=False),
                "资料/source data.csv",
                b"a,b\n1,2\n",
                "export-owner",
                None,
            )
            await db.commit()
            snapshot = artifact_snapshot_path(pid, card.id, "report.txt")
            snapshot.parent.mkdir(parents=True)
            snapshot.write_bytes(b"Retained artifact\n")
            return room.id, private.id, doc.id, snapshot

    room, private, doc, snapshot = asyncio.run(seed())
    # Memory stays outside the exported classes even though it shares the workspace.
    (workspace / "private-memory-marker").write_text("MEMORY-SECRET")
    return dict(
        pid=pid,
        headers=headers,
        room=room,
        private=private,
        doc=doc,
        snapshot=snapshot,
        source=source,
        head=head,
        workspace=workspace,
    )


def test_http_export_is_offline_readable_and_checksums_match(
    client, exported_project, tmp_path
):
    data = exported_project
    response = client.get(f"/projects/{data['pid']}/export", headers=data["headers"])
    assert response.status_code == 200, response.text[:500]
    assert response.headers["cache-control"] == "no-store"
    assert b"PRIVATE-SECRET" not in response.content
    assert b"MEMORY-SECRET" not in response.content
    assert b"fixture-secret-do-not-export" not in response.content
    assert str(data["private"]).encode() not in response.content
    assert not list((data["workspace"] / ".exports").iterdir())
    offline = tmp_path / "offline"
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        archive.extractall(offline, filter="data")
    # This is the key boundary: the HTTP result must not depend on any live source.
    shutil.rmtree(data["source"])
    shutil.rmtree(data["workspace"])
    manifest = json.loads((offline / "manifest.json").read_text())
    assert manifest["repository"]["head"] == data["head"]
    assert manifest["library"] == {"files": 1}
    for row in manifest["files"]:
        content = (offline / row["path"]).read_bytes()
        assert len(content) == row["size"]
        assert hashlib.sha256(content).hexdigest() == row["sha256"]
    clone = tmp_path / "clone"
    git(tmp_path, "clone", str(offline / "repository.bundle"), str(clone))
    assert git(clone, "rev-parse", "HEAD") == data["head"]
    assert git(clone, "rev-parse", "v1") == data["head"]
    assert (clone / "readme.txt").read_text() == "Offline source\n"
    assert (
        offline / "documents" / f"{data['doc']}.md"
    ).read_text() == "# Offline document\n"
    library = offline / "library" / "资料" / "source data.csv"
    assert library.read_bytes() == b"a,b\n1,2\n"
    # Agent session transcripts are not part of a project export.
    assert not (offline / "transcripts").exists()
    assert not (offline / "transcripts.json").exists()
    assert "agent session transcripts" in manifest["excluded"]
    catalog = json.loads((offline / "artifacts.json").read_text())
    assert (
        offline / catalog[0]["versions"][0]["path"]
    ).read_bytes() == b"Retained artifact\n"


def _select_count_of_export(client, data, extra_tasks: int, first: int = 0) -> int:
    """Add tasks (each with a document and an artifact delivered once), then count
    the SELECTs one export request sends."""

    async def seed():
        async with client.test_factory() as db:
            team = await db.scalar(
                select(Project.team_id).where(Project.id == data["pid"])
            )
            for i in range(first, first + extra_tasks):
                doc = Document(
                    project_id=data["pid"],
                    team_id=team,
                    author="export-owner",
                    content=f"# Extra {i}\n",
                    version=1,
                )
                artifact = ProjectArtifact(
                    project_id=data["pid"], name=f"Extra {i}", about="Extra fixture"
                )
                db.add_all([doc, artifact])
                await db.flush()
                db.add_all(
                    [
                        Task(
                            project_id=data["pid"],
                            room_id=data["room"],
                            title=f"Extra task {i}",
                            document_id=doc.id,
                        ),
                        AcceptCard(
                            topic_id=data["room"],
                            reviewer_handle="export-owner",
                            artifact_id=artifact.id,
                            status=AcceptStatus.accepted,
                            deliverable_kind=DeliverableKind.link,
                            deliverable_url=f"https://example.invalid/{i}",
                        ),
                    ]
                )
            await db.commit()

    asyncio.run(seed())
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    engine = client.test_app_engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        response = client.get(
            f"/projects/{data['pid']}/export", headers=data["headers"]
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text[:500]
    return sum(1 for sql in seen if sql.lstrip().upper().startswith("SELECT"))


def test_export_query_count_does_not_grow_with_the_number_of_tasks(
    client, exported_project
):
    few = _select_count_of_export(client, exported_project, 2)
    many = _select_count_of_export(client, exported_project, 9, first=2)
    assert many == few


def test_export_batches_keep_versions_numbering_hidden_rooms_and_node_order(
    client, exported_project, tmp_path
):
    data = exported_project

    async def seed():
        async with client.test_factory() as db:
            project = await db.get(Project, data["pid"])
            number, environment = await ProjectService(db).channel_start(project)
            hidden = await TopicRepository(db).add(
                project_id=data["pid"],
                number=number,
                environment=environment,
                title="Members only",
            )
            hidden.members_only = True
            artifact = await db.scalar(
                select(ProjectArtifact).where(ProjectArtifact.project_id == data["pid"])
            )
            for room, name in ((data["room"], "second.txt"), (hidden.id, "hidden.txt")):
                db.add(
                    AcceptCard(
                        topic_id=room,
                        reviewer_handle="export-owner",
                        artifact_id=artifact.id,
                        status=AcceptStatus.accepted,
                        deliverable_kind=DeliverableKind.link,
                        deliverable_url=f"https://example.invalid/{name}",
                    )
                )
            other = ProjectArtifact(
                project_id=data["pid"], name="Other", about="Second artifact"
            )
            db.add(other)
            await db.flush()
            db.add(
                AcceptCard(
                    topic_id=data["room"],
                    reviewer_handle="export-owner",
                    artifact_id=other.id,
                    status=AcceptStatus.accepted,
                    deliverable_kind=DeliverableKind.link,
                    deliverable_url="https://example.invalid/other",
                )
            )
            db.add_all(
                [
                    DocumentNode(
                        document_id=data["doc"],
                        node_type="paragraph",
                        content=content,
                        position=position,
                        author="export-owner",
                    )
                    for content, position in (("later", 2.0), ("earlier", 1.0))
                ]
            )
            await db.commit()
            return hidden.id

    hidden_room = asyncio.run(seed())
    response = client.get(f"/projects/{data['pid']}/export", headers=data["headers"])
    assert response.status_code == 200, response.text[:500]
    offline = tmp_path / "offline"
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        archive.extractall(offline, filter="data")
    catalog = json.loads((offline / "artifacts.json").read_text())
    by_name = {row["name"]: row for row in catalog}
    assert [v["number"] for v in by_name["Report"]["versions"]] == [1, 2]
    assert [v["number"] for v in by_name["Other"]["versions"]] == [1]

    async def versions_without_the_hidden_room():
        async with client.test_factory() as db:
            ids = [uuid.UUID(by_name[n]["id"]) for n in ("Report", "Other")]
            return await _versions_of(db, ids, {hidden_room}), ids

    kept, (report_id, other_id) = asyncio.run(versions_without_the_hidden_room())
    assert [(v.number, v.room_id == hidden_room) for v in kept[report_id]] == [
        (1, False),
        (2, False),
    ]
    assert [v.number for v in kept[other_id]] == [1]
    rows = json.loads((offline / "documents.json").read_text())
    nodes = [r["content"] for r in rows if r["struct_parent"] == str(data["doc"])]
    assert nodes == ["earlier", "later"]


def test_export_requires_real_project_access_even_with_dev_auth_off(
    client, exported_project, monkeypatch
):
    url = f"/projects/{exported_project['pid']}/export"
    monkeypatch.setattr(settings, "authz_enforce_topic_access", False)
    assert client.get(url).status_code == 401
    stranger = seed_user(client, "export-stranger")
    assert (
        client.get(url, headers={"Authorization": f"Bearer {stranger}"}).status_code
        == 403
    )


@pytest.mark.parametrize(
    "failure",
    ["missing_artifact", "lfs", "submodule", "missing_git_object"],
)
def test_incomplete_source_never_returns_an_archive(client, exported_project, failure):
    data = exported_project
    if failure == "missing_artifact":
        data["snapshot"].unlink()
    elif failure == "missing_git_object":
        (
            data["source"] / ".git" / "objects" / data["head"][:2] / data["head"][2:]
        ).unlink()
    elif failure == "submodule":
        git(
            data["source"],
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{data['head']},child",
        )
        git(data["source"], "commit", "-m", "Gitlink without gitmodules fixture")
    else:
        (data["source"] / "large.bin").write_text(
            "version https://git-lfs.github.com/spec/v1\noid sha256:"
            + "a" * 64
            + "\nsize 123\n"
        )
        git(data["source"], "add", ".")
        git(data["source"], "commit", "-m", "LFS fixture")
    response = client.get(f"/projects/{data['pid']}/export", headers=data["headers"])
    assert response.status_code == 503, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert not list((data["workspace"] / ".exports").iterdir())


def test_a_library_file_whose_bytes_are_gone_returns_no_archive(
    client, exported_project
):
    """清单上有、字节却没了：不交一份缺了东西的导出包。"""
    data = exported_project
    shutil.rmtree(data["workspace"] / ".library-blobs")
    response = client.get(f"/projects/{data['pid']}/export", headers=data["headers"])
    assert response.status_code == 503, response.text
    assert not list((data["workspace"] / ".exports").iterdir())


@pytest.mark.anyio
async def test_interrupted_download_removes_temporary_archive(tmp_path):
    from app.api.routes.project_export import ProjectArchiveResponse

    work = tmp_path / "export-download"
    work.mkdir()
    archive = work / "project.tar"
    archive.write_bytes(b"archive bytes")

    async def interrupted_send(message):
        raise ConnectionError("client disconnected")

    async def receive():
        return {"type": "http.disconnect"}

    response = ProjectArchiveResponse(archive)
    with pytest.raises(ConnectionError, match="disconnected"):
        await response(
            {"type": "http", "method": "GET", "headers": []}, receive, interrupted_send
        )
    assert not work.exists()
