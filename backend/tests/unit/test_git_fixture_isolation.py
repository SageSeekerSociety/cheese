"""No test reads the machine's git configuration, and no two tests share
pre-commit's store.

`_host_git_and_pre_commit_isolated` in `tests/conftest.py` is the fix; this is
the guard on it. The failure it prevents arrives as somebody else's machine: a
host with `commit.gpgsign` set makes every commit the suite runs in a throwaway
repository fail, in a test whose subject never mentions git — so the rule is
checked here on a host of our own making, whose signer is not on the PATH
either way.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

#: What the suite must put in the environment of every child it spawns. Spelled
#: out rather than imported from the fixture: a test that reads its expectation
#: from the code under test passes by construction.
_EXPECTED_ISOLATION = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}

#: Every pre-commit store the tests in this file were handed, so the last of
#: them can say no two were the same.
_STORES: list[str] = []


@pytest.fixture(autouse=True)
def _note_the_store() -> None:
    _STORES.append(os.environ.get("PRE_COMMIT_HOME", ""))


def _host_home_that_signs_every_commit(tmp_path: Path) -> Path:
    """A home directory of the shape that breaks a commit in a throwaway
    repository: every commit signed, by a program that is not there."""
    home = tmp_path / "host-home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[commit]\n\tgpgsign = true\n"
        f"[gpg]\n\tprogram = {home / 'no-such-gpg-program'}\n"
    )
    return home


def _environment_of_a_git_child(home: Path) -> dict[str, str]:
    """The suite's own environment as a git child sees it, with ``home`` as the
    machine's home. Whatever isolation is in it is the fixture's doing."""
    return {**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": str(home)}


def _repository(path: Path, env: dict[str, str]) -> Path:
    path.mkdir()
    done = subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return path


def _commit(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """An empty commit, with the identity on the command line so nothing here
    depends on a config file being readable."""
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=Cheese",
            "-c",
            "user.email=cheese@example.test",
            "commit",
            "--allow-empty",
            "-q",
            "-m",
            "a commit the host must not refuse",
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )


def test_the_suite_sets_what_keeps_git_off_the_machine() -> None:
    """The environment a test runs in, before any child is spawned."""
    assert {name: os.environ.get(name) for name in _EXPECTED_ISOLATION} == (
        _EXPECTED_ISOLATION
    )


def test_the_isolation_reaches_the_child_that_runs_git() -> None:
    """A variable only isolates anything if the process that reads it has it;
    every `git` the suite runs is a child of a test."""
    probe = (
        "import json, os; print(json.dumps("
        "{k: os.environ.get(k) for k in %r}))" % sorted(_EXPECTED_ISOLATION)
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == _EXPECTED_ISOLATION


def test_a_host_that_signs_every_commit_cannot_fail_a_commit(tmp_path: Path) -> None:
    """`git init && git commit --allow-empty` in a throwaway repository, under a
    home whose configuration demands a signature the machine cannot produce."""
    env = _environment_of_a_git_child(_host_home_that_signs_every_commit(tmp_path))
    repo = _repository(tmp_path / "repository", env)
    done = _commit(repo, env)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("variable", sorted(("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")))
def test_removing_that_isolation_is_what_makes_the_host_bite(
    tmp_path: Path, variable: str
) -> None:
    """The deliberate violation: the same host, the same repository, and only
    the one variable pointing at the host's configuration instead of at the null
    device. Without that variable the commit fails — so the test above is a
    check, not a coincidence about the machine this runs on."""
    home = _host_home_that_signs_every_commit(tmp_path)
    env = _environment_of_a_git_child(home)
    assert env.get(variable) == os.devnull, "the fixture put the null device here"

    # Both repositories are created under the environment the fixture built, so
    # the commit is the only thing that differs between the two runs.
    repo = _repository(tmp_path / "repository", env)
    assert _commit(repo, env).returncode == 0

    unisolated = {**env, variable: str(home / ".gitconfig")}
    done = _commit(repo, unisolated)
    assert done.returncode != 0, done.stderr
    assert "gpg" in done.stderr.lower() or "sign" in done.stderr.lower(), done.stderr


def test_each_test_is_handed_a_pre_commit_store_of_its_own(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Two runs sharing a machine — the self-hosted runners, a hook beside a
    test — write one store and lock each other out of pre-commit's sqlite."""
    store = Path(os.environ["PRE_COMMIT_HOME"])
    assert store.is_dir()
    assert not any(store.iterdir()), "a store with something in it is not a test's own"
    assert store.is_relative_to(tmp_path_factory.getbasetemp()), (
        "a store outside this run's temp is the machine's own, shared with"
        " whatever else is running on it"
    )


def test_no_two_tests_were_handed_the_same_store() -> None:
    """Checked last, over what every test in this file was handed."""
    assert len(set(_STORES)) == len(_STORES), _STORES


@pytest.fixture
def a_store_of_my_own(monkeypatch, tmp_path: Path) -> Path:
    store = tmp_path / "pre-commit-home-of-my-own"
    monkeypatch.setenv("PRE_COMMIT_HOME", str(store))
    return store


def test_a_test_that_names_its_own_store_keeps_it(a_store_of_my_own: Path) -> None:
    """The fixture is a default: a test with a configuration of its own — the
    one thing this suite is not allowed to take away — keeps it."""
    assert os.environ["PRE_COMMIT_HOME"] == str(a_store_of_my_own)
