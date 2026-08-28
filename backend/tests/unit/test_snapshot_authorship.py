"""What `snapshot_worktree` writes into history: whose name, and what message.

Both used to be wrong in a way nobody could see from inside the platform. The
author was an address GitHub cannot link to any account, and the message was
in-house Chinese jargon ("芝士 edits（后台任务「…」结束后的最终态）") that lands
verbatim in a repo other people read.
"""

import uuid

import pytest

from app.core.config import settings
from app.domain.workspace import identity
from app.domain.workspace import service as ws


@pytest.fixture
def project(tmp_path, monkeypatch) -> uuid.UUID:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    return uuid.uuid4()


def _author(wt) -> str:
    return ws._jj(  # noqa: SLF001
        wt,
        "log",
        "-r",
        "@-",
        "--no-graph",
        "-T",
        'author.name() ++ " <" ++ author.email() ++ ">"',
    ).strip()


def _subject(wt) -> str:
    return ws._jj(  # noqa: SLF001
        wt, "log", "-r", "@-", "--no-graph", "-T", "description"
    ).strip()


def _dirty_topic(project: uuid.UUID) -> tuple[uuid.UUID, object]:
    ws.ensure_repo(project)
    topic_id = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic_id)  # noqa: SLF001
    (wt / "hello.txt").write_text("hi\n", encoding="utf-8")
    return topic_id, wt


def test_snapshot_is_authored_by_the_human_behind_the_topic(project):
    topic_id, wt = _dirty_topic(project)
    who = identity.GitIdentity("octocat", "583231+octocat@users.noreply.github.com")
    identity.remember(project, topic_id, who)

    ws.snapshot_worktree(project, topic_id)

    assert _author(wt) == "octocat <583231+octocat@users.noreply.github.com>"
    # And on the GIT side — the branch that gets pushed to GitHub is the git
    # ref, so an authorship that only exists in jj would credit nobody where it
    # counts. (`snapshot_worktree` moves the bookmark AFTER the author rewrite
    # precisely so this holds.)
    exported = ws._git(  # noqa: SLF001
        ws._repo(project),  # noqa: SLF001
        "log",
        "-1",
        "--format=%an <%ae>",
        ws.branch_for_tree(topic_id),
    ).strip()
    assert exported == "octocat <583231+octocat@users.noreply.github.com>"


def test_snapshot_stays_cheese_when_nobody_linked_github(project):
    """No connected account → no invented address. 芝士 owning its own commits
    is honest; a guessed email that links to a stranger is not."""
    topic_id, wt = _dirty_topic(project)

    ws.snapshot_worktree(project, topic_id)

    assert _author(wt) == f"{ws.JJ_USER_NAME} <{ws.JJ_USER_EMAIL}>"


def test_snapshot_subject_is_a_conventional_commit(project):
    from app.domain.review.commit_message import check_subject

    topic_id, wt = _dirty_topic(project)

    ws.snapshot_worktree(project, topic_id)

    assert check_subject(_subject(wt)) == ws.SNAPSHOT_MESSAGE


def test_a_mid_command_warning_goes_in_the_body_not_the_subject(project, monkeypatch):
    """The warning has to survive — a tree caught mid-`cheese await` may be
    half-written — but glued onto the subject it blew past 72 chars and read as
    part of the change."""
    from app.domain.agent import awaited_tasks
    from app.domain.review.commit_message import check_subject

    topic_id, wt = _dirty_topic(project)
    held = type("Held", (), {"label": "full test suite"})()
    monkeypatch.setattr(awaited_tasks, "snapshot_hold", lambda _tid: held)

    ws.snapshot_worktree(project, topic_id)

    subject, _, body = _subject(wt).partition("\n")
    assert check_subject(subject) == ws.SNAPSHOT_MESSAGE
    assert "full test suite" in body
