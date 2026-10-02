"""Turn a shard's JUnit report into GitHub annotations a check run can show.

More than half of the check-run annotations on failing CI jobs said only
"Process completed with exit code 1." The real reason was in the job log, and the
platform's GitHub App has no ``actions:read``, so neither a person nor an agent
could reach it. pytest already writes that reason into the JUnit report the test
step asks for; this reads the report back and restates its failing cases as
``::error`` workflow commands, which GitHub attaches to the files in the change
instead of leaving them buried in a log nobody can open.

A shard can also die before it writes any report — collection stopped and the
partition artifact never appeared. Then its captured stdout is the only evidence
left, so the tail of that is annotated instead.

The step that runs this is gated on ``failure()`` and must never add a failure of
its own: ``main`` prints and exits 0 whatever it finds.
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# GitHub keeps ten error annotations per step and silently drops the rest; the
# summary below is one of the ten, so at most nine files get a line of their own.
ANNOTATION_LIMIT = 10
# GitHub truncates a command line near 4 KB. Collapse each message onto one line
# and keep the whole command below that, so it survives intact.
COMMAND_LIMIT = 3900
LOG_TAIL_LINES = 15

_FAILURE_TAGS = ("failure", "error")
# The last frame of a pytest traceback names the failing line: ``path:42:``.
_TRACEBACK_FRAME = re.compile(r"([\w./-]+\.py):(\d+)")

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent


def _collapse(text: str, limit: int = COMMAND_LIMIT) -> str:
    """Squeeze ``text`` onto one line and cut it to ``limit`` characters."""
    one_line = " ".join(text.split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: max(limit - 1, 0)] + "…"


def _command(prefix: str, text: str) -> str:
    """A workflow command whose whole line stays under GitHub's size limit."""
    room = max(COMMAND_LIMIT - len(prefix), 80)
    return prefix + _collapse(text, room)


def _failure_node(case: ET.Element) -> ET.Element | None:
    """The ``failure`` or ``error`` child that marks a case as red."""
    for tag in _FAILURE_TAGS:
        node = case.find(tag)
        if node is not None:
            return node
    return None


def _first_line(node: ET.Element) -> str:
    """The first line of a failure's message — the reason, before the traceback."""
    message = node.get("message") or node.text or ""
    stripped = message.strip()
    if not stripped:
        return "no failure message"
    return stripped.splitlines()[0].strip()


def _property(case: ET.Element, name: str) -> str | None:
    for prop in case.findall("./properties/property"):
        if prop.get("name") == name:
            return prop.get("value")
    return None


def _location(case: ET.Element, node: ET.Element) -> tuple[str | None, str | None]:
    """Where to hang the annotation, or ``(None, None)`` when nothing says.

    Prefer the JUnit ``file``/``line`` attributes (written in the legacy xUnit 1
    style) and the shard's own ``cheese_nodeid`` property (always written by
    ``scripts.ci_shard``); fall back to the last ``path:line`` in the traceback.
    """
    file_name = case.get("file")
    line = case.get("line")
    if not file_name:
        nodeid = _property(case, "cheese_nodeid")
        if nodeid:
            file_name = nodeid.split("::", 1)[0]
    if not line:
        frames = _TRACEBACK_FRAME.findall(node.text or "")
        if frames:
            file_name = file_name or frames[-1][0]
            line = frames[-1][1]
    return file_name, line


def _repo_relative(raw: str | None) -> str | None:
    """A JUnit path made relative to the repository root, which ``file=`` wants.

    The report is written against the pytest rootdir (``backend/`` in CI), so a
    relative path is relative to that, not to the checkout.
    """
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            return str(candidate.relative_to(REPO_ROOT))
        except ValueError:
            return raw
    if candidate.parts and candidate.parts[0] == BACKEND.name:
        return raw
    return str(BACKEND.relative_to(REPO_ROOT) / candidate)


def _failures(tree: ET.ElementTree) -> list[tuple[str | None, str | None, str, str]]:
    """``(file, line, test name, reason)`` for every failing case, in report order."""
    found = []
    for case in tree.getroot().iter("testcase"):
        node = _failure_node(case)
        if node is None:
            continue
        file_name, line = _location(case, node)
        name = case.get("name") or case.get("classname") or "<unknown>"
        found.append((_repo_relative(file_name), line, name, _first_line(node)))
    return found


def format_annotations(
    failures: list[tuple[str | None, str | None, str, str]],
) -> list[str]:
    """The ``::error`` commands for ``failures``: one per file, then a summary."""
    groups: dict[str | None, list[tuple[str | None, str | None, str, str]]] = {}
    for failure in failures:
        groups.setdefault(failure[0], []).append(failure)
    # The most informative first: a file that broke five cases says more than one
    # that broke a single case. Ties keep report order.
    ordered = sorted(groups.values(), key=len, reverse=True)

    commands = []
    for group in ordered[: ANNOTATION_LIMIT - 1]:
        file_name, line, name, reason = group[0]
        detail = f"{name} — {reason}"
        if len(group) > 1:
            detail += f" ({len(group)} failures in this file)"
        if file_name and line:
            commands.append(_command(f"::error file={file_name},line={line}::", detail))
        else:
            detail = f"{file_name}: {detail}" if file_name else detail
            commands.append(_command("::error::", detail))
    commands.append(
        _command(
            "::error::",
            f"{len(failures)} failing case(s) across {len(groups)} file(s)",
        )
    )
    return commands


def _read_failures(results: Path) -> list[tuple[str | None, str | None, str, str]]:
    if not results.exists():
        return []
    try:
        tree = ET.parse(results)
    except ET.ParseError:
        return []
    return _failures(tree)


def _log_tail(log: Path | None) -> str:
    if log is None or not log.exists():
        return ""
    lines = [
        line for line in log.read_text(errors="replace").splitlines() if line.strip()
    ]
    return " ".join(lines[-LOG_TAIL_LINES:])


def report(results: Path, log: Path | None) -> list[str]:
    """The annotation commands for a shard: its failures, or why there are none."""
    failures = _read_failures(results)
    if failures:
        return format_annotations(failures)
    tail = _log_tail(log)
    if tail:
        return [
            _command(
                "::error::",
                f"the shard wrote no JUnit results; pytest output tail: {tail}",
            )
        ]
    return ["::error::the shard produced no results (no results.xml, no pytest.log)"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="the shard's JUnit report")
    parser.add_argument(
        "--log",
        type=Path,
        help="captured pytest output, for when the report never appeared",
    )
    arguments = parser.parse_args()
    try:
        for line in report(arguments.results, arguments.log):
            print(line)
    except Exception as exc:  # noqa: BLE001 — this step must never fail the job
        print(f"::error::could not read {arguments.results}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
