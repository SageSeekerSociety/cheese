"""A publication source reads its entry blobs in one round trip, byte for byte.

Only the directories whose bundle passes validation can become candidates, and
the entry blob is what decides whether each one is static. Reading that blob per
candidate sends the forge one request for every candidate the user is waiting
on; reading the same oids together answers all of them in one call. The answers
must be the bytes each candidate would have got on its own.
"""

import asyncio
import subprocess
import uuid

import pytest

from app.core.config import settings
from app.domain.site import services
from app.domain.site.services import publication_source
from tests.support import git_store


def _git(repo, *args):
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _blob(repo, oid: str) -> bytes:
    return subprocess.run(
        ["git", "cat-file", "blob", oid], cwd=repo, capture_output=True, check=True
    ).stdout


class Reader:
    """ProjectFiles as publication_source uses it, recording every blob read."""

    def __init__(self, project_id):
        self.project_id = project_id
        self.calls: list[list[str]] = []
        self.answers: dict[str, bytes] = {}

    async def revision(self):
        return git_store.head(self.project_id)

    async def committed_entries(self, revision):
        repo = git_store.path(self.project_id)
        entries = []
        for record in _git(repo, "ls-tree", "-rlz", revision).split("\0"):
            if not record:
                continue
            metadata, path = record.split("\t", 1)
            mode, kind, oid, size = metadata.split()
            entries.append(
                dict(
                    path=path,
                    mode=mode,
                    kind=kind,
                    oid=oid,
                    bytes=0 if size == "-" else int(size),
                )
            )
        return entries

    async def committed_blobs(self, oids):
        self.calls.append(list(oids))
        self.answers.update(
            {oid: _blob(git_store.path(self.project_id), oid) for oid in oids}
        )
        return {oid: self.answers[oid] for oid in oids}


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    project_id = uuid.uuid4()
    repo = git_store.ensure_repo(project_id)
    (repo / "alpha").mkdir()
    (repo / "alpha/index.html").write_text('<img src="alpha.png">')
    (repo / "alpha/alpha.png").write_bytes(b"\x89PNG alpha")
    (repo / "beta").mkdir()
    (repo / "beta/index.html").write_text('<script src="beta.js"></script>')
    (repo / "beta/beta.js").write_text("document.title = 'beta'")
    (repo / "gamma").mkdir()
    (repo / "gamma/index.html").write_text('<img src="gamma.png">')
    (repo / "gamma/link.png").symlink_to("gamma.png")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "three candidate directories")
    reader = Reader(project_id)
    monkeypatch.setattr(services, "ProjectFiles", lambda *args: reader)
    return project_id, repo, reader


def test_only_the_bundles_that_pass_are_read_and_they_are_read_once(repository):
    project_id, repo, reader = repository
    alpha = _git(repo, "rev-parse", "HEAD:alpha/index.html")
    beta = _git(repo, "rev-parse", "HEAD:beta/index.html")

    source = asyncio.run(publication_source(None, project_id))

    # gamma's bundle holds a symlink, so it never becomes a candidate and its
    # entry blob is never asked for.
    assert source["candidates"] == [
        {"directory": "alpha", "entry_file": "alpha/index.html"},
        {"directory": "beta", "entry_file": "beta/index.html"},
    ]
    assert reader.calls == [[alpha, beta]]


def test_the_bytes_of_a_together_read_are_those_of_one_at_a_time(repository):
    project_id, repo, reader = repository
    alpha = _git(repo, "rev-parse", "HEAD:alpha/index.html")
    beta = _git(repo, "rev-parse", "HEAD:beta/index.html")

    asyncio.run(publication_source(None, project_id))

    assert reader.answers == {oid: _blob(repo, oid) for oid in (alpha, beta)}
