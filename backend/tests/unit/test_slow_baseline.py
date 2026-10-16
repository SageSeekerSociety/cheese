"""The lines of tests/slow_tests.txt, against what its own header promises.

Every line names a test that still exists, at most one line per test, in the
format the header documents, and the lines run slowest first. A test that was
deleted or renamed leaves its line behind claiming a fix is still owed; a line
that drifted out of order hides the test the list says to look at next; a line
in some other shape is one the list never meant to hold, and the tool that
refreshes the list cannot write.

The numbers on the lines are for orientation, as the header says: they come
from the runs in which the list was last measured, and refreshing them is
``scripts/test_durations.py`` reading those runs back. What this file checks is
the shape of the lines, not the numbers — a number that has gone stale is a
refresh, not a defect.
"""

import ast
import re
from collections import Counter
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
BASELINE = BACKEND / "tests" / "slow_tests.txt"

#: ``<node id>  # <layer>, <N>s`` — exactly the shape the header documents,
#: down to the two spaces and the one decimal place, because the lines are
#: written by reading the refresh table (``scripts/test_durations.py``).
_ENTRY = re.compile(
    r"(?P<nodeid>tests/[\w./-]+\.py::\w+(?:\[[\w.,-]+\])?)"
    r"  # (?P<layer>pure|contract|integration), (?P<seconds>\d+\.\d)s"
)


def _lines(text: str) -> list[str]:
    """The entry lines of ``text``, comments and blank lines dropped."""
    return [line for line in text.splitlines() if line.strip() and line[0] != "#"]


def _entries(text: str) -> list[str]:
    return [line.split()[0] for line in _lines(text)]


def _defines(tree: ast.AST, names: list[str]) -> bool:
    scope = tree.body
    for name in names:
        found = next(
            (
                node
                for node in scope
                if isinstance(
                    node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
                )
                and node.name == name
            ),
            None,
        )
        if found is None:
            return False
        scope = getattr(found, "body", [])
    return True


def _missing(nodeid: str) -> str | None:
    """Why the test this node id names is gone, or ``None`` while it is here.

    Without collecting the suite: the id's file is read and the named function
    (and class) looked for in it.
    """
    path, *names = nodeid.split("::")
    file = BACKEND / path
    if not file.is_file():
        return f"{nodeid}: {path} is gone, so drop its line"
    names[-1] = names[-1].split("[")[0]
    if not _defines(ast.parse(file.read_text()), names):
        return (
            f"{nodeid}: {path} no longer defines {'::'.join(names)}, so drop its line"
        )
    return None


def _problems(text: str) -> list[str]:
    """Everything ``text`` breaks, one message each; empty when it holds.

    Takes the text rather than reading the file, so the self-tests below can
    feed it a list with one thing wrong and watch that one come back. The paths
    in it are still resolved against this checkout.
    """
    problems: list[str] = []
    measured: list[tuple[float, str]] = []
    for line in _lines(text):
        match = _ENTRY.fullmatch(line)
        if match is None:
            problems.append(f"{line!r}: a line is '<node id>  # <layer>, <N>s'")
            continue
        nodeid = match.group("nodeid")
        problems.extend(filter(None, [_missing(nodeid)]))
        measured.append((float(match.group("seconds")), nodeid))
    for nodeid, count in Counter(_entries(text)).items():
        if count > 1:
            problems.append(f"{nodeid}: listed {count} times")
    for (previous, _), (current, nodeid) in zip(measured, measured[1:], strict=False):
        if current > previous:
            problems.append(
                f"{nodeid} is {current}s, under a line of {previous}s:"
                " the list runs slowest first"
            )
    return problems


def test_the_list_holds_what_its_header_says() -> None:
    problems = _problems(BASELINE.read_text())
    if problems:
        pytest.fail("\n".join(problems))


@pytest.mark.parametrize("nodeid", _entries(BASELINE.read_text()))
def test_a_listed_slow_test_still_exists(nodeid: str) -> None:
    assert _missing(nodeid) is None


def test_no_test_is_listed_twice() -> None:
    entries = _entries(BASELINE.read_text())
    assert len(entries) == len(set(entries))


# The guard has to be able to catch something: with every line clean, "came
# back empty" is as much its own success as it says nothing at all. What it
# would have to catch goes in and has to be named.
_HERE = "tests/unit/test_slow_baseline.py"
_MUST_CATCH = {
    "a-deleted-file": (
        "tests/unit/test_a_file_that_is_not_here.py::test_a  # pure, 9.0s"
    ),
    "a-renamed-test": f"{_HERE}::test_a_rule_that_moved_away  # pure, 9.0s",
    "the-same-test-twice": (
        f"{_HERE}::test_no_test_is_listed_twice  # pure, 9.0s\n"
        f"{_HERE}::test_no_test_is_listed_twice  # pure, 8.0s"
    ),
    "the-fast-one-on-top": (
        f"{_HERE}::test_no_test_is_listed_twice  # pure, 4.0s\n"
        f"{_HERE}::test_a_listed_slow_test_still_exists  # pure, 9.0s"
    ),
    "a-line-without-its-number": f"{_HERE}::test_no_test_is_listed_twice",
    "a-path-from-outside": (
        f"/tmp/backend/{_HERE}::test_no_test_is_listed_twice  # pure, 9.0s"
    ),
    "a-layer-that-is-not-one": f"{_HERE}::test_no_test_is_listed_twice  # fast, 9.0s",
}


@pytest.mark.parametrize("text", _MUST_CATCH.values(), ids=list(_MUST_CATCH))
def test_self_test_the_slow_list_guard_goes_red_on(text: str) -> None:
    assert _problems(text), f"没抓到：{text!r}"


def test_self_test_a_list_that_holds_passes() -> None:
    assert _problems(BASELINE.read_text()) == [], "误红：真实的慢测清单本身"
