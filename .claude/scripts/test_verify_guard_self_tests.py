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
#: script in a child (`bash "$0"`). The verdict is still a literal `return 1`
#: outside the block, so it can be silenced soundly — and the child re-run
#: notices, because the child runs the silenced copy as well.
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

#: A guard whose verdict is `raise SystemExit(report(root))`, with its self-test
#: asserting only the line `report` prints. Rewriting that argument to `0`
#: deletes the call and the print with it, so the self-test goes red for a reason
#: that has nothing to do with the verdict.
SIDE_EFFECT_EXIT = '''#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path


def report(root):
    problems = [path.name for path in sorted(Path(root).rglob("*.bad"))]
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    raise SystemExit(report(Path(argv[1]) if len(argv) > 1 else Path(".")))


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if "修法" not in done.stderr:
            print("SELF-TEST FAIL: 报告里要说出 x.bad", file=sys.stderr)
            return 1
    print("PASS: 报告说出了 x.bad")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The same shape, but its self-test is about the exit code — discarding the
#: call's result must still turn it red.
CALL_EXIT_BITING = '''#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path


def report(root):
    problems = [path.name for path in sorted(Path(root).rglob("*.bad"))]
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    raise SystemExit(report(Path(argv[1]) if len(argv) > 1 else Path(".")))


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1:
            print(f"SELF-TEST FAIL: 一个 .bad 文件必须以 1 报错，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: .bad 文件以 1 报错")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: A shell guard whose verdict is a variable (`exit "$CODE"`). Rewriting it means
#: knowing what every assignment path holds, which this tool does not attempt.
NAMED_SHELL_VERDICT = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

CODE=0

