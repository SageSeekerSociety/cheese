"""Self-test coverage_map.py: which integration test files a change reaches.

A tiny backend whose tests reach the code the way the real ones do — over
HTTP, so no test imports what it exercises — and a map saying which test
executed which function.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "coverage_map", Path(__file__).with_name("coverage_map.py"))
coverage_map = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = coverage_map
spec.loader.exec_module(coverage_map)

ROOMS = '''from app.core.config import settings

LIMIT = 3


class RoomOut:
    name: str
    size: int


def open_room():
    return LIMIT


def close_room():
    return settings.grace
'''
CONFIG = '''class Settings:
    grace: int = 5
    colour: str = "red"


settings = Settings()
'''
REGISTRY = '''HOOKS = []
HOOKS.append(print)
'''

TREE = {
    "app/__init__.py": "",
    "app/core/__init__.py": "",
    "app/core/config.py": CONFIG,
    "app/core/registry.py": REGISTRY,
    "app/rooms.py": ROOMS,
    "app/routes.py": "from app import rooms\nfrom app.core import registry\n\n\n"
                     "def get_room():\n    return rooms.RoomOut()\n",
    "tests/__init__.py": "",
    "tests/integration/__init__.py": "",
    "tests/integration/test_open.py": "def test_open(client):\n    pass\n",
    "tests/integration/test_close.py": "def test_close(client):\n    pass\n",
    "tests/integration/test_get.py": "def test_get(client):\n    pass\n",
    "tests/integration/test_rest.py": "def test_rest(client):\n    pass\n",
}

MAP = {"commit": "a" * 40, "unmapped": [], "files": {
    "app/rooms.py": {"open_room": ["tests/integration/test_open.py"],
                     "close_room": ["tests/integration/test_close.py"]},
    "app/routes.py": {"get_room": ["tests/integration/test_get.py"]},
    "app/core/config.py": {},
    "app/other.py": {"gone": ["tests/integration/test_rest.py"]},
}}


def line_of(text, needle):
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


class SelectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.backend = Path(tmp.name) / "backend"
        for rel, text in TREE.items():
            path = self.backend / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.base = {}

    def read_head(self, path):
        file = self.backend.parent / path
        return file.read_text() if file.exists() else None

    def read_base(self, path):
        return self.base.get(path, self.read_head(path))

    def select(self, lines, cmap=MAP, changed=None):
        changed = changed or list(lines)
        return coverage_map.select(cmap, self.backend, changed, lines,
                                   self.read_base, self.read_head)[0]

    def test_a_changed_function_runs_the_tests_that_executed_it(self):
        line = line_of(ROOMS, "return settings.grace")
        self.assertEqual(self.select({"backend/app/rooms.py": ({line}, {line})}),
                         ["tests/integration/test_close.py"])

    def test_a_constant_runs_the_tests_of_the_functions_that_read_it(self):
        line = line_of(ROOMS, "LIMIT = 3")
        self.assertEqual(self.select({"backend/app/rooms.py": ({line}, {line})}),
                         ["tests/integration/test_open.py"])

    def test_a_class_body_runs_the_tests_of_its_readers(self):
        # A model's fields run at import, before any test: who builds it does.
        line = line_of(ROOMS, "size: int")
        self.assertEqual(self.select({"backend/app/rooms.py": ({line}, {line})}),
                         ["tests/integration/test_get.py"])

    def test_a_settings_field_runs_the_tests_that_read_it_as_an_attribute(self):
        line = line_of(CONFIG, "grace: int")
        self.assertIn("tests/integration/test_close.py",
                      self.select({"backend/app/core/config.py": ({line}, {line})}))

    def test_a_statement_that_binds_no_name_runs_every_importers_tests(self):
        line = line_of(REGISTRY, "HOOKS.append")
        self.assertEqual(self.select({"backend/app/core/registry.py": ({line}, {line})}),
                         ["tests/integration/test_get.py"])

    def test_an_import_or_a_comment_runs_nothing_the_map_holds(self):
        self.assertEqual(self.select({"backend/app/rooms.py": ({1}, {1})}), [])

    def test_a_deleted_file_runs_every_test_that_executed_it(self):
        self.assertEqual(self.select({"backend/app/other.py": ({1}, set())}),
                         ["tests/integration/test_rest.py"])

    def test_a_changed_test_runs(self):
        self.assertEqual(self.select({"backend/tests/integration/test_rest.py": ({1}, {1})}),
                         ["tests/integration/test_rest.py"])

    def test_tests_the_map_does_not_cover_run_every_time(self):
        cmap = {**MAP, "unmapped": ["tests/integration/test_rest.py"]}
        line = line_of(ROOMS, "return settings.grace")
        self.assertEqual(self.select({"backend/app/rooms.py": ({line}, {line})}, cmap),
                         ["tests/integration/test_close.py", "tests/integration/test_rest.py"])

    def test_a_test_file_gone_from_the_tree_is_not_handed_to_pytest(self):
        (self.backend / "tests/integration/test_close.py").unlink()
        line = line_of(ROOMS, "return settings.grace")
        self.assertEqual(self.select({"backend/app/rooms.py": ({line}, {line})}), [])

    def test_a_change_every_test_shares_runs_the_whole_layer(self):
        self.assertIsNone(self.select({}, changed=["backend/uv.lock"]))


class ChangedLinesTest(unittest.TestCase):
    def test_hunks_give_old_and_new_lines_and_a_deletion_keeps_its_path(self):
        diff = "\n".join([
            "diff --git a/backend/app/x.py b/backend/app/x.py",
            "--- a/backend/app/x.py", "+++ b/backend/app/x.py",
            "@@ -3,0 +4,2 @@ def f():", "@@ -10 +12 @@",
            "diff --git a/backend/app/gone.py b/backend/app/gone.py",
            "--- a/backend/app/gone.py", "+++ /dev/null", "@@ -1,3 +0,0 @@",
        ])
        self.assertEqual(coverage_map.changed_lines(diff), {
            "backend/app/x.py": ({10}, {4, 5, 12}),
            "backend/app/gone.py": ({1, 2, 3}, set()),
        })


class MergeTest(unittest.TestCase):
    def test_shards_union_and_planned_tests_without_a_context_are_named(self):
        shards = [{"app/a.py": {"f": ["tests/integration/test_a.py"]}},
                  {"app/a.py": {"f": ["tests/integration/test_b.py"]}}]
        merged = coverage_map.merge(shards, {"tests/integration/test_a.py",
                                             "tests/integration/test_b.py",
                                             "tests/integration/test_c.py"}, "c" * 40)
        self.assertEqual(merged["files"]["app/a.py"]["f"],
                         ["tests/integration/test_a.py", "tests/integration/test_b.py"])
        self.assertEqual(merged["unmapped"], ["tests/integration/test_c.py"])

    def test_a_missing_shard_is_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shard = tmp / "shard.json"
            shard.write_text(json.dumps({}))
            out = tmp / "map.json"
            code = coverage_map.main(["merge", str(shard), "--selection", str(tmp),
                                      "--commit", "c" * 40, "--out", str(out)])
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
