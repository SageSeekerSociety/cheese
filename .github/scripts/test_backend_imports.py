"""Which test files a backend change selects for the integration layer.

Each case builds a small backend tree and asks for the files a change reaches.
The rules are the ones a pull request's integration run is judged by: a test
that imports the changed code, however indirectly, runs; one that shares only a
conftest with it does not; a change every test depends on without importing it
runs everything.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "backend_imports", Path(__file__).with_name("backend_imports.py"))
backend_imports = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backend_imports
spec.loader.exec_module(backend_imports)

TREE = {
    "app/__init__.py": "",
    "app/core/__init__.py": "",
    "app/core/clock.py": "def now(): return 0\n",
    "app/domain/__init__.py": "",
    "app/domain/tasks/__init__.py": "",
    "app/domain/tasks/service.py": "from app.core.clock import now\n",
    "app/domain/tasks/prompt.md": "say hello\n",
    "app/domain/rooms/__init__.py": "",
    "app/domain/rooms/service.py": "import importlib\nimportlib.import_module('app.domain.tasks.service')\n",
    "app/domain/billing/__init__.py": "",
    "app/domain/billing/service.py": "x = 1\n",
    "tests/__init__.py": "",
    "tests/conftest.py": "from app.domain.billing import service\n",
    "tests/support/__init__.py": "",
    "tests/support/rooms.py": "from app.domain.rooms import service\n",
    "tests/integration/__init__.py": "",
    "tests/integration/test_tasks.py": "from app.domain.tasks.service import now\n",
    "tests/integration/test_rooms.py": "from tests.support.rooms import service\n",
    "tests/integration/test_billing.py": "x = 1\n",
    "tests/unit/test_task_store.py": "from app.domain.tasks import service\n",
    "tests/contract/test_tasks_contract.py": "from app.domain.tasks import service\n",
}


class SelectIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.backend = Path(self.tmp.name) / "backend"
        for rel, text in TREE.items():
            path = self.backend / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def select(self, *changed):
        return backend_imports.select_integration(self.backend, list(changed))[0]

    def test_a_test_that_reaches_the_change_through_imports_runs(self):
        self.assertEqual(
            self.select("backend/app/core/clock.py"),
            ["tests/integration/test_rooms.py", "tests/integration/test_tasks.py",
             "tests/unit/test_task_store.py"],
        )

    def test_a_literal_import_module_and_a_support_helper_carry_the_change(self):
        self.assertIn("tests/integration/test_rooms.py",
                      self.select("backend/app/domain/tasks/service.py"))

    def test_sharing_only_a_conftest_does_not_select_a_test(self):
        self.assertEqual(self.select("backend/app/domain/billing/service.py"), [])

    def test_the_contract_layer_is_never_narrowed_here(self):
        self.assertNotIn("tests/contract/test_tasks_contract.py",
                         self.select("backend/app/domain/tasks/service.py"))

    def test_a_changed_test_runs(self):
        self.assertEqual(self.select("backend/tests/integration/test_billing.py"),
                         ["tests/integration/test_billing.py"])

    def test_a_data_file_beside_code_selects_what_its_folder_reaches(self):
        self.assertIn("tests/integration/test_tasks.py",
                      self.select("backend/app/domain/tasks/prompt.md"))

    def test_inputs_every_test_shares_run_everything(self):
        for path in ("backend/alembic/versions/a1_add.py", "backend/alembic.ini",
                     "backend/pyproject.toml", "backend/uv.lock",
                     "backend/sandbox/cheese/run.sh", "backend/tests/conftest.py",
                     "backend/tests/integration/conftest.py",
                     "backend/tests/integration/data/rows.json",
                     "backend/tests/support/schema.json"):
            with self.subTest(path=path):
                self.assertIsNone(self.select(path, "backend/app/core/clock.py"))

    def test_notes_and_baselines_beside_the_code_change_nothing(self):
        for path in ("backend/AGENTS.md", "backend/openapi-untyped.json",
                     "backend/tests/contract/routes.yaml", "frontend/src/App.vue"):
            with self.subTest(path=path):
                self.assertEqual(self.select(path), [])


if __name__ == "__main__":
    unittest.main()
