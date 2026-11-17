"""A hosted repository moves to GitHub whole, and never over someone's work."""

import pytest

from app.core.errors import ConflictError
from app.domain.project import github_migration
from app.domain.project.forge_migration import git, references
from app.domain.project.github_migration import mirror_to_github
from app.domain.project.starter import Remote


def _source(tmp_path):
    """A bare repository like the hosted one: two branches, a tag, and a
    Forgejo PR ref that must stay behind."""
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "test@example.invalid")
    git(work, "config", "user.name", "Migration test")
    (work / "a.txt").write_text("a\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "first")
    git(work, "tag", "v1")
    git(work, "checkout", "-q", "-b", "task/1")
    (work / "b.txt").write_text("b\n")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "task work")
    bare = tmp_path / "source.git"
    git(tmp_path, "clone", "-q", "--mirror", str(work), str(bare))
    git(bare, "update-ref", "refs/pull/1/head", "refs/heads/task/1")
    return work, bare


def _remote(bare) -> Remote:
    return Remote(url=str(bare), username="cheese-x", password="unused")


def _empty(tmp_path, name="destination.git"):
    destination = tmp_path / name
    git(tmp_path, "init", "-q", "--bare", str(destination))
    return destination


def test_every_branch_and_tag_moves_and_pull_refs_stay(tmp_path):
    _, source = _source(tmp_path)
    destination = _empty(tmp_path)
    moved = mirror_to_github(_remote(source), str(destination), "token")
    expected = {
        ref: sha
        for ref, sha in references(source).items()
        if not ref.startswith("refs/pull/")
    }
    assert moved == expected
    assert references(destination) == expected
    assert set(expected) == {"refs/heads/main", "refs/heads/task/1", "refs/tags/v1"}


def test_asking_again_after_a_partial_copy_finishes_it(tmp_path):
    _, source = _source(tmp_path)
    destination = _empty(tmp_path)
    main = references(source)["refs/heads/main"]
    git(
        destination,
        "fetch",
        "-q",
        "--no-tags",
        str(source),
        "refs/heads/main:refs/heads/main",
    )
    assert references(destination) == {"refs/heads/main": main}
    moved = mirror_to_github(_remote(source), str(destination), "token")
    assert moved["refs/heads/task/1"] == references(source)["refs/heads/task/1"]
    assert mirror_to_github(_remote(source), str(destination), "token") == moved


def test_a_repository_holding_other_work_is_refused_untouched(tmp_path):
    _, source = _source(tmp_path)
    other_work = tmp_path / "other"
    other_work.mkdir()
    git(other_work, "init", "-q", "-b", "main")
    git(other_work, "config", "user.email", "test@example.invalid")
    git(other_work, "config", "user.name", "Someone else")
    (other_work / "README.md").write_text("theirs\n")
    git(other_work, "add", ".")
    git(other_work, "commit", "-q", "-m", "their README")
    destination = _empty(tmp_path)
    git(other_work, "push", "-q", str(destination), "main")
    before = references(destination)
    with pytest.raises(ConflictError, match="空仓库"):
        mirror_to_github(_remote(source), str(destination), "token")
    assert references(destination) == before


def test_a_push_landing_mid_copy_is_copied_by_the_next_round(tmp_path, monkeypatch):
    work, source = _source(tmp_path)
    destination = _empty(tmp_path)
    real = github_migration._source_refs
    rounds = []

    def source_refs_with_a_push_after_round_one(target, remote):
        if len(rounds) == 1:
            (work / "c.txt").write_text("c\n")
            git(work, "add", ".")
            git(work, "commit", "-q", "-m", "late push")
            git(work, "push", "-q", str(source), "task/1")
        rounds.append(1)
        return real(target, remote)

    monkeypatch.setattr(
        github_migration, "_source_refs", source_refs_with_a_push_after_round_one
    )
    moved = mirror_to_github(_remote(source), str(destination), "token")
    assert len(rounds) == 3
    assert moved["refs/heads/task/1"] == references(source)["refs/heads/task/1"]
    assert references(destination)["refs/heads/task/1"] == moved["refs/heads/task/1"]


def test_an_empty_hosted_repository_has_nothing_to_move(tmp_path):
    source = _empty(tmp_path, "source.git")
    destination = _empty(tmp_path)
    with pytest.raises(ConflictError, match="没有可迁"):
        mirror_to_github(_remote(source), str(destination), "token")
