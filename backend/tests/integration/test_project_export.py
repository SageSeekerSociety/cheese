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
from sqlalchemy import select

from app.core.config import settings
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.library.service import artifact_snapshot_path, write_library_file
from app.domain.project.models import ProjectArtifact, ProjectForge
from app.domain.review.models import AcceptCard, AcceptStatus, DeliverableKind
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
            room = await TopicRepository(db).add(project_id=pid, title="Public room")
            private = await TopicRepository(db).add(
                project_id=pid, title="Other person's private room"
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
            doc = Block(
                project_id=pid,
                topic_id=room.id,
                kind=BlockKind.doc,
                author_type=AuthorType.participant,
                author="export-owner",
                content="# Offline document\n",
            )
            db.add(doc)
            db.add(
                Block(
                    project_id=pid,
                    topic_id=private.id,
                    kind=BlockKind.doc,
                    author_type=AuthorType.participant,
                    author="other",
                    content="PRIVATE-SECRET",
                )
            )
            artifact = ProjectArtifact(
                project_id=pid, name="Report", about="Report fixture"
            )
            db.add(artifact)
            await db.flush()
            card = AcceptCard(
                topic_id=room.id,
                reviewer_handle="export-owner",
                artifact_id=artifact.id,
                status=AcceptStatus.accepted,
                deliverable_kind=DeliverableKind.file,
                deliverable_name="report.txt",
            )
            db.add(card)
            await db.commit()
            snapshot = artifact_snapshot_path(pid, card.id, "report.txt")
            snapshot.parent.mkdir(parents=True)
            snapshot.write_bytes(b"Retained artifact\n")
            return room.id, private.id, doc.id, snapshot

    room, private, doc, snapshot = asyncio.run(seed())
    write_library_file(pid, "source data.csv", b"a,b\n1,2\n")
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
    assert manifest["library"]["status"] == "directory_present"
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
    assert (offline / "library" / "source data.csv").read_bytes() == b"a,b\n1,2\n"
    # Agent session transcripts are not part of a project export.
    assert not (offline / "transcripts").exists()
    assert not (offline / "transcripts.json").exists()
    assert "agent session transcripts" in manifest["excluded"]
    catalog = json.loads((offline / "artifacts.json").read_text())
    assert (
        offline / catalog[0]["versions"][0]["path"]
    ).read_bytes() == b"Retained artifact\n"


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


def test_absent_library_is_explicit_in_manifest(client, exported_project):
    data = exported_project
    shutil.rmtree(data["workspace"] / ".library")
    response = client.get(f"/projects/{data['pid']}/export", headers=data["headers"])
    assert response.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        entry = archive.extractfile("manifest.json")
        assert entry is not None
        assert json.load(entry)["library"]["status"] == "directory_absent"


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
