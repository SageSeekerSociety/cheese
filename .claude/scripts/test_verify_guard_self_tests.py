#!/usr/bin/env python3
"""End-to-end test of verify-guard-self-tests.py against constructed guards.

The verifier's whole claim is that it can tell a self-test that bites from one
that only prints PASS. This drives its CLI over a throwaway repository holding
guards of each kind — a biting guard, a fake-green guard, a guard with no
self-test, a guard whose self-test never went green, one that marks success the
way unittest does, and one that hands its self-test off to a sibling file — and
requires the verdict, the reported reason and the exit code to tell them apart.
The biting guards are the other half of the test: the verifier must NOT flag
them, or it is just red on everything.
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
import sys
from pathlib import Path


def violations_in(root):
    problems = []
    for path in sorted(Path(root).rglob("*.bad")):
        problems.append(path.name)
    return problems


def main(argv):
    if "--self-test" in argv:
        return self_test()
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    return 1 if problems else 0


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        if violations_in(root) != ["x.bad"]:
            print("SELF-TEST FAIL: 没有报出 .bad", file=sys.stderr)
            return 1
    print("PASS: 「.bad」被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

FAKE_GREEN = '''#!/usr/bin/env python3
import sys
from pathlib import Path


def violations_in(root):
    problems = []
    for path in sorted(Path(root).rglob("*.bad")):
        problems.append(path.name)
    return problems


def main(argv):
    if "--self-test" in argv:
        print("PASS: 什么都没查")
        return 0
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


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

#: The same biting guard, marking success the way unittest does (a bare `OK`).
OK_LINE = BITING.replace('print("PASS: 「.bad」被报出")', 'print("OK")')

#: A guard whose `--self-test` runs a sibling test file in another process, so a
#: silenced copy of the guard is not the file the test judges.
DELEGATING = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    problems = []
    for path in sorted(Path(root).rglob("*.bad")):
        problems.append(path.name)
    return problems


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


def _build(root: Path, guards: dict[str, str]) -> Path:
    scripts = root / ".claude" / "scripts"
    scripts.mkdir(parents=True)
    config = ["repos:", "  - repo: local", "    hooks:"]
    for name, source in guards.items():
        (scripts / name).write_text(source, encoding="utf-8")
        config += [
            f"      - id: {name[:-3]}",
            f"        entry: python3 .claude/scripts/{name}",
            "        language: system",
        ]
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
    def test_a_biting_guard_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _build(root, {"biting.py": BITING})
            result = _run(root, config, "--json")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            guard = json.loads(result.stdout)["guards"][0]
            self.assertEqual(guard["status"], "ok", guard)
            self.assertIn("静音后自测", guard["detail"])

    def test_a_guard_that_marks_success_with_OK_is_green(self) -> None:
        """unittest prints `OK`, not `PASS`; that is still a green self-test."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _build(root, {"okline.py": OK_LINE})
            result = _run(root, config, "--json")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            guard = json.loads(result.stdout)["guards"][0]
            self.assertEqual(guard["status"], "ok", guard)

    def test_a_guard_that_hands_its_self_test_off_is_not_called_a_bite(self) -> None:
        """The copy is not the file such a test reads, so neither verdict is earned."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _build(root, {"delegating.py": DELEGATING})
            (root / ".claude" / "scripts" / "test_delegating.py").write_text(
                DELEGATING_SIBLING, encoding="utf-8"
            )
            result = _run(root, config, "--json")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            guard = json.loads(result.stdout)["guards"][0]
            self.assertEqual(guard["status"], "unverified", guard)
            self.assertIn("测试文件", guard["detail"])

    def test_a_fake_green_self_test_is_caught(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _build(root, {"fake.py": FAKE_GREEN})
            result = _run(root, config, "--json")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            guard = json.loads(result.stdout)["guards"][0]
            self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_guard_without_a_self_test_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _build(root, {"bare.py": NO_SELF_TEST})
            result = _run(root, config, "--json")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["guards"][0]["status"], "missing-self-test")

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
