"""A Git credential request can receive only its project's repository token."""

import importlib.util
import io
import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


@pytest.fixture
def cli(monkeypatch):
    source = Path(__file__).resolve().parents[2] / "sandbox" / "cheese"
    loader = SourceFileLoader("forge_credential_cli", str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "_call",
        lambda *args: {
            "data": {
                "url": "https://forge.invalid/owner/repo.git",
                "username": "project-bot",
                "token": "short-lived-token",
            }
        },
    )
    return module


def test_exact_repository_receives_credentials(cli, monkeypatch, capsys):
    monkeypatch.setattr(
        cli.sys,
        "stdin",
        io.StringIO("protocol=https\nhost=forge.invalid\npath=owner/repo.git\n\n"),
    )
    cli._git_credential("get")
    assert (
        capsys.readouterr().out
        == "username=project-bot\npassword=short-lived-token\n\n"
    )


@pytest.mark.parametrize(
    "credential_request",
    [
        "protocol=https\nhost=other.invalid\npath=owner/repo.git\n\n",
        "protocol=https\nhost=forge.invalid\npath=owner/other.git\n\n",
        "protocol=http\nhost=forge.invalid\npath=owner/repo.git\n\n",
        "protocol=https\nhost=forge.invalid\n\n",
    ],
)
def test_other_destination_never_receives_credentials(
    cli, monkeypatch, capsys, credential_request
):
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(credential_request))
    cli._git_credential("get")
    assert capsys.readouterr().out == ""


def test_git_cannot_persist_a_secret_through_the_helper(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_call", lambda *args: pytest.fail("store must not mint"))
    cli._git_credential("store")
    cli._git_credential("erase")
    assert capsys.readouterr().out == ""


def test_relay_receives_only_its_exact_project_repository_token(
    cli, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "API", "https://platform.invalid/api")
    monkeypatch.setattr(cli, "PROJECT", "project-id")
    monkeypatch.setattr(
        cli,
        "_call",
        lambda *_: {
            "data": {
                "kind": "forgejo",
                "url": "https://forge.invalid/owner/repo.git",
                "repo": "owner/repo",
                "username": "bot",
                "token": "project-token",
            }
        },
    )
    for project, expected in [("project-id", True), ("other", False)]:
        monkeypatch.setattr(
            cli.sys,
            "stdin",
            io.StringIO(
                f"protocol=https\nhost=platform.invalid\n"
                f"path=api/sandbox/forge/{project}/owner/repo.git\n\n"
            ),
        )
        cli._git_credential("get")
        assert bool(capsys.readouterr().out) is expected


@pytest.mark.parametrize("kind", ["forgejo", "github_app"])
def test_offline_transport_preserves_origin_and_recovers_direct_access(
    cli, monkeypatch, tmp_path, kind
):
    monkeypatch.setattr(cli, "API", "https://platform.invalid/api")
    monkeypatch.setattr(cli, "PROJECT", "project-id")
    remote = "https://forge.invalid/forge/owner/repo.git"
    cli._git_task(tmp_path, "init", "--bare")
    cli._git_task(tmp_path, "remote", "add", "origin", remote)
    metadata = {"forge_kind": kind, "forge_repo": "owner/repo", "remote": remote}

    def unreachable(*args, **kwargs):
        raise cli.urllib.error.URLError("network unreachable")

    monkeypatch.setattr(cli.urllib.request, "urlopen", unreachable)
    cli._configure_git_transport(tmp_path, metadata)
    assert cli._git_task(tmp_path, "config", "remote.origin.url") == remote
    assert cli._git_task(tmp_path, "remote", "get-url", "origin") == (
        "https://platform.invalid/api/sandbox/forge/project-id/owner/repo.git"
    )
    cli._configure_git_transport(tmp_path, metadata)
    monkeypatch.setattr(cli.urllib.request, "urlopen", lambda *a, **k: io.BytesIO())
    cli._configure_git_transport(tmp_path, metadata)
    assert cli._git_task(tmp_path, "remote", "get-url", "origin") == remote


def test_github_relay_receives_platform_credential_only(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli, "API", "https://platform.invalid")
    monkeypatch.setattr(cli, "PROJECT", "project-id")
    monkeypatch.setattr(cli, "TOKEN", "scoped-platform-token")
    monkeypatch.setattr(
        cli,
        "_call",
        lambda *_: {
            "data": {
                "kind": "github_app",
                "url": "https://github.com/owner/repo.git",
                "repo": "owner/repo",
                "username": "x-access-token",
                "token": "github-token",
            }
        },
    )
    for host, path, secret in [
        (
            "platform.invalid",
            "sandbox/forge/project-id/owner/repo.git",
            "scoped-platform-token",
        ),
        ("github.com", "owner/repo.git", "github-token"),
        ("platform.invalid", "sandbox/forge/other/owner/repo.git", None),
    ]:
        monkeypatch.setattr(
            cli.sys,
            "stdin",
            io.StringIO(f"protocol=https\nhost={host}\npath={path}\n\n"),
        )
        cli._git_credential("get")
        output = capsys.readouterr().out
        assert output == (
            f"username=x-access-token\npassword={secret}\n\n" if secret else ""
        )


@pytest.mark.parametrize("enabled", [True, False])
def test_crediting_the_owner_does_not_replace_the_agent_author(
    cli, monkeypatch, tmp_path, enabled
):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "PROJECT", "project")
    monkeypatch.setattr(cli, "TOPIC", "room")
    monkeypatch.setattr(cli, "_marked_task", lambda: "task")
    monkeypatch.setattr(
        cli,
        "_call",
        lambda *_: {
            "data": {
                "room_id": "room",
                "coauthors": ["Owner <owner@example.invalid>"] if enabled else [],
            }
        },
    )
    message = tmp_path / "message"
    message.write_text(
        "Write report\n\nCo-authored-by: Contributor <contributor@example.invalid>\n"
    )
    cli._git_attribution(str(message))
    cli._git_attribution(str(message))  # Repeated preparation cannot double-credit.
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Agent",
            "-c",
            "user.email=agent@example.invalid",
            "commit",
            "--allow-empty",
            "-F",
            str(message),
        ],
        check=True,
        capture_output=True,
    )
    committed = subprocess.check_output(
        ["git", "log", "-1", "--format=%an <%ae>%n%B"], text=True
    )
    assert committed.startswith("Agent <agent@example.invalid>\n")
    assert committed.count("Co-authored-by: Owner") == int(enabled)
    assert committed.count("Co-authored-by: Contributor") == 1


