"""Functional rules: debt can be removed, never exchanged or re-frozen."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("scene-debt-ratchet.py")
spec = importlib.util.spec_from_file_location("debt", SCRIPT)
assert spec and spec.loader
debt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(debt)


class DebtRules(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline = self.root / debt.BASELINE
        self.write("src/router/index.ts", "import P from '../views/P.vue'")
        self.write("src/api.ts", "export const request = () => fetch('/api')")
        self.write(
            "src/components/Child.vue",
            "<script setup>import {request} from '@/api'; request()</script><template><div/></template>",
        )
        self.page("<child/>")
        self.freeze()

    def write(self, relative: str, text: str) -> None:
        path = self.root / "frontend" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def page(self, template: str, script: str = "") -> None:
        self.write(
            "src/views/P.vue",
            f"""<script setup>
import Child from '../components/Child.vue'
import {{request}} from '@/api'
request()
{script}
</script><template>{template}</template>""",
        )

    def freeze(self) -> None:
        self.baseline.write_text(
            debt.baseline_text(debt.collect(self.root)), encoding="utf-8"
        )

    def run_gate(self, *args: str, rc: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, rc, result.stdout + result.stderr)
        return result

    def test_rendered_edges_not_unused_imports_comments_or_attribute_examples(
        self,
    ) -> None:
        actual = debt.collect(self.root)
        self.assertEqual(
            actual["children"], {"src/views/P.vue -> src/components/Child.vue"}
        )
        self.page('<div title="<Child/>"><!-- <Child/> --></div>')
        self.assertEqual(debt.collect(self.root)["children"], set())
        self.run_gate()

    def test_replacing_a_child_with_another_is_new_debt_even_at_equal_count(
        self,
    ) -> None:
        self.write(
            "src/components/Other.vue",
            "<script setup>fetch('/api')</script><template><div/></template>",
        )
        self.page("<Other/>", "import Other from '../components/Other.vue'")
        before = self.baseline.read_bytes()
        self.run_gate(rc=1)
        self.run_gate("--update", rc=1)
        self.assertEqual(before, self.baseline.read_bytes())

    def test_ready_and_verified_container_pages_are_not_debt_routes(self) -> None:
        self.write("src/views/PView.vue", "<template><div/></template>")
        self.page(
            "<PView/>",
            "import PView from './PView.vue'; import {useRoute} from 'vue-router'; useRoute()",
        )
        actual = debt.collect(self.root)
        self.assertEqual(actual["children"], set())
        self.assertEqual(actual["routes"], set())
        self.write("src/views/P.vue", "<template><div/></template>")
        self.assertEqual(debt.collect(self.root)["routes"], set())

    def test_non_a_sibling_view_is_not_a_route_and_container_must_have_an_a_view(
        self,
    ) -> None:
        self.write(
            "src/views/PView.vue",
            "<script setup>import {useRoute} from 'vue-router'; useRoute()</script><template><div/></template>",
        )
        self.page("<PView/>", "import PView from './PView.vue'")
        actual = debt.collect(self.root)
        self.assertEqual(actual["routes"], set())
        self.assertIn("src/views/P.vue -> src/views/PView.vue", actual["children"])

    def test_new_route_call_cannot_be_frozen_and_paid_calls_cannot_return(self) -> None:
        self.page(
            "<Child/>", "import {useRoute as address} from 'vue-router'; address()"
        )
        before = self.baseline.read_bytes()
        self.run_gate("--update", rc=1)
        self.assertEqual(before, self.baseline.read_bytes())
        self.freeze()
        self.page("<Child/>")
        self.run_gate("--update")
        self.page("<Child/>", "import * as router from 'vue-router'; router.useRoute()")
        self.run_gate(rc=1)

    def test_old_network_imports_are_frozen_per_file_including_types_and_dynamic_imports(
        self,
    ) -> None:
        self.write(
            "src/views/Old.vue",
            "<script setup>import type {T} from '@/network/types'</script><template><div/></template>",
        )
        self.freeze()
        self.run_gate()
        self.write(
            "src/views/New.vue",
            "<script setup>const n = import('../network')</script><template><div/></template>",
        )
        before = self.baseline.read_bytes()
        self.run_gate("--update", rc=1)
        self.assertEqual(before, self.baseline.read_bytes())
        self.write("src/views/New.vue", "<template><div/></template>")
        self.write("src/views/Old.vue", "<template><div/></template>")
        self.run_gate("--update")
        self.write(
            "src/views/Old.vue",
            "<script setup>export type {T} from '@/network/types'</script><template><div/></template>",
        )
        self.run_gate(rc=1)

    def test_route_and_network_counts_cannot_be_swapped_to_other_files(self) -> None:
        self.page(
            "<Child/>",
            "import {useRoute} from 'vue-router'; useRoute(); import '@/network'",
        )
        self.freeze()
        self.page("<Child/>")
        self.write(
            "src/views/Q.vue",
            "<script setup>import {useRoute} from 'vue-router'; useRoute(); import '@/network'</script><template><div/></template>",
        )
        self.write(
            "src/router/index.ts",
            "import P from '../views/P.vue'; import Q from '../views/Q.vue'",
        )
        result = self.run_gate(rc=1)
        self.assertIn("NEW: src/views/Q.vue", result.stdout)

    def test_json_verdict_has_the_same_failure_and_no_human_output(self) -> None:
        self.page("<Child/>", "useRoute()")
        record = json.loads(self.run_gate("--kind", "routes", "--json", rc=1).stdout)
        self.assertEqual(record["status"], "fail")
        self.assertEqual(record["actual"], 1)
        self.assertEqual(record["frozen"], 0)

    def test_missing_malformed_and_incomplete_baselines_are_cannot_judge(self) -> None:
        for text in (
            "{",
            "{}",
            '{"version":1,"children":[],"routes":[],"network":["*"]}',
        ):
            self.baseline.write_text(text)
            self.run_gate(rc=2)
        self.baseline.unlink()
        self.run_gate(rc=2)

    def test_unparseable_source_and_missing_router_are_cannot_judge(self) -> None:
        self.write("src/broken.ts", "const = ;")
        self.run_gate(rc=2)
        (self.root / "frontend/src/broken.ts").unlink()
        (self.root / "frontend/src/router/index.ts").unlink()
        self.run_gate(rc=2)

    def test_hand_raising_a_baseline_is_rejected_against_the_approved_revision(
        self,
    ) -> None:
        def git(*args: str) -> str:
            result = subprocess.run(
                ["git", *args], cwd=self.root, text=True, capture_output=True
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

        git("init", "-q")
        git("add", "frontend/scene-debt-baseline.json")
        git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "test: freeze debt",
        )
        approved = git("rev-parse", "HEAD")
        self.page("<Child/>", "useRoute()")
        self.freeze()  # A JSON edit alone would otherwise hide the regression.
        before = self.baseline.read_bytes()
        result = self.run_gate("--base", approved, "--update", rc=1)
        self.assertIn("BASELINE GREW: src/views/P.vue", result.stdout)
        self.assertEqual(before, self.baseline.read_bytes())


if __name__ == "__main__":
    unittest.main()
