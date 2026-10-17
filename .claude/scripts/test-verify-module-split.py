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
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent / "verify-module-split.py"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFERRED_RULE_SOURCE = Path("backend/scripts/check_deferred_imports.py")

#: Set while the suite is being run by `verify-module-split.py --self-test`, so
#: the test that checks that flag does not start a run of its own.
INNER_MARK = "VERIFY_MODULE_SPLIT_SELF_TEST_INNER"

#: The variables that tell git it is working somewhere else. They are dropped
#: rather than inherited: under a pre-commit hook or a CI step they point at the
#: checkout being tested, and `git -C <fixture>` would then operate on that
#: checkout instead of on the throwaway repository.
GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)

ENV = {
    **{k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARS},
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
}

#: Scratch directories this suite parks copies of its fixtures in. A run that
#: dies before its cleanup leaves one behind, and that copy holds a fixture's
#: text under the package name the tool refuses to compare past — so it would
#: block every later run of this suite, and a `TMPDIR` of the suite's own does
#: not help, because the tool looks in `/tmp` as well. Anything older than a
#: run is cleared first; a copy young enough to belong to a concurrent run is
#: left alone.
SCRATCH_PREFIXES = ("shadow-copy-", "shadow-other-")
SCRATCH_STALE_SECONDS = 900


def _clear_stale_scratch() -> None:
    now = time.time()
    for prefix in SCRATCH_PREFIXES:
        for directory in Path(tempfile.gettempdir()).glob(prefix + "*"):
            try:
                age = now - directory.stat().st_mtime
            except OSError:
                continue
            if age > SCRATCH_STALE_SECONDS:
                shutil.rmtree(directory, ignore_errors=True)


def setUpModule() -> None:
    _clear_stale_scratch()

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
    import urllib.parse

    return json.dumps({"name": urllib.parse.quote(name), "strict": strict})


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
    import json, urllib.parse

    return json.dumps({"name": urllib.parse.quote(name), "strict": strict})
''',
}

#: `from .constants import *` reaches the same two names as naming them, so it
#: is a legal facade too — the tool has to follow the star. The facade's own
#: `__all__` deliberately omits those two: naming them there would make the
#: star unnecessary and the test would pass with the star ignored.
SPLIT_WITH_STAR = {
    **SPLIT,
    "constants.py": '''\
"""Constants."""

DEFAULT_NAME = "widget"
LIMIT = 10

__all__ = ["DEFAULT_NAME", "LIMIT"]
''',
    "__init__.py": '''\
"""The split package, star-flavoured."""

from .constants import *
from .models import Widget
from .parsing import parse
from .rendering import build

__all__ = ["Widget", "build", "parse"]
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

    def __init__(self, root: Path, old_source: str = OLD_SOURCE) -> None:
        self.root = root
        self.package_dir = root / "pkgroot" / "impl" / "foo"
        self._write_base_and_commit(old_source)

    def _write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_base_and_commit(self, old_source: str) -> None:
        self._write("pkgroot/__init__.py", "")
        self._write("pkgroot/impl/__init__.py", "")
        self._write("pkgroot/impl/foo.py", old_source)
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