scan() {
  local root="$1" bad
  bad="$(find "$root" -name '*.bad' -print 2>/dev/null || true)"
  [ -z "$bad" ] && return 0
  echo "$bad: 修法：删掉它" >&2
  CODE=1
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  scan "$tmp"
  [ "$CODE" = 1 ] || self_fail "一个 .bad 文件必须报错"
  echo "PASS: 「.bad」被报出"
  exit 0
fi

scan "${1:-.}"
exit "$CODE"
'''

#: A shell guard whose self-test only ever asserts the clean path and the record
#: it emits. Silencing the rule must leave both intact — if it does not, the red
#: comes from the record vanishing, not from the missing verdict.
CLEAN_ONLY_SHELL = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

FAILED=0

counts() {
  find "$1" -name '*.bad' -print 2>/dev/null | grep -c . || true
}

record() {
  printf '{"files": %s}\\n' "$(counts "$1")"
}

scan() {
  local n
  n="$(counts "$1")"
  if [ "$n" -gt 0 ]; then
    echo "$n 个 .bad 文件：修法：删掉它" >&2
    FAILED=1
  fi
}

if [ "$SELF_TEST" = 1 ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  scan "$tmp"
  [ "$FAILED" = 0 ] || self_fail "干净树必须通过"
  out="$(record "$tmp")"
  case "$out" in
    *'"files": 0'*) ;;
    *) self_fail "记录里要有文件数（得到 $out）" ;;
  esac
  echo "PASS: 干净树通过，记录成立"
  exit 0
fi

scan "${1:-.}"
record "${1:-.}"
[ "$FAILED" = 1 ] && exit 1
exit 0
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

#: A guard whose helper answers a yes/no question with 1 for *good*. Silencing
#: that `return 1` inverts the helper and breaks the clean path, so a red would
#: be the mutation's doing, not the self-test's.
PY_PREDICATE = '''#!/usr/bin/env python3
import sys
from pathlib import Path

GOOD = "ok"


def is_good(line):
    return 1 if line.strip() == GOOD else 0


def scan(root):
    bad = []
    for path in sorted(Path(root).rglob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not is_good(line):
                bad.append(f"{path.name}: {line}")
    return bad


def judge(root):
    bad = scan(root)
    for item in bad:
        print(f"{item}: 修法：每行都写成 {GOOD}", file=sys.stderr)
    return 1 if bad else 0


def self_test():
    import tempfile
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "a.txt").write_text("ok\\n", encoding="utf-8")
        if scan(root) != []:
            print("SELF-TEST FAIL: 干净夹具被报成违规", file=sys.stderr)
            return 1
    print("PASS: 干净夹具")
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return judge(Path(argv[1]) if len(argv) > 1 else Path("."))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The shell twin: `has_bad` returns 1 when nothing is wrong, exactly the
#: convention `grep -q` uses. Rewriting it inverts the rule instead of
#: silencing it.
SHELL_PREDICATE = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

has_bad() {
  find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q . && return 0
  return 1
}

scan() {
  if has_bad "$1"; then
    echo "找到 .bad 文件；修法：删掉它" >&2
    exit 1
  fi
  echo "clean"
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  out="$(scan "$tmp" 2>&1)"
  [ "$out" = "clean" ] || { echo "SELF-TEST FAIL: 干净扫描输出 $out" >&2; exit 1; }
  echo "PASS: 干净扫描"
  exit 0
fi

scan "${1:-.}"
'''

#: A guard that prints its verdict as part of a record. `exit 1` written into
#: that text is output, not an exit status; rewriting it changes what the guard
#: says and makes the record assertion fail for the wrong reason.
SHELL_RECORD_TEXT = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

count_bad() { find "$1" -name '*.bad' -type f 2>/dev/null | wc -l | tr -d ' '; }

record() {
  local n; n="$(count_bad "$1")"
  printf '{"bad": %s, "hint": "删掉它们；exit 1 表示违规"}\\n' "$n"
}

scan() {
  local n; n="$(count_bad "$1")"
  record "$1"
  if [ "$n" -gt 0 ]; then
    echo "$n 个违规；修法：删掉它们" >&2
    return 1
  fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  scan "$tmp" || { echo "SELF-TEST FAIL: 干净夹具被拒" >&2; exit 1; }
  rec="$(record "$tmp")"
  want='{"bad": 0, "hint": "删掉它们；exit 1 表示违规"}'
  [ "$rec" = "$want" ] || { echo "SELF-TEST FAIL: 记录是 $rec" >&2; exit 1; }
  echo "PASS: 干净扫描与记录"
  exit 0
fi

scan "${1:-.}"
'''

#: The same record, written through a heredoc instead of `printf`. The heredoc
#: body is data too, and its `exit 1` must survive the mutation untouched.
SHELL_HEREDOC_RECORD = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

count_bad() { find "$1" -name '*.bad' -type f 2>/dev/null | wc -l | tr -d ' '; }

record() {
  local n; n="$(count_bad "$1")"
  cat <<EOF
{"bad": $n}
{"hint": "违规会让钩子 exit 1"}
EOF
}

scan() {
  local n; n="$(count_bad "$1")"
  record "$1"
  if [ "$n" -gt 0 ]; then echo "$n 个违规；修法：删掉" >&2; return 1; fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  scan "$tmp" || { echo "SELF-TEST FAIL: 干净夹具被拒" >&2; exit 1; }
  rec="$(record "$tmp")"
  want='{"bad": 0}
{"hint": "违规会让钩子 exit 1"}'
  [ "$rec" = "$want" ] || { echo "SELF-TEST FAIL: 记录是 $rec" >&2; exit 1; }
  echo "PASS: 干净扫描与记录"
  exit 0
fi

scan "${1:-.}"
'''

#: The self-test writes a sample containing a `fi` at column 0. That `fi` is
#: data, not the end of the self-test block; treating it as the end hides the
#: self-test's own `exit 1` and turns a biting guard into an unverified one.
SHELL_HEREDOC_FI = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

has_bad() { find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q .; }

scan() {
  if has_bad "$1"; then
    echo "找到 .bad 文件；修法：删掉它" >&2
    return 1
  fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  cat > "$tmp/sample.sh" <<'EOF'
if [ -n "$x" ]; then
  echo ok
fi
EOF
  scan "$tmp" >/dev/null 2>&1
  [ $? -ne 0 ] || { echo "SELF-TEST FAIL: .bad 文件必须被报出" >&2; exit 1; }
  echo "PASS: .bad 文件被报出"
  exit 0
fi

scan "${1:-.}"
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


#: The same hand-off, with the command built into a variable first. Reading
#: only the call site sees a bare name, decides the guard re-runs itself, and
#: reports a bite that never happened against a sibling test it never ran.
DELEGATING_VARIABLE = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def self_test():
    command = [sys.executable, str(Path(__file__).with_name("test_handoff.py"))]
    code = subprocess.call(command)
    if code != 0:
        print("SELF-TEST FAIL: 旁置测试失败", file=sys.stderr)
        return 1
    print("PASS: 旁置测试跑通了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

DELEGATING_VARIABLE_SIBLING = '''#!/usr/bin/env python3
import unittest


class HandoffTest(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
'''


#: The hint is printed without quotes, so its `exit 1` is an argument to `echo`.
#: Rewriting it edits the guard's output; the self-test only looks at that text
#: and never at the exit status, so the edit alone makes it go red.
SHELL_UNQUOTED_HINT = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  if find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q .; then
    echo 修法：让本守卫 exit 1 >&2
    return 1
  fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  out="$(scan "$tmp" 2>&1)"
  case "$out" in
    *"exit 1"*) ;;
    *) echo "SELF-TEST FAIL: 提示里要写出 exit 1" >&2; exit 1 ;;
  esac
  echo "PASS: 提示写出了 exit 1"
  exit 0
fi

scan "${1:-.}"
'''


#: `run` carries the status but ends on an `if`, so the command that answers is
#: `scan`, one level in. Only `run`'s error path is silenced, and the bite the
#: self-test shows comes from `scan`'s untouched `return 1`.
SHELL_TRAILING_IF = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q . || return 0
  echo "找到 .bad 文件；修法：删掉它" >&2
  return 1
}

run() {
  if [ -e "$1" ]; then
    if [ -n "$1" ]; then
      scan "$1"
    fi
  fi
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  if run "$tmp"; then
    echo "SELF-TEST FAIL: .bad 文件必须让 run 返回非 0" >&2
    exit 1
  fi
  echo "PASS: .bad 文件被报出"
  exit 0
fi

run "${1:-.}"
'''


#: `<<EOF` written inside single quotes is a pattern to search for, not a
#: heredoc opener. Reading it as one marks the rest of the file as data, hides
#: every verdict, and downgrades a biting guard to unverified.
SHELL_QUOTED_HEREDOC_OP = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  if grep -rn '<<EOF' "$1" >/dev/null 2>&1; then
    echo "找到 heredoc 用法；修法：改用 printf" >&2
    return 1
  fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  printf '%s\\n' 'cat <<EOF' > "$tmp/a.sh"
  scan "$tmp" && { echo "SELF-TEST FAIL: heredoc 用法必须被报出" >&2; exit 1; }
  echo "PASS: heredoc 用法被报出"
  exit 0
fi

scan "${1:-.}"
'''


#: `self_fail` is defined outside the self-test block, the way many guards do
#: it. It belongs to the harness: silencing its `exit 1` turns the self-test's
#: own failure into a silent success and a biting guard into "unverified".
SHELL_SELF_FAIL_OUTSIDE = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

self_fail() {
  echo "SELF-TEST FAIL: $*" >&2
  exit 1
}

scan() {
  find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q . || return 0
  echo "找到 .bad；修法：删掉" >&2
  return 1
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  scan "$tmp" && self_fail "一个 .bad 文件必须报错"
  echo "PASS: .bad 被报出"
  exit 0
fi

scan "${1:-.}"
'''


#: The verdict sits right after `then`. Reading a word as a command only at the
#: start of the line or after `;|&({` misses it, leaves `exit 1` untouched, and
#: downgrades a guard that really bites to "unverified".
SHELL_CONTROL_WORD_VERDICT = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  echo "找到 .bad；修法：删掉" >&2
  if find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q .; then return 1; fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  scan "$tmp" && { echo "SELF-TEST FAIL: .bad 必须被报出" >&2; exit 1; }
  echo "PASS: .bad 被报出"
  exit 0
fi

scan "${1:-.}"
'''


#: `dead` answers 1, but the call sits after `return 0` and never runs. Following
#: calls past the flow end puts it on the verdict path, so a guard whose only
#: non-zero signal is dead code is reported as "does not bite" instead of
#: "unverified".
SHELL_DEAD_CODE_AFTER_RETURN = '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

dead() {
  return 1
}

scan() {
  echo clean
  return 0
  dead "$1"
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  scan "$tmp"
  echo "PASS: 干净目录不报错"
  exit 0
fi

scan "${1:-.}"
'''


#: The `--self-test` branch hands the work to `check`, whose name says nothing
#: about self-testing. Reading only the branch body sees no spawn, so a copy
#: that never reaches `test_wrapper.py` is reported as a bite that did not happen.
PY_DELEGATES_THROUGH_WRAPPER = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_wrapper.py"))]
    )


def main(argv):
    if "--self-test" in argv:
        return check()
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

WRAPPER_SIBLING = '''#!/usr/bin/env python3
print("PASS: 旁置测试跑通了")
'''


#: The guard dispatches the other way: `if "--self-test" not in argv:` is the
#: *scan* path, and its helper spawn is the scan's own business. Reading that
#: body as the self-test side turns a biting guard into "unverified".
PY_NEGATIVE_BRANCH_SCAN = '''#!/usr/bin/env python3
"""The scan path shells out to helper.py; the self-test re-runs this file."""
import subprocess
import sys
from pathlib import Path


def scan(argv):
    subprocess.run([sys.executable, str(Path(__file__).with_name("helper.py"))], check=False)
    problems = [path.name for path in sorted(Path(argv[1] if len(argv) > 1 else ".").rglob("*.bad"))]
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if "--self-test" not in argv:
        return scan(argv)
    return self_test()


def self_test():
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1:
            print("SELF-TEST FAIL: 没有以 1 报出 .bad", file=sys.stderr)
            return 1
    print("PASS: .bad 被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''


#: The same dispatch, with the hand-off after the branch instead of inside an
#: `else`. The self-test still lives in `check`, so the copy cannot judge it.
PY_ELSE_DELEGATION = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(argv):
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_else.py"))]
    )


def main(argv):
    if "--self-test" not in argv:
        return scan(argv)
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

ELSE_SIBLING = '''#!/usr/bin/env python3
print("PASS: 旁置测试跑通了")
'''

#: The same hand-off, but the negative branch is an `elif`. The statement that
#: follows it belongs to the outer block, not to the branch's own block.
PY_NESTED_ELSE_DELEGATION = PY_ELSE_DELEGATION.replace(
    '''    if "--self-test" not in argv:
        return scan(argv)
    return check()''',
    '''    if len(argv) > 99:
        return 3
    elif "--self-test" not in argv:
        return scan(argv)
    return check()''',
)

#: The flag is counted, not tested for membership. Zero means it is absent, so
#: this branch is the scan; reading it as the self-test side flags the guard.
PY_COUNT_ZERO_SCAN = PY_NEGATIVE_BRANCH_SCAN.replace(
    'if "--self-test" not in argv:', 'if argv.count("--self-test") == 0:'
)

#: The same count, one instead of zero: the branch is the self-test side now.
#: Reading it as the scan leaves the hand-off out and blesses a green guard.
PY_COUNT_ONE_HANDOFF = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if argv.count("--self-test") == 1:
        return check()
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The self-test returns inside the outer `if`; `helper.py` below it is not on
#: that path. Walking further out would call this guard unjudgeable when it
#: simply prints PASS and exits 0 — a green that must be reported as such.
PY_UNREACHABLE_SPAWN = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(argv):
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def main(argv):
    if len(argv) >= 2:
        if "--self-test" not in argv:
            return scan(argv)
        print("PASS: 什么都没测")
        return 0
    subprocess.run([sys.executable, str(Path(__file__).with_name("helper.py"))], check=False)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The self-test branch sits in a handler that always runs; its path leaves the
#: handler and reaches the hand-off after the `try`. Treating the handler as a
#: dead end drops `check` from the scope and the delegation goes unseen.
PY_EXCEPT_HANDOFF = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(argv):
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_except.py"))]
    )


def main(argv):
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        if "--self-test" not in argv:
            return scan(argv)
        print("running the self-test")
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The count is compared against a module-level `ZERO`, not a literal. Reading
#: the name as an unreadable count sends the scan branch to the self-test side
#: and drops the hand-off after it.
PY_NAMED_COUNT = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

ZERO = 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if argv.count("--self-test") == ZERO:
        return 1 if list(Path(argv[1] if len(argv) > 1 else ".").rglob("*.bad")) else 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The counted branch picks the self-test side and its body falls through, so
#: `check` after the branch runs for `--self-test`. Climbing out only for the
#: scan side drops it.
PY_COUNTED_TAIL = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if argv.count("--self-test") == 1:
        print("flag seen")
    if len(argv) > 2:
        return 1
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The `return 0` sits in a loop body that runs zero times for `--self-test`, so
#: execution continues at the hand-off after the loop.
PY_LOOP_BODY_BRANCH = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    roots = [Path(argument) for argument in argv[1:] if not argument.startswith("-")]
    for root in roots:
        if "--self-test" not in argv:
            return scan(root)
        print("PASS: 什么都没测")
        return 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The `raise` at the end of the `try` body is swallowed by the handler, so the
#: hand-off after the `try` still runs for `--self-test`.
PY_CAUGHT_RAISE = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        raise ValueError("boom")
    except ValueError:
        pass
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: `sys.exit(0)` in the self-test branch ends the process there, so the hand-off
#: after the `try` is dead and this guard's green never tested anything.
PY_SYSTEM_EXIT_HANDOFF = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        print("PASS: 什么都没测")
        sys.exit(0)
    except SystemExit:
        raise
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The same exit, swallowed by a handler that falls through: `check` after the
#: `try` still runs for `--self-test`.
PY_SWALLOWED_EXIT = PY_SYSTEM_EXIT_HANDOFF.replace(
    "    except SystemExit:\n        raise", "    except SystemExit:\n        pass"
)

#: The same exit, written as `raise SystemExit(0)`, carried out by the handler.
PY_RAISED_EXIT = PY_SYSTEM_EXIT_HANDOFF.replace(
    "        sys.exit(0)", "        raise SystemExit(0)"
)

#: The self-test branch sits under a condition that reads the environment, so
#: the `return 0` in it is not provably the end of the path.
PY_UNREACHED_SELF_TEST_BRANCH = '''#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    root = os.environ.get("GUARD_SCAN_ROOT")
    if root:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return 0
        return scan(Path(root))
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The self-test branch sits in a `with` body, which provably runs, so its
#: `return 0` leaves the `check` below the `with` dead.
PY_WITH_BRANCH = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    with open(__file__, encoding="utf-8") as stream:
        stream.read(1)
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return 0
        return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The self-test branch sits in a `match` case that falls out of the `match`,
#: so the `check` after it still runs.
PY_MATCH_CASE_HANDOFF = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    match argv[1:]:
        case [first]:
            print(f"scanning {first}")
            if "--self-test" not in argv:
                return 1 if violations_in(first) else 0
            print("PASS: 什么都没测")
        case _:
            print("no root")
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The zero is module-level but annotated: `ZERO: int = 0`.
PY_ANNOTATED_ZERO = PY_NAMED_COUNT.replace("ZERO = 0", "ZERO: int = 0")

#: The self-test branch sits in a `finally`, which always runs, so its
#: `return 0` leaves the `check` below the `try` dead.
PY_FINALLY_BRANCH = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        pass
    finally:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return 0
        return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The self-test branch sits under `len(argv) > 1`, which settles true for the
#: self-test's own command line.
PY_LENGTH_GUARDED_BRANCH = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) > 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return 0
        return scan(Path(argv[1]))
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The zero is a local in `main`, the function that reads it — not a module
#: constant. Reading only module-level names sends the scan branch to the
#: self-test side and drops the `check` that follows it.
PY_SHADOW_ZERO = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    zero = 0
    if argv.count("--self-test") == zero:
        return 1 if violations_in(argv[1] if len(argv) > 1 else ".") else 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The hand-off is the return expression of the handler that catches the
#: self-test's own exit: `except SystemExit: return check()`.
PY_HANDLER_RETURN_HANDOFF = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        print("PASS: 什么都没测")
        sys.exit(0)
    except SystemExit:
        return check()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The branch side itself ends through `exit(0)` and the enclosing handler
#: returns the hand-off. The handler body is reached from the side, not from
#: what follows the branch.
PY_HANDLER_RETURN_INSIDE = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            exit(0)
        else:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
    except SystemExit:
        return check()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The branch side leaves by `raise SystemExit(0)` into a handler that returns
#: the hand-off — the `raise` spelling of the same exit.
PY_HANDLER_RETURN_RAISED = PY_HANDLER_RETURN_INSIDE.replace(
    "            exit(0)", "            raise SystemExit(0)"
)

#: `1 == LIMIT` is false, so the flag takes the hand-off — but `LIMIT` cannot be
#: read. Guessing the self-test side would drop that hand-off.
PY_NEGATED_COUNT = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

LIMIT = len("--self-test") - 10


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if not (argv.count("--self-test") == LIMIT):
        return 1 if violations_in(argv[1] if len(argv) > 1 else ".") else 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

PY_UNREADABLE_BODY_HANDOFF = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

LIMIT = len("--self-test") - 10


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if argv.count("--self-test") == LIMIT:
        return check()
    return 1 if violations_in(argv[1] if len(argv) > 1 else ".") else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The statements after the self-test branch sit inside a gate the command line
#: proves false. What follows the branch there — the `try` and the handler that
#: hands off to the sibling — must not be read as reachable.
PY_FALSE_GATE_TAIL = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) > 2:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The handler sits under a gate the command line proves false (`guard.py
#: --self-test` has two words), so it never runs. Its hand-off must not be put
#: on the path: doing so would hide the real finding that this self-test never
#: fails.
PY_PROVEN_FALSE_GATE = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) > 2:
        try:
            if "--self-test" in argv:
                sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: `main(sys.argv[1:])` leaves one word, so `len(argv) == 1` holds during the
#: self-test: the exit runs, the handler catches it and runs the sibling. Reading
#: argv as the untrimmed command line calls the gate false and drops that spawn.
PY_TRIMMED_ARGV_HANDOFF = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: The same trimmed argv, a gate that really is false under it: `len(argv) > 1`
#: never holds, so the guard prints PASS without measuring anything. Trimming
#: argv must not excuse this shape as unverifiable.
PY_TRIMMED_ARGV_FALSE_GATE = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) > 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return check()
        return 1 if violations_in(argv[1]) else 0
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: The entry point is handed the full command line, but `main` trims `argv`
#: itself. The call site alone would keep two words and judge the gate false.
PY_REBOUND_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    argv = argv[1:]
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The entry call trims the parameter, but the gate reads the process command
#: line: `sys.argv` keeps both words, so the gate holds and the hand-off runs.
PY_SYS_ARGV_FLAT = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(sys.argv) > 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: A helper rebinds its own `argv`; the entry call hands over everything, so
#: the gate on `main`'s argv really is false and the finding must stand. If the
#: helper's store were read as `main`'s, the argv would go unsettled and the
#: hand-off inside the gate would come back `unverified` instead.
PY_UNRELATED_REBIND = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def helper(items):
    argv = items[1:]
    return argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: `argv` is stored inside a class body in `main`. That scope has its own name
#: table, so `main`'s parameter is untouched and the gate really is false.
PY_SCOPED_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    class Options:
        argv = None
    if len(argv) > 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return check()
        return 1 if violations_in(argv[1]) else 0
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: An annotated assignment rebinds `argv` just as a plain one does; the
#: annotation is not an `ast.Assign` node, so a detector that only knows plain
#: assignments would read the full command line here.
PY_ANNOTATED_REBIND = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    argv: list[str] = argv[1:]
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: `argv` is imported from `sys` at module level and read through the gate,
#: while the entry call trims a differently named parameter. The name holds
#: the process command line here, so the entry call must not settle it.
PY_IMPORTED_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(params):
    if len(argv) > 1:
        if "--self-test" in params:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: The same name bound by a module-level assignment rather than an import.
PY_ASSIGNED_GLOBAL_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

argv = sys.argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(params):
    if len(argv) > 1:
        if "--self-test" in params:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: `main` takes its own parameter named `argv`, which shadows the module-level
#: name, so the entry call still says what the gate reads.
PY_SHADOWED_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: A walrus inside a comprehension binds in the scope around it, so `argv`
#: really is trimmed before the gate reads it.
PY_COMPREHENSION_WALRUS = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    [None for _ in [0] if (argv := sys.argv[1:])]
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: `from sys import *` binds `argv` too, so the bare name is not the parameter
#: the entry call hands over.
PY_STAR_IMPORT = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import *


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(params):
    if len(argv) > 1:
        if "--self-test" in params:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: The gate tests the bare name for truth, and the name is not settled: reading
#: it as false would put a hand-off off the path and invent a finding.
PY_TRUTHY_UNSETTLED_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(params):
    if argv:
        if "--self-test" in params:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: A helper takes a parameter named `argv` of its own. That name holds what
#: the callers pass, not what the entry call handed `main`, so no read of it
#: may be settled from the entry point — even though the self-test here does
#: nothing, the tool cannot say so and leaves the guard `unverified`.
PY_HELPER_OWN_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def normalize(argv):
    return argv[1:]


def main(argv):
    normalize(argv)
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The same shadow in a lambda; a lambda is a scope too, and its parameter is
#: no more the entry value than a helper's.
PY_LAMBDA_OWN_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv

trim = lambda argv: argv[1:]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    trim(argv)
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: A helper without a parameter of its own reads the module binding, which the
#: entry call says nothing about; the name stays unsettled even though `main`
#: shadows it in its own body.
PY_HELPER_READS_GLOBAL = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def report():
    return len(argv)


def main(argv):
    report()
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The gate itself sits behind a helper's own `argv` parameter, and the call
#: passes a list of its own. Settling that gate from the entry call would drop
#: the block that hands off to a sibling test and report a false
#: `self-test-does-not-bite` for a guard whose self-test does fork.
PY_HELPER_GATE_DELEGATES = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def helper(argv):
    if len(argv) > 1:
        if "--self-test" in argv:
            return check()
    return 0


def main(argv):
    run_it = helper
    if "--self-test" in argv:
        run_it(["--self-test", "x"])
        print("PASS: 什么都没测")
        return 0
    return 1 if violations_in(argv[1]) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: Same shape without a module-level `argv` to fall back on: the entry call is
#: the only settled value, so a gate read from the helper's own parameter could
#: only be mistaken for it by ignoring whose parameter it is. `len(argv) == 2`
#: is true of the hand-off list and false of the trimmed entry, so settling the
#: helper's read would prune the hand-off and report a false finding.
PY_HELPER_GATE_UNSETTLED_ENTRY = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def helper(argv):
    if len(argv) == 2:
        if "--self-test" in argv:
            print("PASS: 测过了")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


def main(argv):
    return helper(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

#: A closure inside `main` that takes no `argv` of its own reads main's
#: parameter, so the entry call still settles it.
PY_NESTED_CLOSURE_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    def show():
        return argv

    show()
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: A comprehension's own target is a scope of its own: `for argv in ...` here
#: does not rebind main's parameter.
PY_COMPREHENSION_TARGET = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    [argv for argv in range(3)]
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: `argv` as a keyword-only parameter is still the parameter the entry call
#: fills, and `main(argv=sys.argv)` fills it with the whole command line.
PY_KEYWORD_ONLY_ARGV = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path
from sys import argv


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(*, argv):
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv))
'''

#: `main(*argv)` never holds the command line: `main(sys.argv)` puts it inside a
#: one-element tuple. Settling a gate from that name is judging a value it
#: never receives, and here it prunes the hand-off branch that really runs
#: during a self-test — a false `self-test-does-not-bite` for a guard whose
#: self-test does fork.
PY_MAIN_VARARG = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(*argv):
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 测过了")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: A closure that collects the arguments in `*argv` binds the name itself, so
#: a gate read from it says nothing about the value `main` was handed. The call
#: passes no arguments, so the gate is true at run time and the self-test does
#: fork; settling it from main's parameter would prune that hand-off.
PY_NESTED_VARARG = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    def inner(*argv):
        if len(argv) == 0:
            if "--self-test" in argv:
                print("PASS: 测过了")
            try:
                sys.exit(1)
            except SystemExit:
                return check()
        print("PASS: 什么都没测")
        return 0

    inner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The keyword counterpart: `**argv` collects keyword arguments into a dict,
#: which is no more the entry value than `*argv` is.
PY_NESTED_KWARG = PY_NESTED_VARARG.replace(
    "    def inner(*argv):",
    "    def inner(**argv):",
)

#: `sys.argv[1:1]` is empty, not the trimmed command line: only a slice with
#: no upper bound and no step is the `[1:]` idiom.
PY_SLICE_EMPTY = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) == 0:
        if "--self-test" in argv:
            print("PASS: 测过了")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:1]))
'''

#: The entry call passes the command line as a keyword argument; it is the same
#: full argv as `main(sys.argv)`, so the gate on it is false during a self-test.
PY_KEYWORD_ENTRY = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv))
'''

#: Two call sites disagree: the entry trims, a helper passes the command line
#: whole. Taking the helper's shape alone would judge the gate false and bless
#: the hand-off as a no-op; neither call may settle it.
PY_TWO_ENTRY_SHAPES = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) == 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
        try:
            sys.exit(1)
        except SystemExit:
            return check()
    print("PASS: 什么都没测")
    return 0


