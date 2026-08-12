"""What `.claude/scripts/check.sh` reports when checks can't run.

The platform quality gate runs this script; a check that never ran used to be a
SKIP that stayed out of the denominator and out of the exit code, so a gate
container where only ruff could start reported `1/1 passed` and the card went
green. These tests pin the three outcomes apart (passed / blocked / skipped) by
running the real script against a fake repo whose whole toolchain is stubs.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CHECK_SH = Path(__file__).resolve().parents[3] / ".claude" / "scripts" / "check.sh"

# A `uv` that can answer everything check.sh asks of it, without a venv or a
# network. Mirrors the real invocations: `uv run [--no-sync] <tool> ...`.
UV_STUB = """#!/usr/bin/env bash
args=("$@")
for a in "${args[@]}"; do
    case "$a" in
        alembic) echo "abc123def456 (head)"; exit 0 ;;
        pytest|ruff|pyright) exit 0 ;;
        python) exit 0 ;;
    esac
done
exit 0
"""

# A `uv` that exists but can't do anything — what a network-less gate container
# with no usable venv looks like (uv must fetch an interpreter and can't).
UV_BROKEN_STUB = """#!/usr/bin/env bash
echo "error: failed to fetch python interpreter (no network)" >&2
exit 1
"""

TOOL_OK = "#!/usr/bin/env bash\nexit 0\n"
TOOL_RED = '#!/usr/bin/env bash\necho "E501 line too long"\nexit 1\n'

# The repo guards check.sh shells out to (repo-guards.yml's mirror). They are
# bash/python3 with no venv behind them, so unlike the tools above they normally
# run everywhere — the interesting cases are "reports a violation" and "isn't
# there at all", which must not look alike.
# check.sh runs the .py one through `python3`, so its stub must be Python — a
# bash body there dies of SyntaxError and reads as a violation, not as a pass.
GUARDS_OK = {
    "check-repo-rules.sh": "#!/usr/bin/env bash\nexit 0\n",
    "check-action-pins.sh": "#!/usr/bin/env bash\nexit 0\n",
    "check-migration-fork.py": "import sys\n\nsys.exit(0)\n",
}
GUARD_RED = '#!/usr/bin/env bash\necho "naive datetime at foo.py:12"\nexit 1\n'


def _write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fake_repo(
    tmp_path: Path,
    *,
    venv_tools: dict[str, str],
    uv: str,
    guards: dict[str, str] | None = None,
) -> Path:
    """A repo skeleton where check.sh runs end to end against stubs.

    `venv_tools` is what backend/.venv/bin contains — empty means "this worktree
    has no usable Python environment", the situation the gate kept hitting.
    `guards` maps guard filename -> body and REPLACES the all-passing default
    set, so a name left out of an explicit dict is absent from the worktree —
    that is how "the guard itself could not run" is set up.
    """
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    shutil.copy(CHECK_SH, _mkdirs(repo / ".claude" / "scripts" / "check.sh"))
    for name, body in (GUARDS_OK if guards is None else guards).items():
        _write_exe(repo / ".claude" / "scripts" / name, body)
    for name, body in venv_tools.items():
        _write_exe(repo / "backend" / ".venv" / "bin" / name, body)
    _write_exe(repo / "fakebin" / "uv", uv)
    return repo


def _mkdirs(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _run(repo: Path, *args: str, strict: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Only the stub `uv` is reachable; a real one on PATH would make the test
    # depend on this machine's toolchain.
    env["PATH"] = f"{repo / 'fakebin'}:/usr/bin:/bin"
    env.pop("CHECK_STRICT", None)
    if strict:
        env["CHECK_STRICT"] = "1"
    return subprocess.run(
        ["bash", str(repo / ".claude" / "scripts" / "check.sh"), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


@pytest.fixture
def working_repo(tmp_path: Path) -> Path:
    return _fake_repo(
        tmp_path,
        venv_tools={"ruff": TOOL_OK, "pyright": TOOL_OK},
        uv=UV_STUB,
    )


@pytest.fixture
def toolless_repo(tmp_path: Path) -> Path:
    return _fake_repo(tmp_path, venv_tools={}, uv=UV_BROKEN_STUB)


def test_explicit_no_tests_still_green_under_strict(working_repo: Path):
    # The gate host genuinely has no Postgres, so --no-tests is a deliberate
    # configuration, not a failure to run. It must not turn the gate red.
    result = _run(working_repo, "--no-tests", strict=True)

    assert result.returncode == 0, result.stdout
    assert "Result: 6/6 passed, 1 skipped" in result.stdout
    assert "BLOCKED" not in result.stdout


def test_blocked_checks_do_not_pass_under_strict(toolless_repo: Path):
    # Nothing venv-backed could run. Exit 2 = "the check never happened" —
    # distinct from 1 ("the code is bad") so the caller can say so on the card.
    result = _run(toolless_repo, "--no-tests", strict=True)

    assert result.returncode == 2, result.stdout
    assert "Result: 3/6 passed, 3 blocked, 1 skipped" in result.stdout
    for tool in ("ruff", "pyright", "alembic"):
        assert f"BLOCKED: {tool}" in result.stdout


def test_blocked_checks_are_reported_but_never_block_a_local_run(toolless_repo: Path):
    # Same environment without --strict: a developer on a machine without the
    # toolchain still gets out of the gate — informed, not stopped.
    result = _run(toolless_repo, "--no-tests")

    assert result.returncode == 0, result.stdout
    assert "3 blocked" in result.stdout


def test_blocked_checks_are_counted_in_the_denominator(toolless_repo: Path):
    # The old summary said "1/1 passed" whenever a single check could start.
    result = _run(toolless_repo, "--no-tests")

    assert "Result: 3/6 passed" in result.stdout


def test_a_repo_guard_violation_is_a_failure_and_does_not_abort_the_run(
    tmp_path: Path,
):
    # `set -euo pipefail` + a second, unguarded run of the guard inside the
    # `else` branch used to kill check.sh the moment a guard reported a
    # violation: no "FAIL:" line, no summary, and the exit code was the guard's
    # own rather than 1. Everything after it (migration fork, pytest) silently
    # never ran.
    repo = _fake_repo(
        tmp_path,
        venv_tools={"ruff": TOOL_OK, "pyright": TOOL_OK},
        uv=UV_STUB,
        guards=GUARDS_OK | {"check-repo-rules.sh": GUARD_RED},
    )
    result = _run(repo, "--no-tests", strict=True)

    assert result.returncode == 1, result.stdout
    assert "FAIL: repo rules" in result.stdout
    assert "naive datetime at foo.py:12" in result.stdout
    # The checks downstream of the failing guard still ran, and the run still
    # accounted for itself.
    assert "PASS: merging would not fork the alembic chain" in result.stdout
    assert "Result: 5/6 passed, 1 skipped" in result.stdout


def test_a_missing_repo_guard_is_blocked_not_a_code_failure(tmp_path: Path):
    # 127 means the guard isn't in this worktree at all. Calling that FAIL sends
    # 芝士 hunting a code problem that doesn't exist; it is the same "the check
    # never ran" fact as a missing toolchain, so it must not be green either.
    repo = _fake_repo(
        tmp_path,
        venv_tools={"ruff": TOOL_OK, "pyright": TOOL_OK},
        uv=UV_STUB,
        guards={"check-action-pins.sh": GUARDS_OK["check-action-pins.sh"]},
    )
    result = _run(repo, "--no-tests", strict=True)

    assert result.returncode == 2, result.stdout
    assert "BLOCKED: repo rules" in result.stdout
    assert "BLOCKED: migration fork" in result.stdout
    assert "FAIL" not in result.stdout
    assert "Result: 4/6 passed, 2 blocked, 1 skipped" in result.stdout


def test_the_scripts_own_self_test_passes():
    # `--self-test` drives the pass/fail/blocked accounting directly, with no
    # toolchain and no repo — including the two shapes the gate actually
    # shipped (`1/1 passed, 3 skipped` and `0/0 passed, 4 skipped`, both exit 0
    # at the time). It is what CI can run anywhere; the tests above are what
    # prove the real checks feed it the right counters.
    result = subprocess.run(
        ["bash", str(CHECK_SH), "--self-test"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stdout
    assert "self-test: ok" in result.stdout
    assert "BAD:" not in result.stdout


def test_a_run_where_nothing_actually_ran_is_not_green_under_strict(
    tmp_path: Path,
):
    # Card 14a2f2d3 went green on `Result: 0/0 passed, 4 skipped`. Every
    # un-runnable check now routes to BLOCKED, so reaching zero-real-checks
    # takes a future skip flag — the backstop refuses it anyway rather than
    # letting "nothing was wrong" mean "nothing ran".
    check = subprocess.run(
        ["bash", str(CHECK_SH), "--self-test"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "ok: [0 0 0 4 1] -> 2" in check.stdout, check.stdout
    # ...and a local run in the same state still isn't blocked.
    assert "ok: [0 0 0 4 0] -> 0" in check.stdout, check.stdout


def test_a_real_failure_still_exits_1_under_strict(tmp_path: Path):
    # A red check outranks a blocked one: the code has a verdict, report that.
    repo = _fake_repo(
        tmp_path,
        venv_tools={"ruff": TOOL_RED, "pyright": TOOL_OK},
        uv=UV_STUB,
    )
    result = _run(repo, "--no-tests", strict=True)

    assert result.returncode == 1, result.stdout
    assert "FAIL: ruff" in result.stdout


def test_unreachable_database_is_a_failure_not_a_blocked_check(working_repo: Path):
    # The DB probe answers two questions with one exit code. "There is no DB"
    # is a real pytest failure; only "I couldn't even ask" is an environment
    # limitation. Stub `uv run python` into saying the former.
    _write_exe(
        working_repo / "fakebin" / "uv",
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do\n'
        '  case "$a" in\n'
        "    alembic) echo 'abc123 (head)'; exit 0 ;;\n"
        "    python) echo 'database unreachable at localhost:5432: refused' >&2;"
        " exit 1 ;;\n"
        "  esac\n"
        "done\n"
        "exit 0\n",
    )
    result = _run(working_repo, strict=True)

    assert result.returncode == 1, result.stdout
    assert "FAIL: pytest (no DB" in result.stdout
