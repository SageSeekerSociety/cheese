"""The cap exists because one file, once, cost the whole repository its CI."""

import importlib.util
import pathlib
import re
import shlex
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("capper", _HERE / "cap-retained-logs.py")
capper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capper)

_WORKFLOWS = _HERE.parent / "workflows"
CAPPER = "cap-retained-logs.py"


class CapRetainedLogs(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_small_file_is_left_exactly_as_it_was(self) -> None:
        """Almost every run is this one. It must cost nothing and change nothing."""
        path = self.root / "runner.log"
        path.write_bytes(b"all fine\n")

        self.assertIsNone(capper.cap_file(path, cap=1024))
        self.assertEqual(path.read_bytes(), b"all fine\n")

    def test_an_oversized_log_keeps_its_tail(self) -> None:
        """The end of a log is where the failure is, so that is the half to keep."""
        path = self.root / "runner.log"
        path.write_bytes(b"x" * 500 + b"THE ACTUAL ERROR\n")

        capper.cap_file(path, cap=64)

        kept = path.read_bytes()
        self.assertIn(b"THE ACTUAL ERROR", kept)
        self.assertLess(len(kept), 400)

    def test_a_capped_log_says_so_inside_itself(self) -> None:
        """Otherwise the next reader takes the tail for the whole run and
        concludes it started where the file begins."""
        path = self.root / "runner.log"
        path.write_bytes(b"y" * 5000)

        capper.cap_file(path, cap=100)

        self.assertIn(b"cap-retained-logs", path.read_bytes())
        self.assertIn(b"bytes dropped", path.read_bytes())

    def test_structured_output_is_dropped_rather_than_half_kept(self) -> None:
        """A truncated JSON file looks readable and is not, which sends whoever
        opens it looking for a parser bug instead of a size cap."""
        path = self.root / "provider-requests.json"
        path.write_bytes(b'{"requests": [' + b'0,' * 5000 + b"]}")

        capper.cap_file(path, cap=100)

        kept = path.read_bytes()
        self.assertIn(b"cap-retained-logs", kept)
        self.assertNotIn(b'{"requests"', kept)

    def test_walking_a_tree_caps_only_what_is_over(self) -> None:
        big = self.root / "a" / "runner.log"
        big.parent.mkdir(parents=True)
        big.write_bytes(b"z" * 9000)
        small = self.root / "a" / "inputs.json"
        small.write_bytes(b"{}")

        capper.main([str(self.root), "--cap-bytes", "500"])

        self.assertLess(big.stat().st_size, 900)
        self.assertEqual(small.read_bytes(), b"{}")

    def test_a_run_that_produced_nothing_is_not_an_error(self) -> None:
        """`if: always()` means this runs after a job that died early too."""
        self.assertEqual(capper.main([str(self.root / "never-made"), "--cap-bytes", "1"]), 0)


# A cap script nothing runs is the state this file was written in: the 4.5 GB
# upload went through anyway. The wiring is a workflow file, so the guard below
# reads the workflow instead of trusting that whoever edited it remembered.
#
# Read by indentation rather than with a YAML library: repo-guards collects this
# file with the runner's plain `python3`, which has no PyYAML, and adding a
# dependency to the guard would make it the one check that needs an install.
_STEPS = re.compile(r"^(\s*)steps:\s*$")
_ITEM = re.compile(r"^(\s*)- ")


def _jobs(workflow: str) -> list[list[str]]:
    """Every job's steps, as text blocks, with jobs kept apart.

    A step's block runs until the next `- ` item at the same indent or a key at
    or left of `steps:` — so a cap step in one job cannot be read as covering an
    upload in another.
    """
    lines = workflow.splitlines()
    jobs: list[list[str]] = []
    index = 0
    while index < len(lines):
        key = _STEPS.match(lines[index])
        if not key:
            index += 1
            continue
        key_indent = len(key.group(1))
        index += 1
        while index < len(lines) and (
            not lines[index].strip() or lines[index].lstrip().startswith("#")
        ):
            index += 1
        if index >= len(lines):
            break
        item = _ITEM.match(lines[index])
        if not item:
            continue
        item_indent = len(item.group(1))
        steps: list[str] = []
        block: list[str] = []
        while index < len(lines):
            line = lines[index]
            if line.strip():
                indent = len(line) - len(line.lstrip())
                if indent <= key_indent:
                    break
                if indent == item_indent and _ITEM.match(line):
                    if block:
                        steps.append("\n".join(block))
                    block = []
            block.append(line)
            index += 1
        if block:
            steps.append("\n".join(block))
        jobs.append(steps)
    return jobs


def _value(block: str, key: str) -> str:
    """A step key's inline value, or the indented block under `key: |`."""
    lines = block.splitlines()
    for index, line in enumerate(lines):
        match = re.match(rf"^(\s*)(?:-\s+)?{re.escape(key)}:\s*(.*)$", line)
        if not match:
            continue
        value = match.group(2).strip()
        if value not in ("", "|", ">", "|-", "|+", ">-", ">+"):
            return value
        body = []
        key_indent = len(match.group(1))
        for follow in lines[index + 1:]:
            if follow.strip() and len(follow) - len(follow.lstrip()) <= key_indent:
                break
            body.append(follow.strip())
        return "\n".join(body).strip()
    return ""


def _cap_root(block: str) -> str:
    """The directory a cap step walks, taken from its `run:` command."""
    tokens = shlex.split(_value(block, "run"))
    for index, token in enumerate(tokens):
        if not token.endswith(CAPPER):
            continue
        rest = tokens[index + 1:]
        while rest:
            token = rest.pop(0)
            if token == "--cap-bytes":
                rest = rest[1:]
            elif not token.startswith("-"):
                return token.rstrip("/")
    return ""


def _uncapped_uploads(workflow: str) -> list[str]:
    """Every upload of a `logs/` path that no cap step before it covers.

    "Covers" means: a step earlier in the same job runs the cap script on a
    directory that contains the uploaded path, and runs under the same
    condition as the upload — a cap that only runs after a green test step is
    skipped on exactly the failing run that wrote the runaway log.
    """
    problems: list[str] = []
    for steps in _jobs(workflow):
        caps: list[str] = []
        for block in steps:
            if CAPPER in _value(block, "run"):
                caps.append(block)
                continue
            if "actions/upload-artifact" not in _value(block, "uses"):
                continue
            uploaded = [
                path
                for path in _value(block, "path").splitlines()
                if "logs/" in path
            ]
            if not uploaded:
                continue
            label = _value(block, "name") or "an unnamed upload"
            if not caps:
                problems.append(
                    f"{label}: uploads {uploaded} with no cap step before it"
                )
                continue
            cap, root = caps[-1], _cap_root(caps[-1])
            if not root:
                problems.append(f"{label}: the cap step names no directory to walk")
            else:
                uncovered = [
                    path
                    for path in uploaded
                    if path != root and not path.startswith(root + "/")
                ]
                if uncovered:
                    problems.append(f"{label}: {uncovered} is outside {root}")
            condition = _value(block, "if")
            if condition and _value(cap, "if") != condition:
                problems.append(
                    f"{label}: the cap step runs under "
                    f"'{_value(cap, 'if')}', not '{condition}'"
                )
    return problems


CAP_STEP = """\
      - name: Cap the retained logs
        if: always()
        run: python3 .github/scripts/cap-retained-logs.py logs/harness-contracts
"""


def _workflow(before_upload: str = "", after_upload: str = "") -> str:
    """A minimal workflow whose one job uploads a `logs/` path.

    `before_upload` and `after_upload` are step blocks spliced around the upload
    so a test can place the cap on either side of it.
    """
    return (
        "jobs:\n"
        "  contract:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - name: Run the tests\n"
        "        run: pytest\n"
        f"{before_upload}"
        "      - name: Retain the logs\n"
        "        if: always()\n"
        "        uses: actions/upload-artifact@abc\n"
        "        with:\n"
        "          path: |\n"
        "            logs/harness-contracts/*/*.log\n"
        f"{after_upload}"
    )


class ALogUploadIsCappedFirst(unittest.TestCase):
    def test_harness_contract_uploads_logs_and_caps_them_first(self) -> None:
        workflow = (_WORKFLOWS / "harness-contract.yml").read_text()

        uploads = [
            block
            for steps in _jobs(workflow)
            for block in steps
            if "actions/upload-artifact" in _value(block, "uses")
            and "logs/" in _value(block, "path")
        ]
        # Without this the guard could pass over a file it no longer matches.
        self.assertTrue(uploads, "harness-contract.yml no longer uploads a logs/ path")
        self.assertEqual(_uncapped_uploads(workflow), [])

    def test_an_upload_with_no_cap_before_it_is_reported(self) -> None:
        self.assertTrue(_uncapped_uploads(_workflow()))

    def test_a_cap_before_the_upload_satisfies_the_rule(self) -> None:
        self.assertEqual(_uncapped_uploads(_workflow(before_upload=CAP_STEP)), [])

    def test_a_cap_after_the_upload_does_not_count(self) -> None:
        """The upload has already copied the file by then."""
        self.assertTrue(_uncapped_uploads(_workflow(after_upload=CAP_STEP)))

    def test_a_cap_in_another_job_does_not_count(self) -> None:
        workflow = _workflow(
            after_upload=(
                "  other:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                f"{CAP_STEP}"
            )
        )
        self.assertTrue(_uncapped_uploads(workflow))

    def test_a_cap_that_skips_the_failing_run_is_reported(self) -> None:
        """The failing run is the one that wrote the runaway log."""
        self.assertTrue(
            _uncapped_uploads(
                _workflow(before_upload=CAP_STEP.replace("        if: always()\n", ""))
            )
        )

    def test_a_cap_walking_another_directory_is_reported(self) -> None:
        self.assertTrue(
            _uncapped_uploads(
                _workflow(
                    before_upload=CAP_STEP.replace(
                        "logs/harness-contracts", "logs/ci-feedback"
                    )
                )
            )
        )

    def test_an_upload_with_no_logs_path_is_left_alone(self) -> None:
        self.assertEqual(
            _uncapped_uploads(
                _workflow(before_upload=CAP_STEP).replace(
                    "logs/harness-contracts/*/*.log", "reports/contract.xml"
                )
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
