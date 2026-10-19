"""Platform probe program, sent to a project's existing execution sandbox.

Standard library only. Inputs and logs survive the process. It runs in a
room, which is not a checkout, and the commit it probes may have landed after
the machine last fetched: the platform's CLI fetches that exact commit from
the project's forge and checks it out in a worktree of the probe's own
(`cheese revision-tree`), which is removed afterwards. Nothing else on the
machine is written.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def run(argv, cwd, directory, timeout=600):
    with (directory / "commands.jsonl").open("a") as ledger:
        ledger.write(
            json.dumps({"at": time.time(), "argv": argv, "cwd": str(cwd)}) + "\n"
        )
    with (directory / "output.log").open("ab") as log:
        process = subprocess.Popen(
            argv, cwd=cwd, stdout=log, stderr=log, start_new_session=os.name == "posix"
        )
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait()
            code = -1
    with (directory / "commands.jsonl").open("a") as ledger:
        ledger.write(json.dumps({"at": time.time(), "exit": code}) + "\n")
    return code


def package_of(tree, test):
    path = tree / test.split("::", 1)[0]
    if not path.resolve().is_relative_to(tree.resolve()) or not path.is_file():
        # i18n-exempt: the probe's own log on the machine; the platform reads only its verdict
        raise ValueError("Test file is absent at the probed revision")
    for parent in path.parents:
        if any(
            (parent / manifest).is_file()
            for manifest in ("pyproject.toml", "package.json", "go.mod")
        ):
            return parent
        if parent == tree:
            break
    # i18n-exempt: the probe's own log on the machine; the platform reads only its verdict
    raise ValueError("No supported package manifest owns the test")


def probe(tree, tests, directory):
    """Run ``tests`` in ``tree``, a checkout of the probed commit."""
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=tree, text=True
    ).split("\0")
    resolved = []
    for test in tests:
        name = test.split("::", 1)[0]
        matches = [
            path for path in tracked if path == name or path.endswith("/" + name)
        ]
        if len(matches) != 1:
            return "unknown"
        resolved.append(matches[0])
    tests = resolved
    packages = {package_of(tree, test) for test in tests}
    if len(packages) != 1:
        return "unknown"
    package = packages.pop()
    names = [
        str((tree / test.split("::", 1)[0]).relative_to(package)) for test in tests
    ]
    report = directory / "tests.xml"
    if (package / "uv.lock").is_file() and all(name.endswith(".py") for name in names):
        setup = ["uv", "sync", "--frozen", "--all-extras"]
        command = ["uv", "run", "--frozen", "pytest", *names, f"--junitxml={report}"]
    elif (package / "package.json").is_file():
        manifest = json.loads((package / "package.json").read_text())
        dependencies = {
            **manifest.get("dependencies", {}),
            **manifest.get("devDependencies", {}),
        }
        if (package / "pnpm-lock.yaml").is_file():
            setup, execute = ["pnpm", "install", "--frozen-lockfile"], ["pnpm", "exec"]
        elif (package / "package-lock.json").is_file():
            setup, execute = ["npm", "ci"], ["npx", "--no-install"]
        else:
            return "unknown"
        if "vitest" in dependencies:
            command = [
                *execute,
                "vitest",
                "run",
                *names,
                "--reporter=junit",
                f"--outputFile={report}",
            ]
        elif "jest" in dependencies and "jest-junit" in dependencies:
            command = [
                *execute,
                "jest",
                "--runInBand",
                *names,
                "--reporters=jest-junit",
            ]
            # A standard reporter option, scoped to this probe process.
            os.environ["JEST_JUNIT_OUTPUT_FILE"] = str(report)
        else:
            return "unknown"
    elif (package / "go.mod").is_file() and all(name.endswith(".go") for name in names):
        setup = ["go", "mod", "download"]
        command = [
            "go",
            "test",
            "-json",
            *sorted({"./" + str(Path(name).parent) for name in names}),
        ]
    else:
        return "unknown"
    if run(setup, package, directory):
        return "unknown"
    code = run(command, package, directory)
    if report.is_file():
        xml = ET.parse(report).getroot()
        cases = list(xml.iter("testcase"))
        executed = [case for case in cases if case.find("skipped") is None]
        if not executed or list(xml.iter("error")):
            return "unknown"
        if list(xml.iter("failure")) and code != 0:
            return "fail"
        return "pass" if code == 0 else "unknown"
    if command[:2] == ["go", "test"]:
        events = []
        for line in (directory / "output.log").read_text(errors="replace").splitlines():
            if line.startswith("{"):
                events.append(json.loads(line))
        tests_ran = [event for event in events if event.get("Test")]
        if any(event.get("Action") == "fail" for event in tests_ran):
            return "fail"
        if tests_ran and code == 0:
            return "pass"
    return "unknown"


def remove(tree, directory):
    """Take the probe's worktree out of the repository it was made in. Runs
    in a `finally`: failing here must not take the verdict with it."""
    try:
        if (tree / ".git").exists():
            common = subprocess.check_output(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=tree,
                text=True,
            ).strip()
            run(["git", "worktree", "remove", "--force", str(tree)], common, directory)
    except (OSError, subprocess.CalledProcessError) as error:
        (directory / "cleanup-error.txt").write_text(str(error))
    shutil.rmtree(tree, ignore_errors=True)


def main():
    sha, tests, nonce = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
    directory = Path.home() / ".cheese" / "culprit-probes" / nonce
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "input.json").write_text(json.dumps({"sha": sha, "tests": tests}))
    tree = directory / "tree"
    verdict = "unknown"
    try:
        if run(["cheese", "revision-tree", sha, str(tree)], directory, directory):
            # i18n-exempt: the probe's own log on the machine; the platform reads only its verdict
            raise RuntimeError("The probed commit could not be checked out")
        verdict = probe(tree, tests, directory)
    except Exception as error:
        (directory / "error.txt").write_text(str(error))
    finally:
        # The worktree and installed dependencies are disposable; inputs and
        # logs remain.
        remove(tree, directory)
    (directory / "verdict").write_text(verdict)
    return {"pass": 0, "fail": 1, "unknown": 2}[verdict]


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        code = 2
    sys.exit(code)