def test_each_task_checkout_commits_as_the_author_the_platform_names(
    cli, monkeypatch, tmp_path
):
    """Two tasks of one project share a repository and have different authors;
    an author in the environment would override both, so none is used."""

    def git(cwd, *args):
        return subprocess.run(
            ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    # The project's repository is bare, as `cheese worktree` makes it.
    seed, repo = tmp_path / "seed", tmp_path / "project.git"
    subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)
    git(
        seed,
        "-c",
        "user.name=x",
        "-c",
        "user.email=x@x",
        "commit",
        "--allow-empty",
        "-m",
        "base",
    )
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(repo)],
        check=True,
        capture_output=True,
    )
    first, second = tmp_path / "first", tmp_path / "second"
    git(repo, "worktree", "add", "-b", "task/one", str(first))
    git(repo, "worktree", "add", "-b", "task/two", str(second))

    cli._commit_as(repo, first, {"author": "Alice <1+alice@users.noreply.github.com>"})
    cli._commit_as(
        repo,
        second,
        {"author": "cheesex-app[bot] <2+cheesex-app[bot]@users.noreply.github.com>"},
    )
    for where in (first, second):
        (where / "work.txt").write_text("work\n")
        git(where, "add", "work.txt")
        git(where, "commit", "-m", "work")

    assert git(first, "log", "-1", "--format=%an <%ae>") == (
        "Alice <1+alice@users.noreply.github.com>"
    )
    assert git(second, "log", "-1", "--format=%an <%ae>") == (
        "cheesex-app[bot] <2+cheesex-app[bot]@users.noreply.github.com>"
    )
    # The project's repository itself stays bare.
    assert git(repo, "rev-parse", "--is-bare-repository") == "true"
