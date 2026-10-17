"""Black-box pure-move checks; inputs are parsed, never imported."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / ".claude/scripts/move_only_verify.py"
ORIGINAL = '''from __future__ import annotations
import os
__all__ = ["answer", "Client", "LIMIT"]
LIMIT: int = 7
_PRIVATE = {"a": 3}

def answer(x: int = 4) -> int:
    """A preserved docstring."""
    return x + LIMIT

async def _helper(x):
    return x

class Client:
    def run(self, value=8):
        return value
'''
PARTS = ORIGINAL.replace('__all__ = ["answer", "Client", "LIMIT"]\n', '')
FACADE = '''from __future__ import annotations
from .parts import answer, Client, LIMIT
__all__ = ["answer", "Client", "LIMIT"]
'''


class MoveOnlyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="move-only-test-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Test", GIT_COMMITTER_NAME="Test",
                        GIT_AUTHOR_EMAIL="test@example.com", GIT_COMMITTER_EMAIL="test@example.com")
        self.git("init", "-b", "main")
        self.write("pkg/old.py", ORIGINAL)
        self.git("add", "pkg/old.py")
        self.git("commit", "-m", "baseline")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.write("pkg/old.py", FACADE)
        self.write("pkg/parts.py", PARTS)

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.env,
                              check=True, capture_output=True).stdout

    def write(self, name, value):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def verify(self, *paths, base="origin/main"):
        return subprocess.run([sys.executable, "-I", str(SCRIPT), "--base-ref", base,
                               "pkg/old.py", *(paths or ("pkg/old.py", "pkg/parts.py"))],
                              cwd=self.repo, env=self.env, text=True,
                              capture_output=True, timeout=15)

    def assert_result(self, result, code, text):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertIn(text, result.stdout + result.stderr)

    def test_moved_definitions_constants_and_reexports_pass(self):
        self.assert_result(self.verify(), 0, "runtime behaviour unverified")

    def test_comments_locations_and_quote_style_do_not_count_as_changes(self):
        self.write("pkg/parts.py", "# moved\n\n" + PARTS.replace('"a"', "'a'"))
        self.assert_result(self.verify(), 0, "PASS")

    def test_body_default_decorator_class_and_private_function_changes_fail(self):
        mutations = [
            ("return x + LIMIT", "return x - LIMIT", "answer"),
            ("x: int = 4", "x: int = 5", "answer"),
            ("def answer", "@staticmethod\ndef answer", "answer"),
            ("class Client:", "class Client(dict):", "Client"),
            ("return value", "return None", "Client"),
            ("async def _helper(x):\n    return x", "async def _helper(x):\n    return 0", "_helper"),
        ]
        for before, after, name in mutations:
            with self.subTest(name=name, after=after):
                self.write("pkg/parts.py", PARTS.replace(before, after))
                self.assert_result(self.verify(), 1, f"definitions missing/changed: {name}")

    def test_constant_values_and_annotations_are_preserved(self):
        for before, after in (("LIMIT: int = 7", "LIMIT: int = 9"),
                              ("LIMIT: int = 7", "LIMIT: float = 7"),
                              ('{"a": 3}', '{"a": 10}')):
            with self.subTest(after=after):
                self.write("pkg/parts.py", PARTS.replace(before, after))
                self.assert_result(self.verify(), 1, "constants missing/changed")

    def test_lost_private_definition_and_added_definition_fail(self):
        self.write("pkg/parts.py", PARTS.replace("async def _helper(x):\n    return x\n", ""))
        self.assert_result(self.verify(), 1, "definitions missing/changed: _helper")
        self.write("pkg/parts.py", PARTS + "\ndef extra():\n    pass\n")
        self.assert_result(self.verify(), 1, "definitions added/changed: extra")

    def test_duplicate_definition_in_another_file_is_not_hidden_by_set_union(self):
        self.write("pkg/extra.py", "from __future__ import annotations\nasync def _helper(x):\n    return x\n")
        self.assert_result(self.verify("pkg/old.py", "pkg/parts.py", "pkg/extra.py"), 1,
                           "definitions added/changed: _helper")

    def test_lost_export_fails_even_if_implementation_still_exists(self):
        self.write("pkg/old.py", FACADE.replace('"answer", ', ''))
        self.assert_result(self.verify(), 1, "exports missing: answer")

    def test_wrong_reexport_fails_even_with_unchanged_all(self):
        self.write("pkg/old.py", FACADE.replace("from .parts", "from .missing"))
        self.assert_result(self.verify(), 1, "export binding changed: answer")

    def test_unbound_export_and_dynamic_exports_cannot_pass(self):
        for facade, text in ((FACADE.replace("answer, Client, LIMIT", "Client, LIMIT"), "no static binding"),
                             (FACADE.replace('["answer", "Client", "LIMIT"]', "list(('answer', 'Client', 'LIMIT'))"),
                              "dynamic __all__"),
                             (FACADE + "__all__.append('new')\n", "unsupported module statement")):
            with self.subTest(text=text):
                self.write("pkg/old.py", facade)
                self.assert_result(self.verify(), 2, text)

    def test_dynamic_top_level_and_star_imports_are_unjudged(self):
        for suffix in ("\nif True:\n    EXTRA = 1\n", "\nfrom .parts import *\n",
                       "\nLIMIT += 1\n", "\nLIMIT = 8\n"):
            with self.subTest(suffix=suffix):
                self.write("pkg/old.py", FACADE + suffix)
                self.assert_result(self.verify(), 2, "UNJUDGED")

    def test_transitive_reexport_and_module_to_package_move(self):
        (self.repo / "pkg/old.py").unlink()
        self.write("pkg/old/__init__.py", FACADE.replace(".parts", ".bridge"))
        self.write("pkg/old/bridge.py", FACADE)
        self.write("pkg/old/parts.py", PARTS)
        self.assert_result(self.verify("pkg/old/__init__.py", "pkg/old/bridge.py", "pkg/old/parts.py"), 0, "PASS")

    def test_reexport_cycle_is_unjudged(self):
        self.write("pkg/bridge.py", FACADE.replace(".parts", ".old"))
        self.write("pkg/old.py", FACADE.replace(".parts", ".bridge"))
        self.assert_result(self.verify("pkg/old.py", "pkg/bridge.py", "pkg/parts.py"), 2, "circular re-export")

    def test_future_semantics_cannot_be_dropped(self):
        self.write("pkg/parts.py", PARTS.replace("from __future__ import annotations\n", ""))
        self.assert_result(self.verify(), 1, "future imports changed")

    def test_original_and_destination_code_are_never_executed(self):
        # AST equality can be checked for a call-valued constant without calling it.
        source = "MARKER = open('executed', 'w').write('bad')\n"
        self.write("pkg/old.py", source)
        self.git("add", "pkg/old.py")
        self.git("commit", "-m", "call-valued baseline")
        self.write("pkg/parts.py", source)
        self.write("pkg/old.py", "from .parts import MARKER\n")
        self.assert_result(self.verify(base="HEAD"), 0, "PASS")
        self.assertFalse((self.repo / "executed").exists())

    def test_implicit_public_imports_must_survive(self):
        self.write("pkg/old.py", "import os\nVALUE = 3\n")
        self.git("add", "pkg/old.py")
        self.git("commit", "-m", "implicit exports")
        self.write("pkg/old.py", "from .parts import VALUE\n")
        self.write("pkg/parts.py", "import os\nVALUE = 3\n")
        self.assert_result(self.verify(base="HEAD"), 1, "exports missing: os")
        self.write("pkg/old.py", "from .parts import VALUE, os\n")
        self.assert_result(self.verify(base="HEAD"), 0, "PASS")

    def test_base_is_exact_ref_not_merge_base_or_worktree(self):
        self.assert_result(self.verify(base="HEAD"), 0, "PASS")
        self.git("add", "pkg/old.py", "pkg/parts.py")
        self.git("commit", "-m", "split")
        self.assert_result(self.verify(), 0, "PASS")
        self.assert_result(self.verify(base="HEAD"), 1, "definitions added/changed")

    def test_invalid_inputs_are_not_green(self):
        self.assert_result(self.verify(base="nonexistent"), 2, "UNJUDGED")
        self.assert_result(self.verify("pkg/old.py", "pkg/old.py"), 2, "duplicate destination")
        self.assert_result(self.verify("../outside.py"), 2, "repository-relative")
        self.assert_result(self.verify("pkg/missing.py"), 2, "UNJUDGED")
        self.write("pkg/parts.py", "def invalid syntax")
        self.assert_result(self.verify(), 2, "UNJUDGED")


if __name__ == "__main__":
    unittest.main()
