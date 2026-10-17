#!/usr/bin/env python3
"""End-to-end test of verify-guard-self-tests.py against constructed guards.

The verifier's whole claim is that it can tell a self-test that bites from one
that only prints PASS. This drives its CLI over a throwaway repository holding
guards of each kind — a biting guard, a fake-green guard, a guard whose self-test
only ever checks a clean tree, a guard with no self-test, a guard whose self-test
never went green, one that marks success the way unittest does, one that hands
its self-test off to a sibling file, one whose failure comes back in a tuple, one
that names its exit code, and a shell guard — and requires the verdict, the
reported reason and the exit code to tell them apart.

The biting guards are the other half of the test: the verifier must NOT flag
them, or it is just red on everything. Each biting fixture reports through a
different shape (a conditional return, a tuple, a named constant, a shell
`return 1`), because each shape is a separate branch of the mutation engine.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("verify-guard-self-tests.py")

BITING = '''#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        return self_test()
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    return 0 if not problems else 1


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)], capture_output=True, text=True
        )
        if done.returncode != 1:
            print("SELF-TEST FAIL: 没有以 1 报出 .bad", file=sys.stderr)
            return 1
    print("PASS: 「.bad」被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The same biting guard, except the failure travels back in a tuple.
TUPLE_RETURN = '''#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def report(root):
    problems = violations_in(root)
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    if problems:
        return 1, problems
    return 0, []


def main(argv):
    if "--self-test" in argv:
        return self_test()
    code, _ = report(Path(argv[1]) if len(argv) > 1 else Path("."))
    return code


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)], capture_output=True, text=True
        )
        if done.returncode != 1:
            print("SELF-TEST FAIL: 没有以 1 报出 .bad", file=sys.stderr)
            return 1
    print("PASS: 「.bad」被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The same biting guard, except the code is a module-level name.
NAMED_EXIT = '''#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_FOUND = 1


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def report(root):
    problems = violations_in(root)
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    if problems:
        raise SystemExit(EXIT_FOUND)
    sys.exit(0)


def main(argv):
    if "--self-test" in argv:
        return self_test()
    report(Path(argv[1]) if len(argv) > 1 else Path("."))
    return 0


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)], capture_output=True, text=True
        )
        if done.returncode != 1:
            print("SELF-TEST FAIL: 没有以 1 报出 .bad", file=sys.stderr)
            return 1
    print("PASS: 「.bad」被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: A biting guard whose only failure signal is a bare `return 1`.
PLAIN_RETURN = '''#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def report(root):
    problems = violations_in(root)
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    if problems:
        return 1
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return report(Path(argv[1]) if len(argv) > 1 else Path("."))


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)], capture_output=True, text=True
        )
        if done.returncode != 1:
            print("SELF-TEST FAIL: 没有以 1 报出 .bad", file=sys.stderr)
            return 1
    print("PASS: 「.bad」被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The same, with the code named at module level but returned, not raised.
NAMED_RETURN = (
    PLAIN_RETURN.replace("    if problems:\n        return 1", "    if problems:\n        return EXIT_FOUND")
    .replace("from pathlib import Path", "from pathlib import Path\n\nEXIT_FOUND = 1")
)

#: The same biting guard, passing a `.py` fixture name as an argument to itself.
FOREIGN_ARG = BITING.replace(
    '[sys.executable, str(Path(__file__).resolve()), str(root)]',
    '[sys.executable, str(Path(__file__).resolve()), str(root), "fixture.py"]',
)

#: An inline self-test that runs a different `.py` file: the copy is not judged.
FOREIGN_SCRIPT = BITING.replace(
    '[sys.executable, str(Path(__file__).resolve()), str(root)]',
    '[sys.executable, str(Path(__file__).with_name("test_biting.py")), str(root)]',
)

#: Stands in for the file `FOREIGN_SCRIPT` runs: it exits the way the guard's
#: self-test expects, and nothing this tool writes can reach it.
FOREIGN_SCRIPT_SIBLING = '''#!/usr/bin/env python3
raise SystemExit(1)
'''

#: A shell guard: its rule lives in `scan`, and the self-test is below it.
BITING_SHELL = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  local root="$1" bad
  bad="$(find "$root" -name '*.bad' -print 2>/dev/null || true)"
  [ -z "$bad" ] && return 0
  echo "$bad: 修法：删掉它" >&2
  return 1
}

if [ "$SELF_TEST" = 1 ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  scan "$tmp" && self_fail "一个 .bad 文件必须报错"
  echo "PASS: 「.bad」被报出"
  exit 0
fi

scan "${1:-.}"
'''

#: A shell guard whose rules sit *after* its self-test block, which re-runs the
#: script in a child (`bash "$0"`). There is no function before the block, so
#: nothing can be silenced soundly and the tool declines instead of guessing.
PASSES_LATE_SHELL = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  if bash "$0" "$tmp" >/dev/null 2>&1; then
    echo "SELF-TEST FAIL: 一个 .bad 文件必须报错" >&2
    exit 1
  fi
  echo "PASS: 「.bad」被报出"
  exit 0
fi

scan() {
  local root="$1" bad
  bad="$(find "$root" -name '*.bad' -print 2>/dev/null || true)"
  [ -z "$bad" ] && return 0
  echo "$bad: 修法：删掉它" >&2
  return 1
}

scan "${1:-.}"
'''

FAKE_GREEN = '''#!/usr/bin/env python3
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        print("PASS: 什么都没查")
        return 0
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: A guard whose self-test only ever checks that a clean scan comes back — the
#: shape a broad "null the collector" mutation used to bless with a false green.
CLEAN_ONLY = '''#!/usr/bin/env python3
import sys
from pathlib import Path


def scan(root):
    found = []
    for path in sorted(Path(root).rglob("*.md")):
        found.append(path)
    return found


def judge(root):
    found = scan(root)
    if not found:
        print("什么都没扫到 — 这本身就是失败", file=sys.stderr)
        return 1
    bad = [path.name for path in found if "bad" in path.name]
    return 1 if bad else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return judge(Path(argv[1]) if len(argv) > 1 else Path("."))


def self_test():
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "ok.md").write_text("", encoding="utf-8")
        found = [path.name for path in scan(root)]
        if found != ["ok.md"]:
            print(f"SELF-TEST FAIL: 干净夹具里该扫到 ok.md，得到 {found}", file=sys.stderr)
            return 1
    print("PASS: 只查了干净夹具")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

NO_SELF_TEST = '''#!/usr/bin/env python3
import sys


def main():
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

#: A guard with no inline self-test whose check sits in a sibling test file.
SIBLING_ONLY = '''#!/usr/bin/env python3
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

SIBLING_TEST = '''#!/usr/bin/env python3
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("sibling", Path(__file__).with_name("sibling.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SiblingTest(unittest.TestCase):
    def test_reports_a_bad_file(self):
        with tempfile.TemporaryDirectory() as raw:
            Path(raw, "x.bad").write_text("", encoding="utf-8")
            self.assertEqual(module.violations_in(Path(raw)), ["x.bad"])


if __name__ == "__main__":
    unittest.main()
'''

NOT_GREEN = '''#!/usr/bin/env python3
import sys


def main(argv):
    if "--self-test" in argv:
        print("SELF-TEST FAIL: 这个自测从来没绿过", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The same biting guard, marking success the way unittest does (a bare `OK`).
OK_LINE = BITING.replace('print("PASS: 「.bad」被报出")', 'print("OK")')

#: A guard whose `--self-test` runs a sibling test file in another process, so a
#: silenced copy of the guard is not the file the test judges.
DELEGATING = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        return subprocess.call([sys.executable, str(Path(__file__).with_name("test_delegating.py"))])
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

DELEGATING_SIBLING = '''#!/usr/bin/env python3
import importlib.util
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "delegating", Path(__file__).with_name("delegating.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    with tempfile.TemporaryDirectory() as raw:
        (Path(raw) / "x.bad").write_text("", encoding="utf-8")
        if module.violations_in(Path(raw)) != ["x.bad"]:
            print("SELF-TEST FAIL: 没有报出 .bad", file=sys.stderr)
            return 1
    print("PASS: 旁置测试文件跑通了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _build(root: Path, guards: dict[str, str], extra: dict[str, str] | None = None) -> Path:
    scripts = root / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    config = ["repos:", "  - repo: local", "    hooks:"]
    for name, source in guards.items():
        (scripts / name).write_text(source, encoding="utf-8")
        interpreter = "python3" if name.endswith(".py") else "bash"
        config += [
            f"      - id: {Path(name).stem}",
            f"        entry: {interpreter} .claude/scripts/{name}",
            "        language: system",
        ]
    for name, source in (extra or {}).items():
        (scripts / name).write_text(source, encoding="utf-8")
    config_path = root / ".pre-commit-config.yaml"
    config_path.write_text("\n".join(config) + "\n", encoding="utf-8")
    return config_path


def _run(root: Path, config: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--config", str(config), *extra],
        check=False,
        capture_output=True,
        text=True,
    )


class VerifyGuardSelfTestsTest(unittest.TestCase):
    def _only(self, guards: dict[str, str], extra: dict[str, str] | None = None) -> tuple[int, dict, str]:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _build(root, guards, extra)
            result = _run(root, config, "--json")
        return result.returncode, json.loads(result.stdout)["guards"][0], result.stdout + result.stderr

    def test_a_biting_guard_is_not_flagged(self) -> None:
        code, guard, output = self._only({"biting.py": BITING})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)
        self.assertIn("静音后自测", guard["detail"])

    def test_a_guard_whose_failure_comes_back_in_a_tuple_is_not_flagged(self) -> None:
        code, guard, output = self._only({"tuple.py": TUPLE_RETURN})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_guard_that_names_its_exit_code_is_not_flagged(self) -> None:
        code, guard, output = self._only({"named.py": NAMED_EXIT})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_guard_whose_only_signal_is_a_bare_return_is_not_flagged(self) -> None:
        """The plain `return 1` shape is what most guards use; it must be silenced."""
        code, guard, output = self._only({"plain.py": PLAIN_RETURN})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_guard_that_returns_a_named_code_is_not_flagged(self) -> None:
        """`return EXIT_FOUND` is the same signal as `return 1`, and must be reached."""
        code, guard, output = self._only({"namedreturn.py": NAMED_RETURN})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_py_argument_to_a_self_rerun_does_not_make_the_guard_foreign(self) -> None:
        """A fixture path after the program is a command-line argument, not a script."""
        code, guard, output = self._only({"fixturearg.py": FOREIGN_ARG})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_an_inline_self_test_that_runs_another_py_file_is_unverified(self) -> None:
        """Only `spawns_foreign_file` catches this one: no `if` mentions self-test."""
        code, guard, output = self._only(
            {"foreignscript.py": FOREIGN_SCRIPT}, {"test_biting.py": FOREIGN_SCRIPT_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_shell_guard_is_judged_by_its_rule_function(self) -> None:
        code, guard, output = self._only({"biting.sh": BITING_SHELL})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_silencing_stops_at_the_shell_self_test_block(self) -> None:
        """A rule defined inside the block must keep firing, or its red is lost."""
        code, guard, output = self._only({"lateshell.sh": PASSES_LATE_SHELL})
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("自测块之前没有可以静音的函数", guard["detail"])

    def test_a_guard_that_marks_success_with_OK_is_green(self) -> None:
        """unittest prints `OK`, not `PASS`; that is still a green self-test."""
        code, guard, output = self._only({"okline.py": OK_LINE})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_guard_that_hands_its_self_test_off_is_not_called_a_bite(self) -> None:
        """The copy is not the file such a test reads, so neither verdict is earned."""
        code, guard, output = self._only({"delegating.py": DELEGATING}, {"test_delegating.py": DELEGATING_SIBLING})
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_guard_with_a_green_sibling_test_is_unverified(self) -> None:
        """A sibling file is not mutated, so a bite there would prove nothing."""
        code, guard, output = self._only({"sibling.py": SIBLING_ONLY}, {"test_sibling.py": SIBLING_TEST})
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("旁边的测试文件", guard["detail"])

    def test_a_fake_green_self_test_is_caught(self) -> None:
        code, guard, output = self._only({"fake.py": FAKE_GREEN})
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_self_test_that_only_checks_a_clean_tree_is_caught(self) -> None:
        """The blocker: nulling the collector used to turn this green into a false bite.

        Silencing the scan broke a clean fixture, so the self-test went red for a
        reason that had nothing to do with the guard's failure signal. Silencing
        only the signal leaves this self-test green — which is the truth about it.
        """
        code, guard, output = self._only({"cleanonly.py": CLEAN_ONLY})
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_guard_whose_self_test_never_went_green_is_reported(self) -> None:
        code, guard, output = self._only({"notgreen.py": NOT_GREEN})
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-not-green", guard)
        self.assertIn("从来没绿过", guard["detail"])

    def test_a_guard_without_a_self_test_is_reported(self) -> None:
        code, guard, output = self._only({"bare.py": NO_SELF_TEST})
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "missing-self-test", guard)

    def test_every_status_carries_a_repair_hint_on_the_human_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _build(root, {"biting.py": BITING, "fake.py": FAKE_GREEN, "bare.py": NO_SELF_TEST})
            result = _run(root, config)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        # The biting guard stays clean; the other two are the findings.
        self.assertIn("OK", result.stdout)
        self.assertIn("修法：", result.stdout)
        self.assertIn("守卫", result.stdout)

    def test_the_verifier_reproduces_its_own_self_test(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--self-test"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
