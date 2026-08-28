"""`.jj/repo/config-id` must never be able to lock the backend out.

jj's per-repo config lives in the CALLER's `~/.config/jj/repos/<id>/`, and
`config-id` is the pointer to it. Any user whose home lacks that entry — every
fresh sandbox container — makes jj rewrite the file via tmp+rename, so it comes
back owned by that user with a hardcoded 0600. The backend, being a different
uid, then cannot read it, and every jj call it makes fails — `jj workspace add`
included, which is why one agent's `jj log` stopped every topic on the platform.

Modes stand in for the cross-uid check (tests do not run as another user): a
file this process cannot read is exactly the state the backend was left in.
"""

import os
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.core.errors import ValidationError
from app.domain.agent.platform_failures import (
    WORKSPACE_VCS_PERMS_CODE,
    classify_platform_failure,
)
from app.domain.workspace import service as ws


@pytest.fixture
def project(tmp_path, monkeypatch) -> uuid.UUID:
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path / "ws"))
    return uuid.uuid4()


def _config_id(repo: Path) -> Path:
    return ws._jj_store(repo) / "config-id"  # noqa: SLF001


def test_setup_leaves_no_config_id_behind(project):
    """The prevention half: a repo the backend creates has no config-id at all,
    so there is nothing for another uid to take ownership of."""
    repo = ws.ensure_repo(project)

    assert not _config_id(repo).exists()


def test_identity_survives_without_per_repo_config(project):
    """Dropping per-repo config must not cost the 芝士 authorship it carried —
    an empty author would quietly corrupt every snapshot's history."""
    repo = ws.ensure_repo(project)
    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    (wt / "hello.txt").write_text("hi\n", encoding="utf-8")
    ws._jj(wt, "commit", "-m", "work")  # noqa: SLF001

    author = ws._jj(  # noqa: SLF001
        wt,
        "log",
        "-r",
        "@-",
        "--no-graph",
        "-T",
        'author.name() ++ " " ++ author.email()',
    ).strip()
    assert author == f"{ws.CHEESE_IDENTITY.name} {ws.CHEESE_IDENTITY.email}"
    assert not _config_id(repo).exists()


def test_jj_recovers_from_a_config_id_it_cannot_read(project):
    """The reported outage, reproduced: an unreadable config-id in the shared
    store used to fail EVERY jj call. The next call must clear it and succeed."""
    repo = ws.ensure_repo(project)
    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    poisoned = _config_id(repo)
    poisoned.write_text("7dedcf902b99dde332d6", encoding="utf-8")
    os.chmod(poisoned, 0o000)

    # A workspace command reads the SHARED store, not its own .jj — this is the
    # call that broke ("tmux 后端启动失败: jj workspace failed").
    assert ws._jj(wt, "log", "-r", "@", "--no-graph", "-T", "commit_id").strip()  # noqa: SLF001
    assert not poisoned.exists()


def test_new_topics_still_start_after_the_store_is_poisoned(project):
    """The user-visible symptom was "所有新话题起不来" — cover that path itself."""
    repo = ws.ensure_repo(project)
    poisoned = _config_id(repo)
    poisoned.write_text("7dedcf902b99dde332d6", encoding="utf-8")
    os.chmod(poisoned, 0o000)

    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    assert (wt / ".jj").exists()
    assert not poisoned.exists()


def test_history_is_never_touched_by_the_repair(project):
    """`.jj` also holds the commits. The repair may only ever remove config-id."""
    repo = ws.ensure_repo(project)
    store = ws._jj_store(repo)  # noqa: SLF001
    before = sorted(p.name for p in store.iterdir())

    (store / "config-id").write_text("7dedcf902b99dde332d6", encoding="utf-8")
    ws._drop_repo_config_id(store)  # noqa: SLF001

    assert sorted(p.name for p in store.iterdir()) == before
    assert {"store", "op_store", "index"} <= set(before)


def test_an_undeletable_config_id_reports_the_real_cause(project, monkeypatch):
    """When the store itself is not writable the repair cannot run, and the user
    must not be told the AI service failed. Name the file and the fix."""
    repo = ws.ensure_repo(project)
    topic = uuid.uuid4()
    wt = ws._ensure_worktree(project, topic)  # noqa: SLF001

    poisoned = _config_id(repo)
    poisoned.write_text("7dedcf902b99dde332d6", encoding="utf-8")
    os.chmod(poisoned, 0o000)
    monkeypatch.setattr(ws, "_drop_repo_config_id", lambda store: None)

    with pytest.raises(ValidationError) as exc:
        ws._jj(wt, "log", "-r", "@", "--no-graph", "-T", "commit_id")  # noqa: SLF001

    message = str(exc.value)
    assert str(poisoned) in message  # the operator needs the path, not a trace

    failure = classify_platform_failure(message)
    assert failure is not None
    assert failure.code == WORKSPACE_VCS_PERMS_CODE
    assert failure.retryable
    # The copy the user actually reads must not blame the model provider — that
    # is the whole point: this failure used to surface as 「AI 服务返回错误」.
    assert "AI 服务" not in failure.content


def test_raw_jj_failures_keep_their_original_wording(project):
    """Only the secure-config case is reworded; everything else stays verbatim
    so ordinary jj errors are not disguised."""
    repo = ws.ensure_repo(project)

    with pytest.raises(ValidationError) as exc:
        ws._jj(repo, "log", "-r", "no-such-revision-xyz")  # noqa: SLF001

    assert str(exc.value).startswith("jj log failed:")
