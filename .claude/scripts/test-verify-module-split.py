#!/usr/bin/env python3
"""Offline self-test for verify-module-split.py.

    .claude/scripts/test-verify-module-split.py
    .claude/scripts/verify-module-split.py --self-test

No network, no credentials, no third-party import. Each scenario builds a
throwaway git repository under the system temp directory: the pre-split single
module is committed first, the package replaces it in a second commit, and the
tool is asked to compare the two. Git is run with `GIT_CONFIG_NOSYSTEM=1`,
`GIT_CONFIG_GLOBAL=/dev/null` and signing turned off in the repository's own
config, so a developer's machine-level settings cannot change the result.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "verify-module-split.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFERRED_RULE_SOURCE = Path("backend/scripts/check_deferred_imports.py")

#: Set while the suite is being run by `verify-module-split.py --self-test`, so
#: the test that checks that flag does not start a run of its own.
INNER_MARK = "VERIFY_MODULE_SPLIT_SELF_TEST_INNER"

ENV = {
    **os.environ,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}

#: The module as it stood before the split. `json` and `Any` are imported at
#: the top level: a real module brings things in for its own use, and the tool
#: must not mistake those for exports.
OLD_SOURCE = '''\
"""A module that is about to become a package."""

import json
from typing import Any

DEFAULT_NAME = "widget"
LIMIT = 10


def _private_helper(value: int) -> int:
    return value * 2


class Widget:
    """A thing."""

    def __init__(self, name, size=1):
        self.name = name
        self.size = size

    def render(self, width, height=0):
        return f"{self.name}{width}{height}"


def build(name, *, strict=False):
    import json

    return json.dumps({"name": name, "strict": strict})


def parse(payload: str) -> str:
    return json.loads(payload)["name"]
'''

#: The split a careful author would write: every name the old module offered,
#: reachable from the package, with the one deferred import carried over.
SPLIT = {
    "__init__.py": '''\
"""The split package."""

from .constants import DEFAULT_NAME, LIMIT
from .models import Widget
from .parsing import parse
from .rendering import build

__all__ = ["DEFAULT_NAME", "LIMIT", "Widget", "build", "parse"]
''',
    "constants.py": '''\
"""Constants."""

DEFAULT_NAME = "widget"
LIMIT = 10
''',
    "models.py": '''\
"""The widget model."""


class Widget:
    """A thing."""

    def __init__(self, name, size=1):
        self.name = name
        self.size = size

    def render(self, width, height=0):
        return f"{self.name}{width}{height}"
''',
    "parsing.py": '''\
"""Parsing helpers."""

import json


def parse(payload: str) -> str:
    return json.loads(payload)["name"]
''',
    "rendering.py": '''\
"""Rendering helpers."""


def build(name, *, strict=False):
    import json

    return json.dumps({"name": name, "strict": strict})
''',
}

#: `from .constants import *` reaches the same two names as naming them, so it
#: is a legal facade too — the tool has to follow the star.
SPLIT_WITH_STAR = {
    **SPLIT,
    "__init__.py": '''\
"""The split package, star-flavoured."""

from .constants import *
from .models import Widget
from .parsing import parse
from .rendering import build

__all__ = ["DEFAULT_NAME", "LIMIT", "Widget", "build", "parse"]
''',
}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=ENV,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {args} failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout


class Fixture:
    """A throwaway repository holding the pre-split module and the package."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.package_dir = root / "pkgroot" / "impl" / "foo"
        self._write_base_and_commit()

    def _write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_base_and_commit(self) -> None:
        self._write("pkgroot/__init__.py", "")
        self._write("pkgroot/impl/__init__.py", "")
        self._write("pkgroot/impl/foo.py", OLD_SOURCE)
        _git(self.root, "init", "-q", "-b", "main")
        for key, value in (
            ("user.email", "self-test@example.invalid"),
            ("user.name", "verify-module-split self-test"),
            ("commit.gpgsign", "false"),
            ("tag.gpgsign", "false"),
            ("gc.auto", "0"),
        ):
            _git(self.root, "config", key, value)
        _git(
            self.root,
            "add",
            "pkgroot/__init__.py",
            "pkgroot/impl/__init__.py",
            "pkgroot/impl/foo.py",
        )
        _git(self.root, "commit", "-q", "-m", "the module before the split")
        self.base = _git(self.root, "rev-parse", "HEAD").strip()

    def apply_split(self, files: dict[str, str]) -> None:
        """Delete the old module and write the package in its place."""
        (self.root / "pkgroot" / "impl" / "foo.py").unlink()
        for relative, text in files.items():
            self._write(f"pkgroot/impl/foo/{relative}", text)

    def run_tool(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--check-facade",
                str(self.package_dir),
                "--base-ref",
                self.base,
                "--repo-root",
                str(self.root),
                *extra,
            ],
            capture_output=True,
            text=True,
            env=ENV,
            cwd=str(self.root),
        )

    def split_and_run(self, files: dict[str, str], *extra: str):
        self.apply_split(files)
        return self.run_tool(*extra)


class SplitVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="verify-module-split-")
        self.addCleanup(self._tmp.cleanup)

    def fixture(self) -> Fixture:
        return Fixture(Path(self._tmp.name) / "repo")

    def test_a_legal_split_passes(self) -> None:
        result = self.fixture().split_and_run(SPLIT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("结论: 通过", result.stdout)

    def test_a_star_import_facade_passes(self) -> None:
        result = self.fixture().split_and_run(SPLIT_WITH_STAR)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_missing_export_is_reported_by_name(self) -> None:
        files = dict(SPLIT)
        files["__init__.py"] = '''\
"""The split package, minus one export."""

from .constants import DEFAULT_NAME, LIMIT
from .parsing import parse
from .rendering import build

__all__ = ["DEFAULT_NAME", "LIMIT", "build", "parse"]
'''
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("顶层符号不再导出: Widget", result.stdout)
        # The name still exists in the package, so the report must say where.
        self.assertIn("models", result.stdout)

    def test_signature_drift_is_reported_by_name(self) -> None:
        files = dict(SPLIT)
        files["models.py"] = SPLIT["models.py"].replace("width", "w")
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("形参变了: Widget.render", result.stdout)
        self.assertIn("(self, width, height)", result.stdout)
        self.assertIn("(self, w, height)", result.stdout)

    def test_a_new_deferred_import_is_reported_by_name(self) -> None:
        files = dict(SPLIT)
        files["rendering.py"] = '''\
"""Rendering helpers."""


def build(name, *, strict=False):
    import json
    import urllib.parse

    return json.dumps({"name": urllib.parse.quote(name), "strict": strict})
'''
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("多出函数内延迟 import: import urllib.parse", result.stdout)

    def test_a_new_public_name_is_reported(self) -> None:
        files = dict(SPLIT)
        files["__init__.py"] = SPLIT["__init__.py"].replace(
            '["DEFAULT_NAME"', '["EXTRA_THING", "DEFAULT_NAME"'
        )
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("EXTRA_THING", result.stdout)

    def test_an_old_path_missing_from_the_base_ref_cannot_be_judged(self) -> None:
        fixture = self.fixture()
        fixture.apply_split(SPLIT)
        result = fixture.run_tool("--old-file", "pkgroot/impl/nope.py")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot judge", result.stderr)

    def test_a_target_that_is_not_a_package_cannot_be_judged(self) -> None:
        fixture = self.fixture()
        fixture.apply_split(SPLIT)
        result = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--check-facade",
                str(fixture.package_dir / "models.py"),
                "--base-ref",
                fixture.base,
                "--repo-root",
                str(fixture.root),
            ],
            capture_output=True,
            text=True,
            env=ENV,
            cwd=str(fixture.root),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot judge", result.stderr)

    def test_runtime_identity_confirms_the_reexports(self) -> None:
        result = self.fixture().split_and_run(SPLIT, "--runtime-identity")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("运行时同一性: 校验", result.stdout)

    def test_runtime_identity_degrades_when_the_package_will_not_import(self) -> None:
        files = dict(SPLIT)
        files["models.py"] = (
            "import definitely_not_an_installed_module\n\n" + SPLIT["models.py"]
        )
        result = self.fixture().split_and_run(files, "--runtime-identity")
        # The static checks still stand on their own; the runtime one says so.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("运行时同一性: 包不能导入, 跳过", result.stdout)


