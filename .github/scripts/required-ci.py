"""Select merge-diff suites and fail the required check unless all pass."""

import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys

PATHS = json.loads(Path(__file__).with_name("required-ci-paths.json").read_text())


def select(paths):
    # Changes to the gate itself must exercise every callable suite.
    gate_changed = any(
        path == ".github/workflows/required-ci.yml"
        or path.startswith(".github/scripts/required-ci")
        or path == ".github/scripts/test_required_ci.py"
        for path in paths
    )
    return {
        suite: gate_changed
        or suite == "guards"
        or any(fnmatch.fnmatchcase(path, pattern) for path in paths for pattern in patterns)
        for suite, patterns in PATHS.items()
    }


def failures(needs):
    scope = needs.get("scope", {})
    if scope.get("result") != "success":
        return ["scope did not succeed"]
    errors = []
    for suite in PATHS:
        selected = scope.get("outputs", {}).get(suite)
        if selected not in ("true", "false"):
            errors.append(f"{suite}: missing or invalid scope output")
            continue
        expected = "success" if selected == "true" else "skipped"
        actual = needs.get(suite, {}).get("result", "missing")
        if actual != expected:
            errors.append(f"{suite}: expected {expected}, got {actual}")
    return errors


def main():
    if sys.argv[1] == "scope":
        # No rename detection: moving code must test both the old and new area.
        changed = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", sys.argv[2], "HEAD"]
        ).decode().split("\0")
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for suite, selected in select(changed).items():
                line = f"{suite}={str(selected).lower()}"
                print(line)
                print(line, file=output)
    elif sys.argv[1] == "check":
        errors = failures(json.loads(os.environ["CI_NEEDS"]))
        if errors:
            raise SystemExit("\n".join(errors))
        print("Every selected suite passed.")
    else:
        raise SystemExit("Usage: required-ci.py scope BASE_SHA | check")


if __name__ == "__main__":
    main()
