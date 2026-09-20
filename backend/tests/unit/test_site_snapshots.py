"""Snapshot real Git trees, including binary files and unsafe tree entries."""

import subprocess
import uuid
from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.site.models import SiteRelease
from app.domain.site.services import _snapshot, publication_source, read_release_file
from app.domain.workspace import service as ws


def _git(path, *args):
    result = subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    pid = uuid.uuid4()
    repo = ws.ensure_repo(pid)
    (repo / "web").mkdir()
    (repo / "web/index.html").write_text(
        '<img src="image.png"><script src="app.js"></script>'
    )
    (repo / "web/image.png").write_bytes(bytes(range(256)) * 4)
    (repo / "web/app.js").write_text("document.title = 'published'")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "static files")
    return pid, repo


def _publish(pid, revision):
    release_id = uuid.uuid4()
    manifest = _snapshot(pid, revision, "web", release_id)
    return SiteRelease(
        id=release_id,
        project_id=pid,
        source_revision=revision,
        directory="web",
        entry_file="index.html",
        manifest=manifest,
        published_at=datetime.now(UTC),
        published_by="alice",
    )


def test_binary_resources_are_byte_exact_and_revision_does_not_follow_checkout(project):
    pid, repo = project
    revision = ws.accepted_revision(pid)
    (repo / "web/image.png").write_bytes(b"dirty")
    release = _publish(pid, revision)
    assert read_release_file(release, "image.png") == bytes(range(256)) * 4
    assert release.manifest["image.png"]["bytes"] == 1024


@pytest.mark.parametrize("target", ["app.js", "../../outside.txt"])
def test_symlink_resources_are_refused_instead_of_dereferenced(project, target):
    pid, repo = project
    (repo / "web/link.js").symlink_to(target)
    _git(repo, "add", "web/link.js")
    _git(repo, "commit", "-m", "link resource")
    assert publication_source(pid)["candidates"] == []
    with pytest.raises(ValidationError, match="符号链接"):
        _publish(pid, ws.accepted_revision(pid))


def test_submodule_is_not_published_as_an_empty_directory(project):
    pid, repo = project
    revision = ws.accepted_revision(pid)
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{revision},web/vendor")
    _git(repo, "commit", "-m", "gitlink resource")
    assert publication_source(pid)["candidates"] == []
    with pytest.raises(ValidationError, match="子模块"):
        _publish(pid, ws.accepted_revision(pid))


def test_oversized_bundle_is_refused_before_copying(project, monkeypatch):
    from app.domain.site import services

    pid, _ = project
    monkeypatch.setattr(services, "MAX_SITE_BYTES", 100)
    assert publication_source(pid)["candidates"] == []
    with pytest.raises(ValidationError, match="总大小"):
        _publish(pid, ws.accepted_revision(pid))


def test_release_reader_never_reads_files_outside_its_manifest(project):
    from app.domain.site.services import release_directory

    pid, _ = project
    release = _publish(pid, ws.accepted_revision(pid))
    root = release_directory(release)
    (root / "not-published.txt").write_text("operator file")
    for path in ("not-published.txt", "/index.html", "../index.html", ".env", "a\\b"):
        assert read_release_file(release, path) is None


def _entry_commit(repo, html):
    (repo / "web/index.html").write_text(html)
    _git(repo, "add", "web")
    _git(repo, "commit", "-m", "change website resources")


@pytest.mark.parametrize(
    "html, message",
    [
        ('<script src="missing.js"></script>', "缺少资源：missing.js"),
        ('<img src="../outside.png">', "超出发布目录"),
        ('<link rel="stylesheet" href="missing.css">', "缺少资源：missing.css"),
        ('<link rel="modulepreload" href="missing.js">', "缺少资源：missing.js"),
        ('<link rel="shortcut icon" href="missing.ico">', "缺少资源：missing.ico"),
        ('<video src="missing.mp4"></video>', "缺少资源：missing.mp4"),
        ('<audio src="missing.mp3"></audio>', "缺少资源：missing.mp3"),
        ('<source src="missing.webm">', "缺少资源：missing.webm"),
        ('<script src="https://[broken"></script>', "地址格式无效"),
        ('<base href="https://[broken"><script src="app.js"></script>', "地址格式无效"),
    ],
)
def test_missing_or_invalid_direct_resources_are_refused(project, html, message):
    pid, repo = project
    old_release = _publish(pid, ws.accepted_revision(pid))
    _entry_commit(repo, html)
    assert publication_source(pid)["candidates"] == []
    with pytest.raises(ValidationError, match=message):
        _publish(pid, ws.accepted_revision(pid))
    assert (
        read_release_file(old_release, "index.html")
        == b'<img src="image.png"><script src="app.js"></script>'
    )


@pytest.mark.parametrize(
    "html",
    [
        '<script src="/app.js?v=2"></script><img src="/image.png#thumbnail">',
        '<base href="/"><script src="app.js"></script>',
        '<base href="./"><script src="app.js"></script>',
        '<script src="https://cdn.example.test/app.js"></script>'
        '<img src="data:image/png;base64,aGVsbG8=">',
        '<script src="//cdn.example.test/app.js"></script>'
        '<a href="missing-page.html">next</a>',
        '<base href="https://cdn.example.test/assets/">'
        '<script src="remote.js"></script>',
    ],
)
def test_valid_absolute_and_external_resources_remain_publishable(project, html):
    pid, repo = project
    _entry_commit(repo, html)
    assert publication_source(pid)["candidates"] == [
        {"directory": "web", "entry_file": "web/index.html"}
    ]
    assert (
        read_release_file(_publish(pid, ws.accepted_revision(pid)), "index.html")
        == html.encode()
    )


def test_first_base_href_changes_relative_resource_resolution(project):
    pid, repo = project
    (repo / "web/assets").mkdir()
    (repo / "web/assets/chunk.js").write_text("export const value = 1")
    _entry_commit(
        repo,
        '<base href="/assets/"><base href="/ignored/">'
        '<script src="chunk.js"></script><img src="../image.png">',
    )
    assert publication_source(pid)["candidates"] == [
        {"directory": "web", "entry_file": "web/index.html"}
    ]
    release = _publish(pid, ws.accepted_revision(pid))
    assert read_release_file(release, "assets/chunk.js") == b"export const value = 1"
    _entry_commit(repo, '<base href="/missing/"><script src="app.js"></script>')
    assert publication_source(pid)["candidates"] == []
    with pytest.raises(ValidationError, match="missing/app.js"):
        _publish(pid, ws.accepted_revision(pid))