class CallerContractTests(unittest.TestCase):
    """Each broken consumer must fail even when the facade-only checks pass."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="split-callers-")
        self.addCleanup(tmp.cleanup)
        self.fixture = Fixture(Path(tmp.name) / "repo", "from json import loads\n")
        # Exercise the actual app import root, not a backend.app lookalike.
        root = self.fixture.root
        (root / "backend").mkdir()
        (root / "pkgroot").rename(root / "backend/app")
        _git(root, "add", "pkgroot", "backend/app")
        _git(root, "commit", "-qm", "put the fixture under the app import root")
        self.fixture.base = _git(root, "rev-parse", "HEAD").strip()
        self.fixture.package_dir = root / "backend/app/impl/foo"
        (root / "backend/app/impl/foo.py").unlink()
        self.write_package({"__init__.py": "", "worker.py": "from json import loads\n"})

    def write_package(self, files: dict[str, str]) -> None:
        for path, source in files.items():
            self.fixture._write("backend/app/impl/foo/" + path, source)

    def assert_result(self, code: int, *messages: str) -> None:
        result = self.fixture.run_tool()
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        for message in messages:
            self.assertIn(message, result.stdout + result.stderr)

    def test_cross_package_imports_fail_and_retargeting_repairs_them(self) -> None:
        for directory in ("cli", "backend/scripts", ".claude/scripts", "scripts"):
            with self.subTest(directory=directory):
                path = directory + "/consumer.py"
                self.fixture._write(path, "from app.impl.foo import loads as decode\n")
                self.assert_result(1, "调用方导入断裂", path, "loads")
                self.fixture._write(
                    path, "from app.impl.foo.worker import loads as decode\n"
                )
                self.assert_result(0)

    def test_reexport_of_an_imported_name_still_serves_callers(self) -> None:
        self.fixture._write("cli/consumer.py", "from app.impl.foo import loads\n")
        self.write_package({"__init__.py": "from .worker import loads\n"})
        self.assert_result(0)
        self.write_package(
            {"__init__.py": "from .worker import nonexistent as loads\n"}
        )
        self.assert_result(1, "调用方导入断裂", "loads")

    def test_private_imports_and_relative_imports_are_checked(self) -> None:
        self.fixture._write(
            "backend/app/impl/consumer.py", "from .foo import _decode\n"
        )
        self.assert_result(1, "调用方导入断裂", "_decode")
        self.write_package({"__init__.py": "from .worker import loads as _decode\n"})
        self.assert_result(0)

    def test_old_reason_on_a_retargeted_import_fails_then_passes(self) -> None:
        path = "backend/tests/test_decode.py"
        source = (
            "def decode():\n"
            "    # deferred-import: tests replace this name on app.impl.foo\n"
            "    from app.impl.foo.worker import loads\n"
            "    return loads('{}')\n"
        )
        self.fixture._write(path, source)
        self.assert_result(1, "理由句失指", path)
        self.fixture._write(
            path,
            source.replace("name on app.impl.foo\n", "name on app.impl.foo.worker\n"),
        )
        self.assert_result(0)

    def test_reason_strings_and_comments_name_a_removed_patch_target(self) -> None:
        path = "backend/tests/test_decode.py"
        for source in (
            'reason = "tests patch app.impl.foo.loads"\n',
            "# tests replace app.impl.foo.loads\n",
        ):
            self.fixture._write(path, source)
            self.assert_result(1, "理由句失指", path)
            self.fixture._write(path, source.replace("foo.loads", "foo.worker.loads"))
            self.assert_result(0)

    def test_missing_patch_paths_fail_and_new_paths_pass(self) -> None:
        path = "backend/tests/test_decode.py"
        for source in (
            'monkeypatch.setattr("app.impl.foo.loads", fake)\n',
            'from app.impl import foo\nsetattr(foo, "loads", fake)\n',
            'import app.impl.foo as old\nmonkeypatch.setattr(old, "loads", fake)\n',
            'from unittest.mock import patch\npatch("app.impl.foo.loads", fake)\n',
        ):
            with self.subTest(source=source):
                self.fixture._write(path, source)
                self.assert_result(1, "补丁目标断裂", path, "loads")
                self.fixture._write(
                    path,
                    source.replace("app.impl.foo", "app.impl.foo.worker").replace(
                        "import foo\n",
                        "import foo\nfrom app.impl.foo import worker as foo\n",
                    ),
                )
                self.assert_result(0)

    def test_reexported_globals_do_not_keep_patches_effective(self) -> None:
        self.write_package(
            {
                "__init__.py": "from .worker import loads, decode\n",
                "worker.py": "from json import loads\ndef decode():\n    return loads('{}')\n",
            }
        )
        # The old callable looked up loads in the old module's globals.
        old = self.fixture.root / "backend/app/impl/foo.py"
        old.write_text(
            "from json import loads\ndef decode():\n    return loads('{}')\n"
        )
        _git(self.fixture.root, "add", "backend/app/impl/foo.py")
        _git(self.fixture.root, "commit", "-qm", "record the old callable")
        self.fixture.base = _git(self.fixture.root, "rev-parse", "HEAD").strip()
        old.unlink()
        path = "backend/tests/test_decode.py"
        self.fixture._write(path, 'monkeypatch.setattr("app.impl.foo.loads", fake)\n')
        self.assert_result(1, "补丁不再作用于搬走的定义", "decode", "loads")
        self.fixture._write(
            path, 'monkeypatch.setattr("app.impl.foo.worker.loads", fake)\n'
        )
        self.assert_result(0)

    def test_logger_selection_fails_when_the_emitter_name_moves(self) -> None:
        path = "backend/tests/test_logging.py"
        self.write_package(
            {"worker.py": "import logging\nLOG = logging.getLogger(__name__)\n"}
        )
        self.fixture._write(
            path,
            'import logging\nmonkeypatch.setattr(logging.getLogger("app.impl.foo"), "warning", fake)\n',
        )
        self.assert_result(1, "日志名称已迁移", path)
        self.fixture._write(
            path,
            'import logging\nmonkeypatch.setattr(logging.getLogger("app.impl.foo.worker"), "warning", fake)\n',
        )
        self.assert_result(0)
        self.write_package(
            {"worker.py": 'import logging\nLOG = logging.getLogger("app.impl.foo")\n'}
        )
        self.fixture._write(
            path,
            'import logging\nmonkeypatch.setattr(logging.getLogger("app.impl.foo"), "warning", fake)\n',
        )
        self.assert_result(0)

    def test_unrelated_strings_and_logger_hierarchies_are_not_patches(self) -> None:
        self.fixture._write("cli/consumer.py", 'text = "app.impl.foo.loads"\n')
        self.write_package(
            {"worker.py": "import logging\nLOG = logging.getLogger(__name__)\n"}
        )
        self.fixture._write(
            "backend/tests/test_logging.py",
            'import logging\nlogging.getLogger("app.impl")\n',
        )
        self.assert_result(0)

    def test_nested_reexports_resolve_relative_to_their_own_package(self) -> None:
        self.write_package(
            {
                "__init__.py": "from .nested import loads\n",
                "nested/__init__.py": "from ..worker import loads\n",
            }
        )
        self.fixture._write("cli/consumer.py", "from app.impl.foo import loads\n")
        self.assert_result(0)
        self.write_package(
            {"nested/__init__.py": "from ..worker import missing as loads\n"}
        )
        self.assert_result(1, "调用方导入断裂", "cli/consumer.py")

    def test_parameter_and_reassignment_shadow_a_module_alias(self) -> None:
        self.fixture._write(
            "backend/tests/test_decode.py",
            (
                "from app.impl import foo\n"
                "def first(foo, monkeypatch):\n"
                "    monkeypatch.setattr(foo, 'loads', fake)\n"
                "def second(monkeypatch):\n"
                "    foo = object()\n"
                "    monkeypatch.setattr(foo, 'loads', fake)\n"
            ),
        )
        self.assert_result(0)
        self.fixture._write(
            "backend/tests/test_decode.py",
            (
                "from app.impl import foo\n"
                "def first(foo, monkeypatch):\n"
                "    monkeypatch.setattr(foo, 'loads', fake)\n"
                "def second(monkeypatch):\n"
                "    monkeypatch.setattr(foo, 'loads', fake)\n"
            ),
        )
        self.assert_result(1, "补丁目标断裂")

    def test_local_names_do_not_turn_reexports_into_broken_patches(self) -> None:
        old = self.fixture.root / "backend/app/impl/foo.py"
        source = "from json import loads\ndef decode(loads):\n    return loads('{}')\n"
        old.write_text(source)
        _git(self.fixture.root, "add", "backend/app/impl/foo.py")
        _git(
            self.fixture.root, "commit", "-qm", "record a parameter with the same name"
        )
        self.fixture.base = _git(self.fixture.root, "rev-parse", "HEAD").strip()
        old.unlink()
        self.write_package(
            {"__init__.py": "from .worker import loads, decode\n", "worker.py": source}
        )
        self.fixture._write(
            "backend/tests/test_decode.py",
            'monkeypatch.setattr("app.impl.foo.loads", fake)\n',
        )
        self.assert_result(0)

    def test_logger_aliases_are_checked_but_parent_handlers_remain_valid(self) -> None:
        self.write_package(
            {"worker.py": "import logging\nLOG = logging.getLogger(__name__)\n"}
        )
        path = "backend/tests/test_logging.py"
        self.fixture._write(
            path,
            (
                "from logging import getLogger as logger_for\n"
                'logger = logger_for("app.impl.foo")\n'
                'monkeypatch.setattr(logger, "warning", fake)\n'
            ),
        )
        self.assert_result(1, "日志名称已迁移")
        self.fixture._write(
            path,
            ('import logging\nlogging.getLogger("app.impl.foo").addHandler(handler)\n'),
        )
        self.assert_result(0)

    def test_package_relative_imports_do_not_need_the_package_name_in_source(
        self,
    ) -> None:
        for relative, source in (
            ("consumer.py", "from . import loads\n"),
            ("nested/__init__.py", "from .. import loads\n"),
        ):
            with self.subTest(relative=relative):
                self.write_package({"__init__.py": "", relative: source})
                self.assert_result(1, "调用方导入断裂", "app.impl.foo.loads")
                self.write_package({"__init__.py": "from .worker import loads\n"})
                self.assert_result(0)

    def test_patch_object_can_supply_the_mock_without_a_third_argument(self) -> None:
        path = "backend/tests/test_decode.py"
        for extra in ("", ", return_value={}"):
            with self.subTest(extra=extra):
                self.write_package({"__init__.py": ""})
                source = (
                    "from app.impl import foo\n"
                    "from unittest.mock import patch\n"
                    f"with patch.object(foo, 'loads'{extra}):\n    pass\n"
                )
                self.fixture._write(path, source)
                self.assert_result(1, "补丁目标断裂", "app.impl.foo.loads")
                self.write_package({"__init__.py": "from .worker import loads\n"})
                self.assert_result(0)

    def test_patching_parent_logger_handlers_still_captures_child_records(self) -> None:
        self.write_package(
            {
                "__init__.py": "from .worker import emit\n",
                "worker.py": (
                    "import logging\nLOG = logging.getLogger(__name__)\n"
                    "def emit():\n    LOG.warning('hello')\n"
                ),
            }
        )
        source = (
            "import logging\nfrom unittest.mock import patch\n"
            "from app.impl.foo import emit\n"
            "seen = []\n"
            "class Capture(logging.Handler):\n"
            "    def emit(self, record):\n        seen.append(record.getMessage())\n"
            "with patch.object(logging.getLogger('app.impl.foo'), 'handlers', [Capture()]):\n"
            "    emit()\n"
            "assert seen == ['hello'], seen\n"
        )
        self.fixture._write("scripts/consumer.py", source)
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); " + source,
                str(self.fixture.root / "backend"),
            ],
            capture_output=True,
            text=True,
            env=ENV,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_result(0)

    def test_an_unreadable_relevant_caller_is_not_a_pass(self) -> None:
        self.fixture._write(
            "cli/consumer.py",
            "from app.impl.foo import loads\nthis is invalid python\n",
        )
        self.assert_result(2, "cannot judge", "cli/consumer.py")


class SplitVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="verify-module-split-")
        self.addCleanup(self._tmp.cleanup)

    def fixture(self, old_source: str = OLD_SOURCE) -> Fixture:
        return Fixture(Path(self._tmp.name) / "repo", old_source)

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
        self.assertIn("旧 (self, width, height=)", result.stdout)
        self.assertIn("包 (self, w, height=)", result.stdout)

    def test_a_new_deferred_import_is_reported_by_name(self) -> None:
        files = dict(SPLIT)
        files["rendering.py"] = '''\
"""Rendering helpers."""


def build(name, *, strict=False):
    import json, urllib.parse
    import mimetypes

    return json.dumps({"name": urllib.parse.quote(mimetypes.guess_type(name))})
'''
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("多出函数内延迟 import: mimetypes", result.stdout)
        # The two that were carried over are not reported.
        self.assertNotIn("多出函数内延迟 import: json", result.stdout)
        self.assertNotIn("多出函数内延迟 import: urllib.parse", result.stdout)

    def test_a_deferred_import_that_only_moved_still_passes(self) -> None:
        """Order and grouping are not part of what a split changes."""
        files = dict(SPLIT)
        files["rendering.py"] = SPLIT["rendering.py"].replace(
            "    import json, urllib.parse\n",
            "    import urllib.parse\n    import json\n",
        )
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_second_deferred_import_of_one_module_is_reported(self) -> None:
        files = dict(SPLIT)
        files["rendering.py"] = '''\
"""Rendering helpers."""


def build(name, *, strict=False):
    import json, urllib.parse

    return json.dumps({"name": urllib.parse.quote(name), "strict": strict})


def quote(name):
    import json

    return json.dumps(name)
'''
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("函数内延迟 import 变多了: json", result.stdout)
        self.assertIn("旧 1 处 → 包 2 处", result.stdout)

    def test_a_positional_only_marker_is_drift(self) -> None:
        """`def f(a, b)` and `def f(a, /, b)` take different calls."""
        files = dict(SPLIT)
        files["models.py"] = SPLIT["models.py"].replace(
            "def render(self, width, height=0):",
            "def render(self, width, /, height=0):",
        )
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "旧 (self, width, height=) → 包 (self, width, /, height=)", result.stdout
        )

    def test_a_dropped_default_is_drift(self) -> None:
        files = dict(SPLIT)
        files["models.py"] = SPLIT["models.py"].replace(
            "def render(self, width, height=0):", "def render(self, width, height):"
        )
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "旧 (self, width, height=) → 包 (self, width, height)", result.stdout
        )

    def test_turning_a_function_async_is_drift(self) -> None:
        files = dict(SPLIT)
        files["rendering.py"] = SPLIT["rendering.py"].replace(
            "def build(", "async def build("
        )
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("变了同步/异步: build", result.stdout)

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

    def test_a_dotted_target_that_is_not_importable_cannot_be_judged(self) -> None:
        fixture = self.fixture()
        fixture.apply_split(SPLIT)
        result = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--check-facade",
                "pkgroot.impl.foo",
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
        self.assertIn("cannot be imported as a module", result.stderr)

    def test_a_file_that_is_not_utf8_cannot_be_judged(self) -> None:
        fixture = self.fixture()
        fixture.apply_split(SPLIT)
        # A latin-1 source file with no encoding declaration: readable by the
        # interpreter, not by a UTF-8 read. Saying nothing would read as a pass.
        (fixture.package_dir / "models.py").write_bytes(
            b'"""Mod\xe8le."""\n\n\nclass Widget:\n    pass\n'
        )
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot judge", result.stderr)


class RuntimeShapeTests(unittest.TestCase):
    """Cases where the old module's source over- or under-states its surface."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="verify-module-split-")
        self.addCleanup(self._tmp.cleanup)

    def fixture(self, old_source: str = OLD_SOURCE) -> Fixture:
        return Fixture(Path(self._tmp.name) / "repo", old_source)

    def test_a_computed_all_is_said_out_loud(self) -> None:
        """The names it declares cannot be read, so the run must not imply it did."""
        old = '''\
"""A module that computes its __all__."""


def run():
    return 1


__all__ = sorted(["run"])
'''
        files = {
            "__init__.py": 'from .impl import run\n\n__all__ = ["run"]\n',
            "impl.py": "def run():\n    return 1\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("__all__ 不是字面量", result.stdout)

    def test_a_deleted_name_is_not_required_of_the_package(self) -> None:
        old = '''\
"""A module that drops a name before it finishes loading."""

GONE = 1
del GONE


def run():
    return 1
'''
        files = {
            "__init__.py": 'from .impl import run\n\n__all__ = ["run"]\n',
            "impl.py": "def run():\n    return 1\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_name_bound_only_under_type_checking_is_not_required(self) -> None:
        old = '''\
"""A module that binds a name the type checker alone sees."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    TYPING_ONLY = 1


def run():
    return 1
'''
        files = {
            "__init__.py": 'from .impl import run\n\n__all__ = ["run"]\n',
            "impl.py": "def run():\n    return 1\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_relative_import_one_level_deeper_still_matches(self) -> None:
        """The file moved down a level, so `from .x` became `from ..x`."""
        old = '''\
"""A module whose function bodies reach a sibling relatively."""

from .sibling import helper


def run():
    from .sibling import helper as h

    return h()
'''
        files = {
            "__init__.py": 'from .runner import run\n\n__all__ = ["run"]\n',
            "runner.py": """\
def run():
    from ..sibling import helper as h

    return h()
""",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_relative_import_that_keeps_its_level_is_reported(self) -> None:
        """Same file as above, dots not rewritten: now a different module."""
        old = '''\
"""A module whose function bodies reach a sibling relatively."""

from .sibling import helper


def run():
    from .sibling import helper as h

    return h()
'''
        files = {
            "__init__.py": 'from .runner import run\n\n__all__ = ["run"]\n',
            "runner.py": """\
def run():
    from .sibling import helper as h

    return h()
""",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("多出函数内延迟 import: pkgroot.impl.foo.sibling", result.stdout)

    def test_a_facade_that_re_exports_absolutely_still_passes(self) -> None:
        """How every split in this repository writes its facade."""
        files = dict(SPLIT)
        files["__init__.py"] = '''\
"""The split package, re-exporting by absolute path."""

from pkgroot.impl.foo.constants import DEFAULT_NAME as DEFAULT_NAME
from pkgroot.impl.foo.constants import LIMIT as LIMIT
from pkgroot.impl.foo.models import Widget as Widget
from pkgroot.impl.foo.parsing import parse as parse
from pkgroot.impl.foo.rendering import build as build

__all__ = ["DEFAULT_NAME", "LIMIT", "Widget", "build", "parse"]
'''
        result = self.fixture().split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_method_lifted_to_a_module_function_is_a_note(self) -> None:
        old = '''\
"""A class whose private helper is about to become a function."""


class Widget:
    def render(self, width):
        return self._paint(width)

    def _paint(self, width):
        return str(width)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self, width):
        return _paint(width)


from .painting import _paint  # noqa: E402
""",
            "painting.py": "def _paint(width):\n    return str(width)\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("方法被提到模块级: Widget._paint", result.stdout)

    def test_a_namesake_with_a_different_parameter_list_is_not_a_lift(self) -> None:
        """A name collision is not a lift: the parameters are not the method's."""
        old = '''\
"""A class whose private helper is about to be dropped."""


class Widget:
    def render(self, width):
        return str(width)

    def _paint(self, width):
        return str(width)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self, width):
        return str(width)
""",
            # An unrelated helper that happens to share the name, and is called
            # as often as the lost method ever was. Every real package here has
            # a few of these; none of them is the lost method.
            "painting.py": """\
def _paint(colour):
    return colour


def paint_all(items):
    return [_paint(item) for item in items]
""",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("符号没了: Widget._paint", result.stdout)

    def test_a_lift_that_keeps_the_instance_is_a_note(self) -> None:
        """This repository writes a lift both ways.

        `_require_screen` keeps the instance as its first parameter and spells it
        `hub`; the callers reach it through its module as `painting._paint(self,
        width)`. The parameter list is what says the method moved.
        """
        old = '''\
"""A class whose private helper is about to become a function."""


class Widget:
    def render(self, width):
        return self._paint(width)

    def _paint(self, width):
        return str(width)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
from . import painting


class Widget:
    def render(self, width):
        return painting._paint(self, width)
""",
            "painting.py": "def _paint(hub, width):\n    return str(width)\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("方法被提到模块级: Widget._paint", result.stdout)

    def test_a_lift_whose_callers_only_pass_it_as_a_value_is_a_note(self) -> None:
        """`partial(_paint, 1)` is a caller even though it never calls it.

        Judging by the call sites instead of the parameter list is what would
        report this lift as a lost symbol.
        """
        old = '''\
"""A class whose private helper is about to become a function."""


class Widget:
    def render(self, width):
        return self._paint(width)

    def _paint(self, width):
        return str(width)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
import functools
from .painting import _paint


class Widget:
    def render(self, width):
        return self._paint(width)


apply = functools.partial(_paint, 1)
all_of = list(map(_paint, [1, 2]))
""",
            "painting.py": "def _paint(width):\n    return str(width)\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("方法被提到模块级: Widget._paint", result.stdout)

    def test_a_namesake_with_an_extra_parameter_is_not_a_lift(self) -> None:
        """Same name and arity, different parameters: a different function."""
        old = '''\
"""A class whose private helper is about to be dropped."""


class Widget:
    def render(self, width):
        return str(width)

    def _paint(self, width):
        return str(width)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self, width):
        return str(width)
""",
            "painting.py": "def _paint(width, height):\n    return str(width)\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("符号没了: Widget._paint", result.stdout)

    def test_a_sync_function_is_not_the_lift_of_an_async_method(self) -> None:
        """A lift keeps `async`; dropping it is a break the callers see."""
        old = '''\
"""A class whose async private helper is about to be dropped."""


class Widget:
    async def render(self, width):
        return await self._paint(width)

    async def _paint(self, width):
        return str(width)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    async def render(self, width):
        return await _paint(width)


from .painting import _paint  # noqa: E402
""",
            "painting.py": "def _paint(width):\n    return str(width)\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("符号没了: Widget._paint", result.stdout)

    def test_a_namesake_with_the_same_shape_is_a_note(self) -> None:
        """The one case the parameter list cannot settle.

        A module-level function that shares the name *and* the parameter list is
        a lift and a namesake alike to anything this can read. It gets a note a
        person can check; calling every one of them a lost symbol instead is how
        the checker red-lights the repo's own splits.
        """
        old = '''\
"""A class whose private helper is about to be dropped."""


class Widget:
    def render(self, width):
        return self._paint(width)

    def _paint(self, width):
        return str(width)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self, width):
        return str(width)
""",
            "painting.py": "def _paint(width):\n    return width\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("方法被提到模块级: Widget._paint", result.stdout)

    def test_a_self_only_method_dropped_is_not_excused_by_a_namesake(self) -> None:
        """A method that took only `self` leaves no shape behind to match.

        `def _reset(value)` shares the name and the arity with `_reset(self)` and
        nothing else. Reading that as the lift is how a deleted method goes out
        unnoticed.
        """
        old = '''\
"""A class whose private helper is about to be dropped."""


class Widget:
    def render(self):
        return self._reset()

    def _reset(self):
        return 1
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self):
        return 1
""",
            "painting.py": "def _reset(value):\n    return value\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("符号没了: Widget._reset", result.stdout)

    def test_a_self_only_method_dropped_survives_no_namesake_either(self) -> None:
        """Arity zero is not a parameter list the lost method could have had."""
        old = '''\
"""A class whose private helper is about to be dropped."""


class Widget:
    def render(self):
        return self._reset()

    def _reset(self):
        return 1
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self):
        return 1
""",
            "painting.py": "def _reset():\n    return 1\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("符号没了: Widget._reset", result.stdout)

    def test_a_lift_that_drops_nothing_is_the_same_call(self) -> None:
        """A lift keeps the parameter list, so an unchanged one is the lift.

        Neither helper loses a parameter on the way out: `_unavailable` takes
        none, and `_label` keeps the instance as its only one. Nothing is left
        to explain, and both read as lifts rather than as lost symbols.
        """
        old = '''\
"""A class whose private helpers are about to become functions."""


class Widget:
    @staticmethod
    def _unavailable():
        return "n/a"

    def render(self):
        return self._label() + _unavailable()

    def _label(self):
        return "w"
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self):
        return _label(self) + _unavailable()


from .painting import _label, _unavailable  # noqa: E402
""",
            "painting.py": (
                'def _unavailable():\n    return "n/a"\n\n\n'
                'def _label(self):\n    return "w"\n'
            ),
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("方法被提到模块级: Widget._unavailable", result.stdout)
        self.assertIn("方法被提到模块级: Widget._label", result.stdout)

    def test_a_keyword_only_method_is_not_excused_by_a_vararg_namesake(self) -> None:
        """`*` and `*args` take a different number of positionals.

        `_paint(width, *args, strict)` accepts an extra positional that
        `_paint(self, width, *, strict)` never did, so it is not this method's
        lift; the method is gone and the namesake only shares its name.
        """
        old = '''\
"""A class whose private helper is about to be dropped."""


class Widget:
    def render(self, width):
        return self._paint(width, strict=True)

    def _paint(self, width, *, strict):
        return str(width)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self, width):
        return str(width)
""",
            "painting.py": "def _paint(width, *args, strict):\n    return str(width)\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("符号没了: Widget._paint", result.stdout)

    def test_a_lift_may_rename_a_vararg(self) -> None:
        """No caller can name a vararg, so a lift is free to rename one."""
        old = '''\
"""A class whose private helper is about to become a function."""


class Widget:
    def render(self, *colours):
        return self._paint(*colours)

    def _paint(self, *colours):
        return str(colours)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self, *colours):
        return _paint(*colours)


from .painting import _paint  # noqa: E402
""",
            "painting.py": "def _paint(*values):\n    return str(values)\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("方法被提到模块级: Widget._paint", result.stdout)

    def test_a_lift_may_drop_a_positional_only_instance(self) -> None:
        """`/` closes the positional-only group; it is not a parameter name."""
        old = '''\
"""A class whose private helper is about to become a function."""


class Widget:
    def render(self, width):
        return self._paint(width)

    def _paint(self, /, width):
        return str(width)
'''
        files = {
            "__init__.py": 'from .models import Widget\n\n__all__ = ["Widget"]\n',
            "models.py": """\
class Widget:
    def render(self, width):
        return _paint(width)


from .painting import _paint  # noqa: E402
""",
            "painting.py": "def _paint(width):\n    return str(width)\n",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("方法被提到模块级: Widget._paint", result.stdout)

    def test_a_logger_is_not_required_of_the_package(self) -> None:
        old = '''\
"""A module with the logger every module in this repository has."""

import logging

logger = logging.getLogger(__name__)


def run():
    logger.info("x")
'''
        files = {
            "__init__.py": 'from .impl import run\n\n__all__ = ["run"]\n',
            "impl.py": """\
import logging

logger = logging.getLogger(__name__)


def run():
    logger.info("x")
""",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_constant_built_by_a_call_is_still_required(self) -> None:
        """Only the logger factory is set aside; a value built by a call is not."""
        old = '''\
"""A module with a constant that happens to be built by a call."""

import logging

LINK_ONLY_PROVIDERS = frozenset({"github_app"})
logger = logging.getLogger(__name__)


def run():
    return LINK_ONLY_PROVIDERS
'''
        files = {
            "__init__.py": 'from .impl import run\n\n__all__ = ["run"]\n',
            "impl.py": """\
def run():
    return frozenset({"github_app"})
""",
        }
        result = self.fixture(old).split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("顶层符号不再导出: LINK_ONLY_PROVIDERS", result.stdout)

    def test_a_star_import_that_does_not_cover_the_name_is_reported(self) -> None:
        """`from .constants import *` binds `constants.__all__`, nothing else."""
        files = dict(SPLIT_WITH_STAR)
        files["constants.py"] = SPLIT_WITH_STAR["constants.py"].replace(
            '__all__ = ["DEFAULT_NAME", "LIMIT"]', '__all__ = ["LIMIT"]'
        )
        result = self.fixture(OLD_SOURCE).split_and_run(files)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("顶层符号不再导出: DEFAULT_NAME", result.stdout)


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


#: Two sibling modules of one directory, before and after a name walks from the
#: first to the second. `keep` and `other` stay put so the change is a move and
#: not a rewrite, and `_helper` is the private name the 2026-10-08 move carried.
MOVE_BEFORE_FOO = '''\
def keep(value):
    return value


def _helper(value):
    return value + 1
'''

MOVE_AFTER_FOO = '''\
def keep(value):
    return value
'''

MOVE_BEFORE_BAR = '''\
def other(value):
    return value
'''

MOVE_AFTER_BAR = '''\
def other(value):
    return value


def _helper(value):
    return value + 2
'''

#: A script outside the package, reading the moved name from the file it left.
#: `backend/scripts/` is the directory `[tool.pyright]`'s `include = ["app"]`
#: leaves unchecked, which is why nothing local saw the 2026-10-08 move.
STALE_CALLER = "from app.foo import _helper\n"
MOVED_CALLER = "from app.bar import _helper\n"

#: A module that forwards a sibling's name and also loses one of its own. The
#: caller reads `shared` through `app.foo` and keeps working after the change,
#: so a check that resolves only what the file itself types into a break about
#: an import that never stopped being valid.
EXPORT_BEFORE_FOO = '''\
from .base import shared


def keep(value):
    return value


def removed(value):
    return value
'''

EXPORT_AFTER_FOO = '''\
from .base import shared


def keep(value):
    return value
'''

EXPORT_BASE = '''\
def shared(value):
    return value
'''

SHARED_CALLER = "from app.foo import shared\n"

#: A name that also happens to be a module beside the file it leaves. The package
#: holds a module named `helper`, so a view that copies every neighbour in under
#: its bare name lets `from app.foo import helper` walk into `app.helper` and pass
#: a name `app.foo` no longer offers.
NAMESAKE_BEFORE_FOO = '''\
def keep(value):
    return value


def helper(value):
    return value
'''

NAMESAKE_AFTER_FOO = '''\
def keep(value):
    return value
'''

NAMESAKE_SIBLING = '''\
"""`app/helper.py`: a module, and not a name `app.foo` hands out."""


def other(value):
    return value
'''

NAMESAKE_CALLER = "from app.foo import helper\n"

#: A module two levels under the package root, forwarding a sibling's name. The
#: file spells that import `y`, which is a name only the package around it can
#: place on a file — `app/domain/y.py` here, not `app/y.py`.
NESTED_BEFORE_X = '''\
from .y import z


def keep(value):
    return value


def removed(value):
    return value
'''

NESTED_AFTER_X = '''\
from .y import z


def keep(value):
    return value
'''

NESTED_SIBLING = '''\
def z(value):
    return value
'''

NESTED_CALLER = "from app.domain.x import z\n"

#: The package's own `__init__.py` losing a name, while a caller reads a
#: submodule through the package. `from app.helper import other` reads the file
#: `app/helper.py`, which is in the package and not in the module: a view that
#: carries only the module cannot walk down to it, and every caller naming a
#: submodule of the package would read as broken.
FACADE_BEFORE = '''\
def gone(value):
    return value
'''

FACADE_AFTER = ""

FACADE_SUBMODULE_CALLER = "from app.helper import other\n"
FACADE_NAME_CALLER = "from app import gone\n"

#: A definition that left for a sibling while this module kept the name as a
#: re-export. The import still binds, but the re-export preserves the object
#: and not the globals dictionary the moved function reads: a patch spelled
#: against this module no longer reaches the definition.
PATCH_BEFORE_FOO = '''\
from json import loads


def decode():
    return loads('{}')


def gone(value):
    return value
'''

PATCH_AFTER_FOO = '''\
from .bar import decode, loads
'''

PATCH_BEFORE_BAR = '''\
def other(value):
    return value
'''

PATCH_AFTER_BAR = '''\
from json import loads


def decode():
    return loads('{}')
'''

PATCH_CALLER = '''\
def test_it(monkeypatch):
    monkeypatch.setattr("app.foo.loads", fake)
'''

#: A name the module both imports and defines: the import is the fast path and
#: the definition is the fallback, so a caller keeps working either way. A view
#: that keeps the import in the module's own record but drops the file it names
#: reads that caller's import as broken.
FALLBACK_BEFORE_FOO = '''\
try:
    from .fast import shared
except ImportError:
    def shared(value):
        return value


def gone(value):
    return value
'''

FALLBACK_AFTER_FOO = '''\
try:
    from .fast import shared
except ImportError:
    def shared(value):
        return value
'''

FALLBACK_FAST = '''\
def shared(value):
    return value
'''

FALLBACK_CALLER = "from app.foo import shared\n"

#: A package whose facade loses a name while a caller reads a name that is not
#: there out of its submodule. The walk has to keep descending through the
#: facade into `app/pkg/sub.py`, or the missing name reads as present.
SUBPACKAGE_INIT_BEFORE = '''\
def old_name(value):
    return value


from .sub import shared
'''

SUBPACKAGE_INIT_AFTER = '''\
from .sub import shared
'''

SUBPACKAGE_SUB = '''\
def shared(value):
    return value
'''

SUBPACKAGE_CALLER = "from app.pkg.sub import gone\n"


class MoveFixture:
    """A throwaway repository whose two modules are siblings under `app`."""

    def __init__(
        self,
        root: Path,
        commit_bar: bool = True,
        relative_export: bool = False,
        files: dict[str, str] | None = None,
    ) -> None:
        self.root = root
        written = {
            "backend/app/__init__.py": "",
            "backend/app/foo.py": (
                EXPORT_BEFORE_FOO if relative_export else MOVE_BEFORE_FOO
            ),
            "backend/scripts/tool.py": (
                SHARED_CALLER if relative_export else STALE_CALLER
            ),
        }
        if relative_export:
            written["backend/app/base.py"] = EXPORT_BASE
        if commit_bar:
            written["backend/app/bar.py"] = MOVE_BEFORE_BAR
        written.update(files or {})
        for relative, text in written.items():
            self._write(relative, text)
        _git(root, "init", "-q", "-b", "main")
        for key, value in (
            ("user.email", "self-test@example.invalid"),
            ("user.name", "verify-module-split self-test"),
            ("commit.gpgsign", "false"),
            ("tag.gpgsign", "false"),
            ("gc.auto", "0"),
        ):
            _git(root, "config", key, value)
        _git(root, "add", *written)
        _git(root, "commit", "-q", "-m", "both modules before the move")
        self.base = _git(root, "rev-parse", "HEAD").strip()

    def _write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def caller(self, source: str) -> None:
        self._write("backend/scripts/tool.py", source)

    def move(self) -> None:
        self._write("backend/app/foo.py", MOVE_AFTER_FOO)
        self._write("backend/app/bar.py", MOVE_AFTER_BAR)

    def drop(self) -> None:
        """A name deleted outright: no sibling takes it, so only the loss shows."""
        self._write("backend/app/foo.py", EXPORT_AFTER_FOO)

    def rewrite(self, relative: str, text: str) -> None:
        """One file as it stands after the change, for the shapes of their own."""
        self._write(relative, text)

    def run_tool(
        self, directory: str = "backend/app"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--check-moves",
                directory,
                "--base-ref",
                self.base,
                "--repo-root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            env=ENV,
            cwd=str(self.root),
        )


class MovedNameTests(unittest.TestCase):
    """A name that left one file for a sibling while a caller still reads it
    where it used to be: the shape no gate in this repository caught on
    2026-10-08, when `ClaudeLaunch` moved between two harness modules and only
    CI's acceptance job went red."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="split-moves-")
        self.addCleanup(tmp.cleanup)
        self.fixture = MoveFixture(Path(tmp.name) / "repo")

    def test_a_caller_of_the_name_it_left_is_blocked(self) -> None:
        self.fixture.move()
        result = self.fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("backend/scripts/tool.py:1", result.stdout + result.stderr)
        self.assertIn("调用方导入断裂", result.stdout)
        self.assertIn("app.foo._helper", result.stdout)

    def test_the_report_names_the_sibling_that_took_the_name(self) -> None:
        """A deletion reads as a deletion; the destination is what makes it a move."""
        self.fixture.move()
        result = self.fixture.run_tool()
        self.assertIn("符号搬移: app.foo._helper → app.bar", result.stdout)

    def test_retargeting_the_caller_clears_it(self) -> None:
        self.fixture.move()
        self.fixture.caller(MOVED_CALLER)
        result = self.fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("结论: 通过", result.stdout)

    def test_a_destination_not_committed_yet_is_still_the_destination(self) -> None:
        """The move is read before anyone commits it, which is when it is cheap
        to fix: the new sibling file is untracked and must count all the same."""
        tmp = tempfile.TemporaryDirectory(prefix="split-moves-new-")
        self.addCleanup(tmp.cleanup)
        fixture = MoveFixture(Path(tmp.name) / "repo", commit_bar=False)
        fixture.move()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("backend/scripts/tool.py:1", result.stdout + result.stderr)
        self.assertIn("符号搬移: app.foo._helper → app.bar", result.stdout)

    def test_a_directory_that_is_not_there_cannot_be_judged(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--check-moves",
                "backend/app/nowhere",
                "--repo-root",
                str(self.fixture.root),
            ],
            capture_output=True,
            text=True,
            env=ENV,
            cwd=str(self.fixture.root),
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot judge", result.stderr)

    def test_a_relative_re_export_is_not_a_break(self) -> None:
        """`from .base import shared` still resolves after a sibling name is
        deleted, and the caller reading `shared` through this module keeps
        working: the package the module sits in is what answers for it."""
        tmp = tempfile.TemporaryDirectory(prefix="split-moves-export-")
        self.addCleanup(tmp.cleanup)
        fixture = MoveFixture(
            Path(tmp.name) / "repo", commit_bar=False, relative_export=True
        )
        fixture.drop()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("调用方导入断裂", result.stdout)

    def test_a_wider_directory_finds_the_same_break(self) -> None:
        """`--check-moves backend` has to name the same modules `app.foo` that
        `--check-moves backend/app` does: given one level too wide, the run must
        not pass over the break in silence."""
        self.fixture.move()
        for directory in ("backend", "."):
            with self.subTest(directory=directory):
                result = self.fixture.run_tool(directory)
                self.assertEqual(
                    result.returncode, 1, result.stdout + result.stderr
                )
                self.assertIn("调用方导入断裂: app.foo._helper", result.stdout)

    def test_a_name_that_is_also_a_module_beside_it_is_still_a_break(self) -> None:
        """`app/helper.py` is a module of the package, not a submodule of the
        file `app/foo.py`: a view that copies every neighbour in under its bare
        name walks down `app.foo.helper` and passes a name the file gave up."""
        tmp = tempfile.TemporaryDirectory(prefix="split-moves-namesake-")
        self.addCleanup(tmp.cleanup)
        fixture = MoveFixture(
            Path(tmp.name) / "repo",
            commit_bar=False,
            files={
                "backend/app/foo.py": NAMESAKE_BEFORE_FOO,
                "backend/app/helper.py": NAMESAKE_SIBLING,
                "backend/scripts/tool.py": NAMESAKE_CALLER,
            },
        )
        fixture.rewrite("backend/app/foo.py", NAMESAKE_AFTER_FOO)
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("调用方导入断裂: app.foo.helper", result.stdout)
        # One step further, the file itself is gone and `app/helper.py` stands
        # where it was: still not an answer for `app.foo.helper`.
        (fixture.root / "backend/app/foo.py").unlink()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("调用方导入断裂: app.foo.helper", result.stdout)

    def test_a_re_export_from_a_sibling_down_a_level_is_not_a_break(self) -> None:
        """`from .y import z` written by a module that is not the package's only
        level: the short name in the file means `app/domain/y.py`, and a view
        that puts the package's own keys in the module's place reads the
        caller's import as broken while it still resolves."""
        tmp = tempfile.TemporaryDirectory(prefix="split-moves-nested-")
        self.addCleanup(tmp.cleanup)
        fixture = MoveFixture(
            Path(tmp.name) / "repo",
            commit_bar=False,
            files={
                "backend/app/domain/__init__.py": "",
                "backend/app/domain/x.py": NESTED_BEFORE_X,
                "backend/app/domain/y.py": NESTED_SIBLING,
                "backend/scripts/tool.py": NESTED_CALLER,
            },
        )
        fixture.rewrite("backend/app/domain/x.py", NESTED_AFTER_X)
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("调用方导入断裂", result.stdout)

    def test_a_submodule_read_through_the_package_is_not_a_break(self) -> None:
        """The module under inspection is the package's own `__init__.py` here,
        and `from app.helper import other` reads the file `app/helper.py`: a
        table holding only that module has nothing to walk down into, and every
        caller that names a submodule of the package would read as broken."""
        tmp = tempfile.TemporaryDirectory(prefix="split-moves-facade-")
        self.addCleanup(tmp.cleanup)
        fixture = MoveFixture(
            Path(tmp.name) / "repo",
            commit_bar=False,
            files={
                "backend/app/__init__.py": FACADE_BEFORE,
                "backend/app/helper.py": NAMESAKE_SIBLING,
                "backend/scripts/tool.py": FACADE_SUBMODULE_CALLER,
            },
        )
        fixture.rewrite("backend/app/__init__.py", FACADE_AFTER)
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("调用方导入断裂", result.stdout)
        # The name the facade itself gave up is still a break for its callers.
        fixture.caller(FACADE_NAME_CALLER)
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("调用方导入断裂: app.gone", result.stdout)

    def test_a_patch_that_misses_the_definition_it_left_out_of_is_reported(
        self,
    ) -> None:
        """The module kept `decode` as a re-export, so `app.foo.loads` still
        binds and the patch target is not broken — but the re-export preserves
        the object, not the globals dictionary `decode` reads, so the patch no
        longer reaches it, and that is what this check is for."""
        tmp = tempfile.TemporaryDirectory(prefix="split-moves-patch-")
        self.addCleanup(tmp.cleanup)
        fixture = MoveFixture(
            Path(tmp.name) / "repo",
            commit_bar=False,
            files={
                "backend/app/foo.py": PATCH_BEFORE_FOO,
                "backend/app/bar.py": PATCH_BEFORE_BAR,
                "backend/tests/test_x.py": PATCH_CALLER,
                "backend/scripts/tool.py": "",
            },
        )
        fixture.rewrite("backend/app/foo.py", PATCH_AFTER_FOO)
        fixture.rewrite("backend/app/bar.py", PATCH_AFTER_BAR)
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(
            "补丁不再作用于搬走的定义: app.foo.loads → decode", result.stdout
        )

    def test_a_name_both_imported_and_defined_is_not_a_break(self) -> None:
        """`shared` is the fast import and the fallback definition at once, so
        a caller keeps working whichever one runs: a view that still holds the
        import while dropping the file it names calls that caller broken."""
        tmp = tempfile.TemporaryDirectory(prefix="split-moves-fallback-")
        self.addCleanup(tmp.cleanup)
        fixture = MoveFixture(
            Path(tmp.name) / "repo",
            commit_bar=False,
            files={
                "backend/app/foo.py": FALLBACK_BEFORE_FOO,
                "backend/app/fast.py": FALLBACK_FAST,
                "backend/scripts/tool.py": FALLBACK_CALLER,
            },
        )
        fixture.rewrite("backend/app/foo.py", FALLBACK_AFTER_FOO)
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("调用方导入断裂", result.stdout)

    def test_a_missing_name_below_a_submodule_is_still_a_break(self) -> None:
        """The caller reads through the facade and down into `app/pkg/sub.py`,
        which does not hold the name: the walk has to keep descending there, or
        the missing name reads as present and the break goes unreported."""
        tmp = tempfile.TemporaryDirectory(prefix="split-moves-subpackage-")
        self.addCleanup(tmp.cleanup)
        fixture = MoveFixture(
            Path(tmp.name) / "repo",
            commit_bar=False,
            files={
                "backend/app/pkg/__init__.py": SUBPACKAGE_INIT_BEFORE,
                "backend/app/pkg/sub.py": SUBPACKAGE_SUB,
                "backend/scripts/tool.py": SUBPACKAGE_CALLER,
            },
        )
        fixture.rewrite("backend/app/pkg/__init__.py", SUBPACKAGE_INIT_AFTER)
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("调用方导入断裂: app.pkg.sub.gone", result.stdout)


#: A module whose whole file a caller reads — the shape a split silently
#: changes. `checkout_sweep.py` and `launch.py` read `resource_cleanup` and the
#: executor scripts this way, and the read still succeeds after a split because
#: `__file__` points at the new package's `__init__.py`.
SQUASH_BEFORE_FOO = '''\
def keep(value):
    return value
'''

SQUASH_AFTER_INIT = '''\
from .rendering import keep

__all__ = ["keep"]
'''

SQUASH_AFTER_RENDERING = '''\
def keep(value):
    return value
'''

SQUASH_BAR = '''\
def other(value):
    return value
'''

SQUASH_FILE_READER = '''\
from pathlib import Path

from app import foo

SOURCE = Path(foo.__file__).read_text()
'''

SQUASH_ALIASED_READER = '''\
from pathlib import Path

import app.foo as mod

SOURCE = Path(mod.__file__).read_text()
'''

SQUASH_SPEC_READER = '''\
import importlib.util

SPEC = importlib.util.spec_from_file_location("app.foo", "somewhere/foo.py")
'''

SQUASH_RELATIVE_READER = '''\
from pathlib import Path

from . import foo

SOURCE = Path(foo.__file__).read_text()
'''

SQUASH_OTHER_READER = '''\
from pathlib import Path

from app import bar

SOURCE = Path(bar.__file__).read_text()
'''

SQUASH_WRITER = '''\
from app import foo

foo.__file__ = "elsewhere.py"
'''

SQUASH_ORDINARY_CALLER = "from app.foo import keep\n"


class SquashFixture:
    """A throwaway repository where `app/foo.py` is replaced by `app/foo/`."""

    def __init__(self, root: Path, caller: str = "") -> None:
        self.root = root
        written = {
            "backend/app/__init__.py": "",
            "backend/app/foo.py": SQUASH_BEFORE_FOO,
            "backend/app/bar.py": SQUASH_BAR,
            "backend/scripts/tool.py": caller,
        }
        for relative, text in written.items():
            self._write(relative, text)
        _git(root, "init", "-q", "-b", "main")
        for key, value in (
            ("user.email", "self-test@example.invalid"),
            ("user.name", "verify-module-split self-test"),
            ("commit.gpgsign", "false"),
            ("tag.gpgsign", "false"),
            ("gc.auto", "0"),
        ):
            _git(root, "config", key, value)
        _git(root, "add", *written)
        _git(root, "commit", "-q", "-m", "the module before the split")
        self.base = _git(root, "rev-parse", "HEAD").strip()

    def _write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def caller(self, source: str, relative: str = "backend/scripts/tool.py") -> None:
        self._write(relative, source)

    def split(self) -> None:
        (self.root / "backend/app/foo.py").unlink()
        self._write("backend/app/foo/__init__.py", SQUASH_AFTER_INIT)
        self._write("backend/app/foo/rendering.py", SQUASH_AFTER_RENDERING)

    def run_tool(
        self, directory: str = "backend/app"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--check-moves",
                directory,
                "--base-ref",
                self.base,
                "--repo-root",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            env=ENV,
            cwd=str(self.root),
        )


class WholeFileReadTests(unittest.TestCase):
    """A caller that reads the module's own file: the split changes which file
    it reads without breaking any name, so the export, signature, import and
    deferred checks all stay green while the caller ships the wrong file."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="split-squash-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "repo"

    def test_a_caller_reading_the_module_file_is_blocked(self) -> None:
        fixture = SquashFixture(self.root, SQUASH_FILE_READER)
        fixture.split()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("backend/scripts/tool.py", result.stdout)
        self.assertIn("整份读取目标已拆包: app.foo", result.stdout)

    def test_an_aliased_module_read_is_blocked(self) -> None:
        """`import app.foo as mod` binds the module under another name; the
        alias, not the spelling of the import, is what the read follows."""
        fixture = SquashFixture(self.root, SQUASH_ALIASED_READER)
        fixture.split()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("整份读取目标已拆包: app.foo", result.stdout)

    def test_a_spec_loaded_by_module_name_is_blocked(self) -> None:
        """`spec_from_file_location("app.foo", ...)` builds a spec for the
        package's `__init__.py` and still succeeds; nothing else in the tree
        notices that the module's source is no longer what is loaded."""
        fixture = SquashFixture(self.root, SQUASH_SPEC_READER)
        fixture.split()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("整份读取目标已拆包: app.foo", result.stdout)
        self.assertIn("spec_from_file_location", result.stdout)

    def test_a_relative_module_import_is_followed(self) -> None:
        """`from . import foo` inside the package names the same module without
        ever spelling `app.foo`; a scan keyed on the written import misses it."""
        fixture = SquashFixture(self.root)
        fixture.caller(SQUASH_RELATIVE_READER, "backend/app/use.py")
        fixture.split()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("backend/app/use.py", result.stdout)
        self.assertIn("整份读取目标已拆包: app.foo", result.stdout)

    def test_an_ordinary_import_of_a_split_module_still_passes(self) -> None:
        fixture = SquashFixture(self.root, SQUASH_ORDINARY_CALLER)
        fixture.split()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("结论: 通过", result.stdout)

    def test_reading_another_modules_file_is_not_this_break(self) -> None:
        """Only the module that became a package changed meaning; a `__file__`
        read of a sibling the change did not touch is exactly what it was."""
        fixture = SquashFixture(self.root, SQUASH_OTHER_READER)
        fixture.split()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("整份读取目标已拆包", result.stdout)

    def test_writing_the_file_attribute_is_not_a_read(self) -> None:
        """`module.__file__ = ...` sets a string attribute and works the same
        before and after the split; only a read sees which file it names."""
        fixture = SquashFixture(self.root, SQUASH_WRITER)
        fixture.split()
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("整份读取目标已拆包", result.stdout)

    def test_a_read_before_the_split_is_not_this_break(self) -> None:
        """The guard is about the module becoming a package, not about the
        `__file__` spelling: the same caller is fine while the file stays one."""
        fixture = SquashFixture(self.root, SQUASH_FILE_READER)
        fixture.caller(
            SQUASH_BEFORE_FOO + "\n\ndef added(value):\n    return value\n",
            "backend/app/foo.py",
        )
        result = fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("整份读取目标已拆包", result.stdout)


class ShadowCopyTests(unittest.TestCase):
    """A second module under the name this run is about: Python resolves a
    module by name, so the copy answers for the package in `--runtime-identity`,
    and every check that walks the tree reads it as part of the change."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="split-shadow-")
        self.addCleanup(tmp.cleanup)
        self.fixture = Fixture(Path(tmp.name) / "repo")

    def scratch(self) -> Path:
        """The copy a parity harness parks: `<tempdir>/<dir>/<module>.py`."""
        directory = Path(tempfile.mkdtemp(prefix="shadow-copy-"))
        self.addCleanup(shutil.rmtree, directory, True)
        copy = directory / "foo.py"
        copy.write_text(OLD_SOURCE, encoding="utf-8")
        return copy

    def stranger(self) -> Path:
        """Somebody else's file under the same name: not a copy of this module."""
        directory = Path(tempfile.mkdtemp(prefix="shadow-other-"))
        self.addCleanup(shutil.rmtree, directory, True)
        path = directory / "foo.py"
        path.write_text("# somebody else's scratch file\nX = 1\n", encoding="utf-8")
        return path

    def test_a_same_named_file_that_is_not_a_copy_is_left_alone(self) -> None:
        """A name is not a module: `/tmp/foo.py` is an ordinary name for an
        ordinary file, and a run that refused on it refused whenever anybody
        else on the machine had one — 62 of the 68 cases in this file went red
        on exactly that, from one unrelated `/tmp/foo.py` on 2026-10-16."""
        self.fixture.apply_split(SPLIT)
        stranger = self.stranger()
        result = self.fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(str(stranger), result.stderr)

    def test_an_empty_file_is_not_a_copy_of_an_empty_module(self) -> None:
        """A package's blank module is the commonest file in the repository, and
        its text says nothing about anybody else's blank `<name>.py`: matched on
        it, one empty file on the machine would refuse the run."""
        self.fixture.apply_split({**SPLIT, "blank.py": ""})
        directory = Path(tempfile.mkdtemp(prefix="shadow-other-"))
        self.addCleanup(shutil.rmtree, directory, True)
        stranger = directory / "foo.py"
        stranger.write_text("", encoding="utf-8")
        result = self.fixture.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(str(stranger), result.stderr)

    def test_a_copy_in_the_temp_directory_blocks_the_run(self) -> None:
        self.fixture.apply_split(SPLIT)
        copy = self.scratch()
        result = self.fixture.run_tool()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("duplicate module shadow in sys.path", result.stderr)
        self.assertIn(str(copy), result.stderr)
        copy.unlink()
        self.assertEqual(self.fixture.run_tool().returncode, 0)

    def test_a_pre_split_file_left_beside_the_package_blocks_the_run(self) -> None:
        """What the 2026-10-16 eslint run found: the leftover is not a difference
        the change introduced, so the run says which copy to delete instead of
        reporting on two modules at once."""
        self.fixture.apply_split(SPLIT)
        left = self.fixture.root / "pkgroot" / "impl" / "foo.py"
        self.fixture._write("pkgroot/impl/foo.py", OLD_SOURCE)
        result = self.fixture.run_tool()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("duplicate module shadow in sys.path", result.stderr)
        self.assertIn(str(left), result.stderr)
        left.unlink()
        self.assertEqual(self.fixture.run_tool().returncode, 0)


class UsageTests(unittest.TestCase):
    def test_a_missing_check_facade_cannot_be_judged(self) -> None:
        result = subprocess.run(
            [sys.executable, str(TOOL)],
            capture_output=True,
            text=True,
            env=ENV,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_the_self_test_flag_refuses_to_also_check_a_package(self) -> None:
        """Running the tests while the caller asked for a check is not an answer."""
        result = subprocess.run(
            [sys.executable, str(TOOL), "--self-test", "--check-facade", "whatever"],
            capture_output=True,
            text=True,
            env=ENV,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("cannot judge", result.stderr)

    def test_two_modes_at_once_cannot_be_judged(self) -> None:
        """Two answers in one exit code: the reader cannot tell which one it is.

        The assertion is on the refusal's own words, not on the exit code: a
        directory that does not exist exits 2 as well, and a test that accepted
        either would pass with the refusal taken out.
        """
        result = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "--check-facade",
                "whatever",
                "--check-moves",
                "whatever",
            ],
            capture_output=True,
            text=True,
            env=ENV,
        )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("pass exactly one of", result.stderr)

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
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(RuntimeShapeTests))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(CallerContractTests))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(MovedNameTests))
    suite.addTests(
        unittest.TestLoader().loadTestsFromTestCase(WholeFileReadTests)
    )
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(ShadowCopyTests))
    suite.addTests(
        unittest.TestLoader().loadTestsFromTestCase(DeferredRuleAgreementTests)
    )
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(UsageTests))
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