def run():
    main(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''

PY_UNREADABLE_COUNT = '''#!/usr/bin/env python3
import subprocess
import sys
import tempfile
from pathlib import Path

LIMIT = len("--self-test") - 11


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1 or "修法" not in done.stderr:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成守卫自测（.bad 文件被报出并给了修法）")
    return 0


def scan(argv):
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if argv.count("--self-test") == LIMIT:
        return self_test()
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The exit sits in an `elif` body and lands in a handler that returns the
#: hand-off.
PY_ELIF_EXIT_HANDLER = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        elif len(sys.argv) > 1:
            print("PASS: 什么都没测")
            sys.exit(0)
        else:
            return 0
    except SystemExit:
        return check()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: `main(sys.argv)` is the same command line, so `len(sys.argv) > 1` holds and
#: the inner `sys.exit(0)` ends the run for `--self-test`.
PY_SYS_ARGV_BRANCH = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" in argv:
            if len(sys.argv) > 1:
                print("PASS: 什么都没测")
                sys.exit(0)
        else:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
    except SystemExit:
        raise
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The self-test ends through the bare builtin `exit(0)`, so `check` below is
#: dead. Missing that call keeps the hand-off on the path.
PY_BARE_EXIT = '''#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if "--self-test" in argv:
        print("PASS: 什么都没测")
        exit(0)
    if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")):
        return 1
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The branch sits under an unreadable gate, so its exit does not settle. The
#: handler below it — which spawns a sibling test — is therefore not on the
#: self-test path, and the guard's own self-test does bite.
PY_GATED_HANDLER_ARM = '''#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1 or "修法" not in done.stderr:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成守卫自测（.bad 文件被报出并给了修法）")
    return 0


def scan(argv):
    if "--self-test" in argv:
        return self_test()
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if os.environ.get("MODE") == "1":
        try:
            if "--self-test" in argv:
                print("PASS: 什么都没测")
                sys.exit(0)
        except SystemExit:
            return subprocess.call(
                [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
            )
    return scan(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: Same gate, but the exit after the branch is what the handler catches. The
#: handler is again off the path, so the sibling spawn must not be read.
PY_GATED_HANDLER_TAIL = '''#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1 or "修法" not in done.stderr:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成守卫自测（.bad 文件被报出并给了修法）")
    return 0


def scan(argv):
    if "--self-test" in argv:
        return self_test()
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if os.environ.get("MODE") == "1":
        try:
            if "--self-test" not in argv:
                return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
            print("PASS: 什么都没测")
            sys.exit(0)
        except SystemExit:
            return subprocess.call(
                [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
            )
    return scan(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

#: The scan path shells out to `helper.py`; the self-test re-runs this very
#: file, so mutating it reaches the process that judges. Only the self-test
#: path decides whether a spawn makes the mutation unreachable.
PY_FOREIGN_SPAWN_IN_SCAN = '''#!/usr/bin/env python3
"""The normal scan runs helper.py; the self-test re-runs this file."""
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        return self_test()
    subprocess.run([sys.executable, str(Path(__file__).with_name("helper.py"))], check=False)
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    return 1 if problems else 0


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1:
            print("SELF-TEST FAIL: 没有以 1 报出 .bad", file=sys.stderr)
            return 1
    print("PASS: .bad 被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
'''

HELPER_SCRIPT = '''#!/usr/bin/env python3
print("helper")
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

    def test_a_shell_guard_defined_below_its_self_test_is_still_judged(self) -> None:
        """The rule sits after the block and the child re-run still sees the silenced copy."""
        code, guard, output = self._only({"lateshell.sh": PASSES_LATE_SHELL})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_an_exit_argument_that_is_a_call_keeps_its_side_effects(self) -> None:
        """`raise SystemExit(report(root))` must still run `report`, or its red is a lie."""
        code, guard, output = self._only({"sideeffect.py": SIDE_EFFECT_EXIT})
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_an_exit_argument_that_is_a_call_still_loses_its_verdict(self) -> None:
        """Discarding the call's result, not the call, is what makes this one bite."""
        code, guard, output = self._only({"callexit.py": CALL_EXIT_BITING})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_shell_guard_whose_verdict_is_a_variable_is_unverified(self) -> None:
        """`exit "$CODE"` carries no literal to rewrite; declining beats guessing."""
        code, guard, output = self._only({"namedshell.sh": NAMED_SHELL_VERDICT})
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("自测块之外没有非 0 的 exit/return", guard["detail"])

    def test_silencing_a_shell_guard_keeps_its_scan_and_its_record(self) -> None:
        """The shell twin of the blocker: a red made by the record vanishing is not a bite.

        Silencing the whole rule function also stopped `record()` from writing
        the `--json` line, so the self-test failed for the wrong reason and the
        guard was blessed as biting. Rewriting only the verdict leaves the record
        in place, and this self-test stays green — which is the truth about it.
        """
        code, guard, output = self._only({"cleanonly.sh": CLEAN_ONLY_SHELL})
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_python_predicate_that_answers_yes_is_not_the_verdict(self) -> None:
        """`return 1` for a *good* line is not the exit status; rewriting it inverts the rule."""
        code, guard, output = self._only({"predicate.py": PY_PREDICATE})
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_shell_predicate_that_answers_yes_is_not_the_verdict(self) -> None:
        """`has_bad`'s `return 1` is the grep convention, not the guard's verdict."""
        code, guard, output = self._only({"predicate.sh": SHELL_PREDICATE})
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_silencing_a_shell_guard_leaves_printed_text_alone(self) -> None:
        """`exit 1` inside a record the guard prints is output, not an exit status.

        Rewriting it changes the record, so a record assertion fails and the
        guard looks like it bites. This self-test only checks the clean path.
        """
        for name, source in (
            ("record.sh", SHELL_RECORD_TEXT),
            ("heredoc-record.sh", SHELL_HEREDOC_RECORD),
        ):
            code, guard, output = self._only({name: source})
            self.assertEqual(code, 1, output)
            self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_an_unquoted_hint_is_an_argument_not_a_verdict(self) -> None:
        """`echo 修法：让本守卫 exit 1` prints those words; nothing exits.

        Rewriting the argument changes the printed hint, and a self-test that
        only reads that hint goes red over the edit — reported as a bite by a
        guard that never checks its exit status.
        """
        code, guard, output = self._only({"hint.sh": SHELL_UNQUOTED_HINT})
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_status_handed_on_through_a_trailing_if_is_still_the_verdict(self) -> None:
        """`run` ends on `fi`, and the command that answers is `scan` inside it.

        Reading only the last statement of the body misses `scan`, leaves its
        `return 1` untouched, and calls a guard that really bites a no-op.
        """
        code, guard, output = self._only({"trailing.sh": SHELL_TRAILING_IF})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_quoted_heredoc_opener_is_not_a_heredoc(self) -> None:
        """`grep '<<EOF'` searches for the text; it does not open a heredoc."""
        code, guard, output = self._only({"quoted.sh": SHELL_QUOTED_HEREDOC_OP})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_self_test_helper_defined_outside_the_block_is_left_alone(self) -> None:
        """`self_fail` reports the self-test's failure, whichever side of `if` it sits on."""
        code, guard, output = self._only({"selffail.sh": SHELL_SELF_FAIL_OUTSIDE})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_spawn_on_the_scan_path_is_not_a_delegated_self_test(self) -> None:
        """Only the self-test's own spawns decide whether the mutation reaches the judge."""
        code, guard, output = self._only(
            {"spawnscan.py": PY_FOREIGN_SPAWN_IN_SCAN}, {"helper.py": HELPER_SCRIPT}
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_self_test_branch_that_calls_an_oddly_named_helper_is_followed(self) -> None:
        """The branch says `return check()`, and `check` does the delegating.

        Following only functions named like a self-test sees no spawn and calls
        this a guard that does not bite, when the honest answer is that nothing
        here can judge the copy.
        """
        code, guard, output = self._only(
            {"wrapper.py": PY_DELEGATES_THROUGH_WRAPPER}, {"test_wrapper.py": WRAPPER_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_negative_branch_is_the_scan_not_the_self_test(self) -> None:
        """`if "--self-test" not in argv:` guards the scan; its helper is not a delegation."""
        code, guard, output = self._only(
            {"negsweep.py": PY_NEGATIVE_BRANCH_SCAN}, {"helper.py": HELPER_SCRIPT}
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_hand_off_after_a_negative_branch_is_still_unverified(self) -> None:
        """The self-test is what runs when the branch is skipped, so it is on the path."""
        code, guard, output = self._only(
            {"elsedel.py": PY_ELSE_DELEGATION}, {"test_else.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_an_elif_branch_still_leaves_the_rest_of_the_block_on_the_path(self) -> None:
        """`elif` is an `if` inside an `orelse`; what follows it is on the path too."""
        code, guard, output = self._only(
            {"nested.py": PY_NESTED_ELSE_DELEGATION}, {"test_else.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_counted_zero_is_the_scan_not_the_self_test(self) -> None:
        """`count() == 0` holds when the flag is absent, exactly like `not in`."""
        code, guard, output = self._only(
            {"counted.py": PY_COUNT_ZERO_SCAN}, {"helper.py": HELPER_SCRIPT}
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_counted_one_is_the_self_test_not_the_scan(self) -> None:
        """`count() == 1` holds when the flag is present, so the branch hands off."""
        code, guard, output = self._only(
            {"countedone.py": PY_COUNT_ONE_HANDOFF}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_spawn_after_the_self_test_returns_is_not_on_its_path(self) -> None:
        """The self-test exits inside the outer `if`; `helper.py` below never runs."""
        code, guard, output = self._only(
            {"unreachable.py": PY_UNREACHABLE_SPAWN}, {"helper.py": HELPER_SCRIPT}
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_count_against_a_named_zero_is_read_as_the_scan(self) -> None:
        """`ZERO = 0` is the same operand as a literal zero; the hand-off follows it."""
        code, guard, output = self._only(
            {"namedcount.py": PY_NAMED_COUNT}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_counted_branch_that_falls_through_still_reaches_the_hand_off(self) -> None:
        """The self-test side here is the branch body, and it does not return."""
        code, guard, output = self._only(
            {"countedtail.py": PY_COUNTED_TAIL}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_loop_body_that_runs_zero_times_leaves_the_hand_off_on_the_path(self) -> None:
        """`roots` is empty for `--self-test`, so its `return 0` never runs."""
        code, guard, output = self._only(
            {"loopbody.py": PY_LOOP_BODY_BRANCH}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_caught_raise_does_not_end_the_self_test_path(self) -> None:
        """The handler swallows the `raise`, so `check` after the `try` still runs."""
        code, guard, output = self._only(
            {"caughtraise.py": PY_CAUGHT_RAISE}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_sys_exit_in_the_self_test_makes_the_hand_off_below_it_dead(self) -> None:
        """`sys.exit(0)` ends the process, so the `check` after the `try` never runs."""
        code, guard, output = self._only(
            {"sysexithandoff.py": PY_SYSTEM_EXIT_HANDOFF}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_swallowed_system_exit_leaves_the_hand_off_on_the_path(self) -> None:
        """`except SystemExit: pass` resumes at `check` after the `try`."""
        code, guard, output = self._only(
            {"swallowedexit.py": PY_SWALLOWED_EXIT}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_raised_system_exit_carried_out_by_the_handler_is_final(self) -> None:
        """`raise SystemExit(0)` re-raised by the handler never reaches `check`."""
        code, guard, output = self._only(
            {"raisedexit.py": PY_RAISED_EXIT}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_branch_under_an_unsettled_condition_does_not_end_the_path(self) -> None:
        """The outer block reads the environment, so its `return 0` may not run."""
        code, guard, output = self._only(
            {"unreached.py": PY_UNREACHED_SELF_TEST_BRANCH},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_branch_in_a_with_body_ends_the_path(self) -> None:
        """A `with` body provably runs, so its `return 0` kills the hand-off."""
        code, guard, output = self._only(
            {"withbranch.py": PY_WITH_BRANCH}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_branch_in_a_match_case_reaches_the_hand_off_below_the_match(self) -> None:
        """Falling out of the case lands on `check` after the `match`."""
        code, guard, output = self._only(
            {"matchhandoff.py": PY_MATCH_CASE_HANDOFF},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_zero_bound_in_the_reading_function_is_read(self) -> None:
        """`zero = 0` inside `main` is the constant the branch in `main` reads."""
        code, guard, output = self._only(
            {"shadowzero.py": PY_SHADOW_ZERO}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_hand_off_in_the_handlers_return_is_reached(self) -> None:
        """`except SystemExit: return check()` runs the sibling, so `check` is on the path."""
        code, guard, output = self._only(
            {"handlerreturn.py": PY_HANDLER_RETURN_HANDOFF},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_hand_off_in_a_handler_reached_from_the_branch_side_is_found(self) -> None:
        """The branch side exits into a handler; its return runs the sibling."""
        code, guard, output = self._only(
            {"handlerinside.py": PY_HANDLER_RETURN_INSIDE},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_hand_off_in_a_handler_reached_from_a_raised_exit_is_found(self) -> None:
        """`raise SystemExit(0)` lands in the handler the same way an `exit` call does."""
        code, guard, output = self._only(
            {"handlerraised.py": PY_HANDLER_RETURN_RAISED},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_count_compared_against_an_unreadable_name_keeps_the_hand_off(self) -> None:
        """`1 == LIMIT` is false, so the flag takes the hand-off; guessing stops the walk."""
        code, guard, output = self._only(
            {"unreadable.py": PY_UNREADABLE_COUNT}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_negated_unreadable_count_still_keeps_the_hand_off(self) -> None:
        """`not (1 == LIMIT)` is false at runtime; `not None` must not settle it."""
        code, guard, output = self._only(
            {"negated.py": PY_NEGATED_COUNT}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_an_unsettled_test_keeps_the_hand_off_in_its_body(self) -> None:
        """Both sides stay on the path, so the body's hand-off is not dropped."""
        code, guard, output = self._only(
            {"body.py": PY_UNREADABLE_BODY_HANDOFF},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_tail_behind_a_provably_false_gate_is_not_on_the_path(self) -> None:
        """A false outer gate rules out the whole block, handler included."""
        code, guard, output = self._only(
            {"falsetail.py": PY_FALSE_GATE_TAIL},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_length_gate_over_a_trimmed_argv_keeps_the_hand_off(self) -> None:
        """`main(sys.argv[1:])` leaves one word, so `len(argv) == 1` is true."""
        code, guard, output = self._only(
            {"trimmed.py": PY_TRIMMED_ARGV_HANDOFF},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_sys_argv_is_not_the_trimmed_parameter(self) -> None:
        """`main(sys.argv[1:])` trims the parameter; `sys.argv` keeps both words."""
        code, guard, output = self._only(
            {"flatargv.py": PY_SYS_ARGV_FLAT},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_rebinding_in_another_function_does_not_excuse_the_gate(self) -> None:
        """Only `main`'s own rebinding makes its argv unreadable."""
        code, guard, output = self._only(
            {"unrelated.py": PY_UNRELATED_REBIND},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_store_in_a_nested_scope_does_not_rebind_the_parameter(self) -> None:
        """A class body's `argv` is its own name; `main`'s parameter is untouched."""
        code, guard, output = self._only(
            {"scoped.py": PY_SCOPED_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_an_argv_the_guard_trims_itself_keeps_the_hand_off(self) -> None:
        """`argv = argv[1:]` inside `main`: the call site alone cannot settle it."""
        code, guard, output = self._only(
            {"rebound.py": PY_REBOUND_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_an_annotated_assignment_is_a_rebinding_too(self) -> None:
        """`argv: list[str] = ...` stores the name even though it is not `ast.Assign`."""
        code, guard, output = self._only(
            {"annotated.py": PY_ANNOTATED_REBIND},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_an_argv_imported_from_sys_is_not_the_trimmed_parameter(self) -> None:
        """A module-level `from sys import argv` holds the process command line."""
        code, guard, output = self._only(
            {"imported.py": PY_IMPORTED_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_module_level_assignment_is_not_the_trimmed_parameter(self) -> None:
        """`argv = sys.argv` at module level holds the process command line."""
        code, guard, output = self._only(
            {"assigned.py": PY_ASSIGNED_GLOBAL_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_main_parameter_shadows_the_module_level_name(self) -> None:
        """`main(argv)`'s own parameter leaves the entry call the one to settle."""
        code, guard, output = self._only(
            {"shadowed.py": PY_SHADOWED_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_walrus_in_a_comprehension_rebinds_the_parameter(self) -> None:
        """A comprehension's own target stays inside; a `:=` writes outside it."""
        code, guard, output = self._only(
            {"walrus.py": PY_COMPREHENSION_WALRUS},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_star_import_binds_the_name_as_well(self) -> None:
        """`from sys import *` may bind `argv`, so the bare name stays unsettled."""
        code, guard, output = self._only(
            {"star.py": PY_STAR_IMPORT},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_an_unsettled_name_read_for_truth_keeps_the_hand_off(self) -> None:
        """Reading the name itself as false would settle a gate that is unknown."""
        code, guard, output = self._only(
            {"truthy.py": PY_TRUTHY_UNSETTLED_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_helper_parameter_is_not_the_entry_value(self) -> None:
        """A helper's own `argv` holds what its callers pass, not the entry argv."""
        code, guard, output = self._only(
            {"helper.py": PY_HELPER_OWN_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_lambda_parameter_is_not_the_entry_value(self) -> None:
        """A lambda's parameter is no more the entry value than a helper's."""
        code, guard, output = self._only(
            {"lambda.py": PY_LAMBDA_OWN_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_gate_behind_a_helper_parameter_is_not_settled(self) -> None:
        """Settling it would prune the hand-off and report a false finding."""
        code, guard, output = self._only(
            {"gate.py": PY_HELPER_GATE_DELEGATES},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_gate_on_a_helpers_argument_is_not_the_trimmed_entry(self) -> None:
        """Without a module-level `argv`, the helper's value is all there is to confuse."""
        code, guard, output = self._only(
            {"gate.py": PY_HELPER_GATE_UNSETTLED_ENTRY},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_closure_without_its_own_parameter_reads_mains(self) -> None:
        """A nested function that does not declare `argv` sees main's parameter."""
        code, guard, output = self._only(
            {"closure.py": PY_NESTED_CLOSURE_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_comprehension_target_does_not_rebind_the_parameter(self) -> None:
        """`for argv in ...` inside a comprehension is that comprehension's own."""
        code, guard, output = self._only(
            {"target.py": PY_COMPREHENSION_TARGET},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_keyword_only_parameter_is_the_one_the_entry_fills(self) -> None:
        """`def main(*, argv)` is the same parameter as the positional one."""
        code, guard, output = self._only(
            {"kwonly.py": PY_KEYWORD_ONLY_ARGV},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_variadic_parameter_does_not_hold_the_entry_value(self) -> None:
        """`main(*argv)` collects the arguments; it is not the argument."""
        code, guard, output = self._only(
            {"star.py": PY_MAIN_VARARG},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_nested_variadic_parameter_binds_its_own_name(self) -> None:
        """A closure's `*argv` is its own tuple; main's parameter does not reach it."""
        code, guard, output = self._only(
            {"nested.py": PY_NESTED_VARARG},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_nested_keyword_variadic_parameter_binds_its_own_name(self) -> None:
        """`**argv` collects keywords into a dict — also not the entry value."""
        code, guard, output = self._only(
            {"nested.py": PY_NESTED_KWARG},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_slice_with_an_upper_bound_is_not_the_trimmed_form(self) -> None:
        """`sys.argv[1:1]` is empty, so nothing may be settled from it."""
        code, guard, output = self._only(
            {"slice.py": PY_SLICE_EMPTY},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_helper_without_the_parameter_still_reads_the_module_name(self) -> None:
        """One unshadowed read anywhere leaves the entry call unable to settle it."""
        code, guard, output = self._only(
            {"reader.py": PY_HELPER_READS_GLOBAL},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_keyword_entry_call_is_read_like_a_positional_one(self) -> None:
        """`main(argv=sys.argv)` hands over the full command line, so the gate is false."""
        code, guard, output = self._only(
            {"kentry.py": PY_KEYWORD_ENTRY},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_call_sites_that_disagree_leave_the_argv_unsettled(self) -> None:
        """One call trims and one does not; taking either alone would invent an answer."""
        code, guard, output = self._only(
            {"shapes.py": PY_TWO_ENTRY_SHAPES},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_gate_the_trimmed_argv_really_falsifies_still_bites(self) -> None:
        """Trimmed argv does not make every condition unsettled: this one is false."""
        code, guard, output = self._only(
            {"trimmedfalse.py": PY_TRIMMED_ARGV_FALSE_GATE},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_handler_behind_a_provably_false_gate_is_not_on_the_path(self) -> None:
        """`len(argv) > 2` is false for the self-test, so its handler never runs."""
        code, guard, output = self._only(
            {"falsegate.py": PY_PROVEN_FALSE_GATE},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_an_exit_in_an_elif_body_reaches_the_handler(self) -> None:
        """The `elif` body is what runs here, and its exit lands in the handler."""
        code, guard, output = self._only(
            {"elifhandler.py": PY_ELIF_EXIT_HANDLER}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_sys_argv_is_read_as_the_self_test_command_line(self) -> None:
        """`main(sys.argv)` carries the same argv, so the inner block runs."""
        code, guard, output = self._only(
            {"sysargv.py": PY_SYS_ARGV_BRANCH}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_bare_exit_call_ends_the_path(self) -> None:
        """The builtin `exit(0)` ends the run just as `sys.exit(0)` does."""
        code, guard, output = self._only(
            {"bareexit.py": PY_BARE_EXIT}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_handler_under_an_unsettled_gate_is_left_unverified(self) -> None:
        """The branch sits under `MODE`; its handler may run, so do not judge."""
        code, guard, output = self._only(
            {"gatedarm.py": PY_GATED_HANDLER_ARM}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_handler_tail_under_an_unsettled_gate_is_left_unverified(self) -> None:
        """The exit after the branch is gated too; its handler may be entered."""
        code, guard, output = self._only(
            {"gatedtail.py": PY_GATED_HANDLER_TAIL}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_an_annotated_module_zero_is_read_as_the_scan(self) -> None:
        """`ZERO: int = 0` is the same operand as `ZERO = 0`."""
        code, guard, output = self._only(
            {"annotatedzero.py": PY_ANNOTATED_ZERO}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_branch_in_a_finally_ends_the_path(self) -> None:
        """A `finally` body always runs, so its `return 0` kills the hand-off."""
        code, guard, output = self._only(
            {"finallybranch.py": PY_FINALLY_BRANCH}, {"test_counted.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_branch_under_a_settled_length_ends_the_path(self) -> None:
        """`len(argv) > 1` holds for `["guard.py", "--self-test"]`."""
        code, guard, output = self._only(
            {"lengthbranch.py": PY_LENGTH_GUARDED_BRANCH},
            {"test_counted.py": ELSE_SIBLING},
        )
        self.assertEqual(code, 1, output)
        self.assertEqual(guard["status"], "self-test-does-not-bite", guard)

    def test_a_branch_in_a_handler_still_reaches_the_hand_off_below_the_try(self) -> None:
        """Falling out of the `except` body lands on `check` after the `try`."""
        code, guard, output = self._only(
            {"handlerhandoff.py": PY_EXCEPT_HANDOFF}, {"test_except.py": ELSE_SIBLING}
        )
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertIn("测试文件", guard["detail"])

    def test_a_verdict_after_a_control_word_is_still_the_verdict(self) -> None:
        """`then exit 1` is a command in command position, not another word of the test."""
        code, guard, output = self._only({"control.sh": SHELL_CONTROL_WORD_VERDICT})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

    def test_a_call_after_the_flow_end_is_not_part_of_the_verdict_path(self) -> None:
        """`dead` never runs, so its `return 1` proves nothing about this guard."""
        code, guard, output = self._only({"deadcode.sh": SHELL_DEAD_CODE_AFTER_RETURN})
        self.assertEqual(code, 2, output)
        self.assertEqual(guard["status"], "unverified", guard)

    def test_a_fi_inside_a_heredoc_does_not_end_the_self_test_block(self) -> None:
        """The self-test writes a sample containing `fi`; that line is data.

        Reading it as the block's end leaves the self-test's own `exit 1`
        rewritten, so a guard that really bites was reported as unverified.
        """
        code, guard, output = self._only({"heredocfi.sh": SHELL_HEREDOC_FI})
        self.assertEqual(code, 0, output)
        self.assertEqual(guard["status"], "ok", guard)

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

    def test_a_hand_off_built_into_a_variable_is_still_unverified(self) -> None:
        """The command is in `command`; the call site alone says the guard re-runs itself."""
        code, guard, output = self._only(
            {"handoff.py": DELEGATING_VARIABLE}, {"test_handoff.py": DELEGATING_VARIABLE_SIBLING}
        )
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

    # --- the allowance, which has to be a claim and not a waiver -----------
    #
    # `--allow-unjudgeable NAME` exists for a guard this lane cannot judge: its
    # self-test judges it in another process (`unverified`), or needs an install
    # the lane does not have (its self-test does not start, which also reads as
    # `self-test-not-green`). The point of these cases is that the flag stays
    # narrow — it tolerates those two statuses for one named guard, the entry is
    # checked, and this lane's own judgement is never waived.

    def _allow(self, guards: dict[str, str], extra: dict[str, str], *allowed: str):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _build(root, guards, extra)
            result = _run(root, config, "--json", *allowed)
        return result

    def _allow_human(
        self, guards: dict[str, str], extra: dict[str, str], *allowed: str
    ):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _build(root, guards, extra)
            result = _run(root, config, *allowed)
        return result

    def test_an_unverified_guard_is_not_a_pass_without_the_allowance(self) -> None:
        """The baseline the flag is measured against: exit 2, not a pass."""
        result = self._allow(
            {"delegating.py": DELEGATING}, {"test_delegating.py": DELEGATING_SIBLING}
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)["guards"][0]["allowed"])

    def test_an_allowed_unverified_guard_is_reported_and_not_counted(self) -> None:
        """Allowed by name: still printed as unverified, but exit 0."""
        result = self._allow(
            {"delegating.py": DELEGATING},
            {"test_delegating.py": DELEGATING_SIBLING},
            "--allow-unjudgeable",
            "delegating.py",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        guard = json.loads(result.stdout)["guards"][0]
        self.assertEqual(guard["status"], "unverified", guard)
        self.assertTrue(guard["allowed"], guard)

    def test_the_human_report_marks_an_allowed_run_rather_than_hiding_it(self) -> None:
        """`unverified` is still printed; the allowance is visible on the line."""
        result = self._allow_human(
            {"delegating.py": DELEGATING},
            {"test_delegating.py": DELEGATING_SIBLING},
            "--allow-unjudgeable",
            "delegating.py",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("UNVERIFIED (allowed)", result.stdout)

    def test_an_allowance_covers_a_self_test_that_is_red_in_this_lane(self) -> None:
        """scene-debt-ratchet's shape in a lane without frontend/node_modules.

        Silencing is irrelevant here: the self-test runs and fails on what the
        lane lacks, which is `self-test-not-green` — the same status a broken
        self-test produces, so this lane cannot tell them apart. The allowance
        is what says the breakage is the lane's. (The other shape, a self-test
        that cannot start at all, is `unverified` and is covered by the first
        case above; both statuses are in `ALLOWABLE_STATUSES`.)
        """
        result = self._allow(
            {"needsnode.py": NOT_GREEN}, {}, "--allow-unjudgeable", "needsnode.py"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        guard = json.loads(result.stdout)["guards"][0]
        self.assertEqual(guard["status"], "self-test-not-green", guard)
        self.assertTrue(guard["allowed"], guard)

    def test_an_allowance_for_a_guard_that_is_verified_now_is_reported(self) -> None:
        """A fixed guard leaves its excuse behind; that stale entry is a finding."""
        result = self._allow(
            {"biting.py": BITING}, {}, "--allow-unjudgeable", "biting.py"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("verified now", result.stdout + result.stderr)

    def test_an_allowance_for_a_guard_the_run_never_judged_is_reported(self) -> None:
        result = self._allow(
            {"biting.py": BITING}, {}, "--allow-unjudgeable", "gone.py"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no guard of that name was judged", result.stdout + result.stderr)

    def test_an_allowance_never_waives_this_lanes_own_verdict(self) -> None:
        """Biting is this lane's judgement of the guard, so it is not excusable."""
        result = self._allow(
            {"fake.py": FAKE_GREEN}, {}, "--allow-unjudgeable", "fake.py"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("this lane's own judgement", result.stdout + result.stderr)

    def test_the_summary_counts_a_guard_and_its_stale_entry_separately(self) -> None:
        """One guard that is both a finding and a bad entry is one finding line.

        Adding the two numbers used to report `有发现 2 个` for a single guard;
        the summary names the allowance problems as their own count instead.
        """
        result = self._allow_human(
            {"fake.py": FAKE_GREEN}, {}, "--allow-unjudgeable", "fake.py"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("有发现 1 个，名单问题 1 条", result.stdout)

    def test_the_summary_omits_the_allowance_count_when_there_is_no_problem(self) -> None:
        result = self._allow_human({"fake.py": FAKE_GREEN}, {})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("有发现 1 个，判不了", result.stdout)
        self.assertNotIn("名单问题", result.stdout)

    def test_an_allowance_never_waives_a_missing_self_test(self) -> None:
        """The lane can always see that a guard carries no self-test at all."""
        result = self._allow(
            {"bare.py": NO_SELF_TEST}, {}, "--allow-unjudgeable", "bare.py"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("this lane's own judgement", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
