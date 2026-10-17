"""The forge event relay is recreated exactly when the code it runs changes.

deploy-docker.sh keeps the running relay, and every deployment's event socket
on it, when deploy/code-digest.py gives the same answer in the running relay
and in the release's image. A digest that missed a change would leave the
relay on old code; one that moved with unrelated code would recreate it on
every release, which is what it replaced.
"""

import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "code-digest.py"
BACKEND = ROOT / "backend"


def digest(path: Path, module: str) -> str:
    result = subprocess.run(
        [sys.executable, "-", module],
        input=SCRIPT.read_text(),
        cwd=path,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def digest_with_unreadable_input(
    path: Path, module: str, unreadable: str
) -> subprocess.CompletedProcess:
    runner = """
import builtins
import io
import os
import runpy
import sys

script, module, unreadable = sys.argv[1:]
real_builtin_open = builtins.open
real_io_open = io.open
real_os_open = os.open

def guarded_open(file, *args, **kwargs):
    if os.fsdecode(file) == unreadable:
        raise PermissionError(f"permission denied: {unreadable}")
    return real_builtin_open(file, *args, **kwargs)

def guarded_io_open(file, *args, **kwargs):
    if os.fsdecode(file) == unreadable:
        raise PermissionError(f"permission denied: {unreadable}")
    return real_io_open(file, *args, **kwargs)

def guarded_os_open(file, *args, **kwargs):
    if os.fsdecode(file) == unreadable:
        raise PermissionError(f"permission denied: {unreadable}")
    return real_os_open(file, *args, **kwargs)

# Simulate a permission error through Python's standard file APIs.
builtins.open = guarded_open
io.open = guarded_io_open
os.open = guarded_os_open
sys.argv = [script, module]
runpy.run_path(script, run_name="__main__")
"""
    return subprocess.run(
        [sys.executable, "-c", runner, str(SCRIPT), module, unreadable],
        cwd=path,
        capture_output=True,
        text=True,
    )


class CodeDigestTest(unittest.TestCase):
    def test_moves_with_the_service_code_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "svc").mkdir()
            (root / "svc" / "__init__.py").write_text("")
            (root / "svc" / "app.py").write_text("from svc import dep\nVALUE = dep.VALUE\n")
            (root / "svc" / "dep.py").write_text("VALUE = 1\n")
            (root / "svc" / "other.py").write_text("UNUSED = 1\n")
            first = digest(root, "svc.app")
            self.assertTrue(first.startswith("code-digest "))

            (root / "svc" / "other.py").write_text("UNUSED = 2\n")
            self.assertEqual(digest(root, "svc.app"), first, "an unrelated module moved the digest")

            (root / "svc" / "dep.py").write_text("VALUE = 2\n")
            self.assertNotEqual(digest(root, "svc.app"), first, "a change to imported code kept the digest")

    def test_stdin_main_alias_is_not_treated_as_a_missing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "svc").mkdir()
            (root / "svc" / "__init__.py").write_text("")
            (root / "svc" / "app.py").write_text("import multiprocessing\nVALUE = 1\n")

            result = subprocess.run(
                [sys.executable, "-", "svc.app"],
                input=SCRIPT.read_text(),
                cwd=root,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("code-digest "))

    def test_missing_module_source_fails_without_printing_a_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "svc").mkdir()
            (root / "svc" / "__init__.py").write_text("")
            (root / "svc" / "app.py").write_text("from svc import dep\n")
            (root / "svc" / "dep.py").write_text(
                "from pathlib import Path\nPath(__file__).unlink()\nVALUE = 1\n"
            )

            result = subprocess.run(
                [sys.executable, "-", "svc.app"],
                input=SCRIPT.read_text(),
                cwd=root,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("code-digest ", result.stdout)
            self.assertIn(str(root / "svc" / "dep.py"), result.stderr)

    def test_unreadable_system_inputs_fail_without_printing_a_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "svc").mkdir()
            (root / "svc" / "__init__.py").write_text("")
            (root / "svc" / "app.py").write_text("VALUE = 1\n")

            for path in ("/etc/os-release", "/var/lib/dpkg/status"):
                with self.subTest(path=path):
                    result = digest_with_unreadable_input(root, "svc.app", path)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("code-digest ", result.stdout)
                    self.assertIn(path, result.stderr)

    def test_the_relay_imports_everything_it_runs_up_front(self):
        # The digest sees what importing the relay loads. A module the relay
        # imported inside a function would change without moving it.
        seen: set[str] = set()
        pending = ["app.forge_events_app"]
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            path = BACKEND / (name.replace(".", "/") + ".py")
            if not path.exists():
                path = BACKEND / name.replace(".", "/") / "__init__.py"
            tree = ast.parse(path.read_text())
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
                    pending.append(node.module)
                elif isinstance(node, ast.Import):
                    pending.extend(alias.name for alias in node.names if alias.name.startswith("app"))
            nested = [
                f"{path.relative_to(ROOT)}:{node.lineno}"
                for top in tree.body
                if not isinstance(top, (ast.Import, ast.ImportFrom))
                for node in ast.walk(top)
                if isinstance(node, (ast.Import, ast.ImportFrom))
            ]
            self.assertEqual(nested, [], "the relay imports these later, out of the digest's sight")
        self.assertIn("app.core.forge_events", seen)


if __name__ == "__main__":
    unittest.main()
