"""A publication source reads its entry blobs in one round trip, byte for byte.

Every directory whose bundle passes validation needs its own `index.html` read
to decide whether it is a static candidate: reading that blob per candidate
sends the forge one request for each one the user is waiting on, and reading the
same oids together answers all of them in one call. The two directories below
are built so that the answer depends on which candidate gets which bytes — not
only on how many requests went out.
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
        return {oid: _blob(git_store.path(self.project_id), oid) for oid in oids}


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    project_id = uuid.uuid4()
    repo = git_store.ensure_repo(project_id)
    # alpha is a static entry: it is the one candidate.
    (repo / "alpha").mkdir()
    (repo / "alpha/index.html").write_text('<img src="alpha.png">')
    (repo / "alpha/alpha.png").write_bytes(b"\x89PNG alpha")
    # beta passes bundle validation and takes part in the batch, but its own
    # html names a file its bundle does not hold — so beta drops out, and it
    # can only drop out if beta was judged by beta's own bytes. It holds
    # alpha.png too, so the wrong bytes would put beta back in.
    (repo / "beta").mkdir()
    (repo / "beta/index.html").write_text('<img src="missing.png">')
    (repo / "beta/alpha.png").write_bytes(b"\x89PNG beta")
    # gamma never passes validation: its bundle holds a symlink.
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

    asyncio.run(publication_source(None, project_id))

    # Two of the three pass validation, and both are read in a single call;
    # gamma's symlink keeps it out of the batch entirely.
    assert reader.calls == [[alpha, beta]]


def test_each_candidate_is_decided_by_its_own_entry_blob(repository):
    project_id, repo, reader = repository

    source = asyncio.run(publication_source(None, project_id))

    # beta drops out and alpha stays in only if beta was judged by beta's own
    # html and alpha by alpha's: handed the same entry's bytes for both, the
    # two would answer alike and this list would be empty or hold both.
    assert source["candidates"] == [
        {"directory": "alpha", "entry_file": "alpha/index.html"}
    ]
