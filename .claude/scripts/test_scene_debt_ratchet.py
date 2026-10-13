"""Functional rules: debt can be removed, never exchanged or re-frozen."""

from __future__ import annotations

import importlib.util
import json
import re
import resource
import signal
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

    def test_dynamic_and_async_children_cannot_disappear_from_the_freeze(self) -> None:
        self.page('<component :is="Child"/>')
        self.run_gate("--update")
        self.assertEqual(
            debt.parse_baseline(self.baseline.read_text())["children"],
            {"src/views/P.vue -> src/components/Child.vue"},
        )
        self.write(
            "src/components/Other.vue",
            "<script setup>fetch('/api')</script><template><div/></template>",
        )
        before = self.baseline.read_bytes()
        self.page(
            '<component :is="AsyncChild"/>',
            "import {defineAsyncComponent} from 'vue'; const AsyncChild = defineAsyncComponent(() => import('../components/Other.vue'))",
        )
        self.run_gate("--update", rc=1)
        self.assertEqual(before, self.baseline.read_bytes())
        self.page(
            "<AsyncChild/>",
            "import {defineAsyncComponent as async} from 'vue'; const AsyncChild = async(() => import('../components/Other.vue'))",
        )
        self.run_gate(rc=1)

    def test_template_route_calls_count_and_shadowed_calls_do_not(self) -> None:
        for template, script in (
            (
                "<div>{{ address().params.id }}</div>",
                "import {useRoute as address} from 'vue-router'",
            ),
            ("<Child/>", "import {useRoute} from 'vue-router'; (useRoute)()"),
            ("<Child/>", "import * as router from 'vue-router'; router['useRoute']()"),
        ):
            self.page(template, script)
            self.run_gate(rc=1)
        self.page(
            "<Child/>",
            "import {useRoute as address} from 'vue-router'; const run = (address: () => number) => address()",
        )
        self.run_gate()

    def test_commented_scripts_and_supported_tsx_are_not_false_debt(self) -> None:
        self.write(
            "src/views/Example.vue",
            "<!-- <script>import '@/network'</script> --><template><div/></template>",
        )
        self.write(
            "src/components/Render.vue",
            '<script lang="tsx">export default () => <div/></script>',
        )
        self.run_gate()

    def test_empty_vue_expressions_in_non_route_components_are_legal(self) -> None:
        for template in ("<div>{{ }}</div>", '<component :is=""/>', '<div v-show=""/>'):
            self.write("src/components/Empty.vue", f"<template>{template}</template>")
            self.run_gate()

    def test_dynamic_template_locals_are_not_imported_child_edges(self) -> None:
        self.page("<div/>")
        self.run_gate("--update")
        before = self.baseline.read_bytes()
        for template in (
            '<component v-for="Child in items" :is="Child"/>',
            '<my-list v-slot="Child"><component :is="Child"/></my-list>',
            '<my-list v-slot="{value: Child}"><component :is="Child"/></my-list>',
        ):
            self.page(template)
            self.run_gate()
            self.assertEqual(debt.collect(self.root)["children"], set())
        self.page('<component :is="Child"/>')
        self.run_gate("--update", rc=1)
        self.assertEqual(before, self.baseline.read_bytes())

    def test_shorthand_component_registries_cannot_hide_new_child_edges(self) -> None:
        self.page("<div/>")
        self.run_gate("--update")
        before = self.baseline.read_bytes()
        for template, script in (
            ('<component :is="choices.Child"/>', "const choices = { Child }"),
            ('<component :is="choices.Child"/>', "const choices = { Child: Child }"),
            ('<component :is="({Child}).Child"/>', ""),
        ):
            self.page(template, script)
            self.run_gate("--update", rc=1)
            self.assertEqual(before, self.baseline.read_bytes())
        self.page('<component v-for="Child in items" :is="({Child}).Child"/>')
        self.run_gate()

    def test_failed_update_preserves_baseline_and_reports_cannot_judge(self) -> None:
        self.page("<div/>")
        before = self.baseline.read_bytes()

        def limit():
            resource.setrlimit(resource.RLIMIT_FSIZE, (40, 40))
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(self.root),
                "--update",
                "--json",
            ],
            text=True,
            capture_output=True,
            preexec_fn=limit,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "cannot_judge")
        self.assertEqual(before, self.baseline.read_bytes())
        self.assertEqual(list(self.baseline.parent.glob("tmp*")), [])

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

    def test_failure_names_the_kind_and_the_shrink_only_update(self) -> None:
        # A new route read: the failure names that kind's fix and its landing
        # spot, says the baseline only shrinks, and does not print another
        # kind's fix for a debt it does not have.
        self.page("<Child/>", "import {useRoute} from 'vue-router'; useRoute()")
        routes = self.run_gate(rc=1)
        self.assertIn("scene-use-route —", routes.stdout)
        self.assertIn("lint:scene-debt:update", routes.stdout)
        self.assertNotIn("scene-non-a-children —", routes.stdout)
        # The debt entry itself is the location, listed under the fix.
        self.assertIn("NEW: src/views/P.vue", routes.stdout)

        # --json (what ci:fast calls) stays the one record line on stdout, with
        # the same failing verdict, and carries the same fix and entries on stderr.
        result = self.run_gate("--kind", "routes", "--json", rc=1)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        self.assertEqual(json.loads(lines[0])["status"], "fail")
        self.assertIn("scene-use-route —", result.stderr)
        self.assertIn("NEW: src/views/P.vue", result.stderr)

        # A new legacy @/network import names the network fix instead.
        self.page("<Child/>", "import '@/network'")
        network = self.run_gate(rc=1)
        self.assertIn("legacy-network —", network.stdout)
        self.assertNotIn("scene-use-route —", network.stdout)

    def test_baseline_line_points_within_the_kind_for_any_json_layout(self) -> None:
        indented = (
            "{\n"
            '  "version": 1,\n'
            '  "children": [],\n'
            '  "routes": [\n'
            '    "src/views/A.vue"\n'
            "  ],\n"
            '  "network": [\n'
            '    "src/views/A.vue"\n'
            "  ]\n"
            "}\n"
        )
        # One path can sit in two arrays, so the line is scoped to its own kind.
        self.assertEqual(debt.baseline_line(indented, "routes", "src/views/A.vue"), 5)
        self.assertEqual(debt.baseline_line(indented, "network", "src/views/A.vue"), 8)
        # One-line JSON still locates the entry (line 1) without crashing.
        one_line = (
            '{"version":1,"children":[],"routes":["src/views/A.vue"],"network":[]}'
        )
        self.assertEqual(debt.baseline_line(one_line, "routes", "src/views/A.vue"), 1)
        self.assertIsNone(debt.baseline_line(indented, "routes", "src/views/B.vue"))

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
        # --update refuses while a mis-added line is present, so it cannot remove
        # it: the report must say to restore that line by hand.
        self.assertIn("put the added baseline line back by hand", result.stdout)
        # The grew entry carries the real line in the baseline JSON, and that line
        # really holds the entry to delete.
        match = re.search(
            r"BASELINE GREW: src/views/P\.vue \(baseline line (\d+)\)", result.stdout
        )
        self.assertIsNotNone(match, result.stdout)
        file_lines = self.baseline.read_text(encoding="utf-8").splitlines()
        self.assertIn("src/views/P.vue", file_lines[int(match.group(1)) - 1])
        self.assertEqual(before, self.baseline.read_bytes())

        # The same entry list rides on stderr under --json, stdout staying one line.
        as_json = self.run_gate("--base", approved, "--json", rc=1)
        self.assertEqual(
            len(as_json.stdout.strip().splitlines()), 1, as_json.stdout
        )
        self.assertIn("BASELINE GREW: src/views/P.vue", as_json.stderr)
        self.assertIn("(baseline line", as_json.stderr)


if __name__ == "__main__":
    unittest.main()