class DeferredRuleAgreementTests(unittest.TestCase):
    """The counting rule belongs to backend/scripts/check_deferred_imports.py.

    This tool re-implements it because that file is written to be loaded by
    path, not imported as a package. Pin the two together so a change to one
    shows up as a failure here rather than as a disagreement in production.
    """

    SAMPLES = {
        "module body": "import os\nfrom typing import Any\n",
        "in a function": "def f():\n    import json\n",
        "in an async function": "async def f():\n    from a import b\n",
        "in a nested function": "def f():\n    def g():\n        import os\n",
        "in a class inside a function": "def f():\n    class C:\n        import os\n",
        "in a module-level class": "class C:\n    import os\n",
        "under a module-level if": "if True:\n    import os\n",
        "under a module-level try": (
            "try:\n    import os\nexcept ImportError:\n    os = None\n"
        ),
        "two names, one statement": "def f():\n    import a, b\n",
        "type-checking block": (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import os\n"
        ),
        "the pre-split module": OLD_SOURCE,
        "the split module": SPLIT["rendering.py"],
    }

    def test_counts_and_lines_match_the_backend_gate(self) -> None:
        gate_path = REPO_ROOT / DEFERRED_RULE_SOURCE
        if not gate_path.is_file():
            self.skipTest(f"{gate_path} is not here to compare against")
        gate = _load_by_path("_deferred_gate", gate_path)
        tool = _load_by_path("_verify_split", TOOL)

        for name, source in self.SAMPLES.items():
            with self.subTest(sample=name):
                mine = tool.read_source(textwrap.dedent(source), name).deferred
                theirs = gate.deferred_imports_in(textwrap.dedent(source))
                # Which statements count, and where they are. The wording
                # differs by design: the gate reports the source line, this
                # tool rebuilds the target with the alias dropped, so that a
                # renamed binding does not read as a new dependency.
                self.assertEqual(
                    [d.line for d in mine],
                    [d.line for d in theirs],
                    "the two implementations disagree on the same source",
                )


def _load_by_path(name: str, path: Path):
    """Import a file by path, registering it so dataclasses can find it.

    `exec_module` alone leaves the module out of `sys.modules`, and
    `@dataclass` then cannot look up the defining module's namespace.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


class UsageTests(unittest.TestCase):
    def test_a_missing_check_facade_cannot_be_judged(self) -> None:
        result = subprocess.run(
            [sys.executable, str(TOOL)],
            capture_output=True,
            text=True,
            env=ENV,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_the_dash_dash_self_test_flag_runs_this_file(self) -> None:
        if os.environ.get(INNER_MARK):
            # Already inside the run that this test would start.
            self.skipTest("nested self-test run")
        result = subprocess.run(
            [sys.executable, str(TOOL), "--self-test", "-v"],
            capture_output=True,
            text=True,
            env={**ENV, INNER_MARK: "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("test_a_legal_split_passes", result.stdout + result.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="test-verify-module-split.py",
        description="Run the offline self-test for verify-module-split.py.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args, extra = parser.parse_known_args(argv)
    suite = unittest.TestLoader().loadTestsFromTestCase(SplitVerificationTests)
    suite.addTests(
        unittest.TestLoader().loadTestsFromTestCase(DeferredRuleAgreementTests)
    )
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(UsageTests))
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
