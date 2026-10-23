"""The task worktree warms the diff the 改动 tab will ask for.

A task's checkout is a ``blob:none`` partial clone (``cheese worktree`` step 2
in ``docs/manual/dev/tasks.md``). The old side of a diff is fetched only when
something reads it, so the first person to open 改动 pays for that fetch; on a
slow machine it outlasts the browser's read budget and the tab times out once.
``cheese sync`` warms it at the end of a turn, off the sync's own path.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

_CHEESE = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"


def _load():
    loader = SourceFileLoader("cheese_cli_warmup", str(_CHEESE))
    spec = importlib.util.spec_from_loader("cheese_cli_warmup", loader)
    assert spec
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_warmup_spawns_a_background_diff_against_the_base(monkeypatch, tmp_path):
    cli = _load()
    asked = []

    def fake_git(cwd, *args, **_kwargs):
        asked.append((cwd, args))
        return "ancestor-sha"

    spawned = []

    class FakePopen:
        def __init__(self, command, **kwargs):
            spawned.append((command, kwargs))

    monkeypatch.setattr(cli, "_git_task", fake_git)
    monkeypatch.setattr(cli.subprocess, "Popen", FakePopen)

    cli._warm_task_diff(tmp_path, {"base": "main"})

    assert asked == [(tmp_path, ("merge-base", "HEAD", "refs/remotes/origin/main"))]
    command, kwargs = spawned[0]
    assert command == [
        "git",
        "-C",
        str(tmp_path),
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "ancestor-sha",
        "--",
    ]
    assert kwargs["stdin"] is cli.subprocess.DEVNULL
    assert kwargs["stdout"] is cli.subprocess.DEVNULL
    assert kwargs["stderr"] is cli.subprocess.DEVNULL
    # Not waited for: the sync must not pay this fetch on its own path.
    assert kwargs.get("start_new_session") or kwargs.get("creationflags")


def test_warmup_uses_main_when_the_task_names_no_base(monkeypatch, tmp_path):
    cli = _load()
    asked = []
    monkeypatch.setattr(
        cli,
        "_git_task",
        lambda cwd, *args, **kwargs: asked.append(args) or "ancestor-sha",
    )
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *_a, **_k: None)

    cli._warm_task_diff(tmp_path, {})

    assert asked == [("merge-base", "HEAD", "refs/remotes/origin/main")]


def test_warmup_does_nothing_when_the_base_ref_is_missing(monkeypatch, tmp_path):
    cli = _load()

    def no_base(*_args, **_kwargs):
        raise RuntimeError("no such ref")

    spawned = []
    monkeypatch.setattr(cli, "_git_task", no_base)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: spawned.append((a, k)))

    cli._warm_task_diff(tmp_path, {"base": "missing"})

    assert spawned == []
