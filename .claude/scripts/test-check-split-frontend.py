#!/usr/bin/env python3
"""Black-box contracts: deliberately broken splits must fail; repaired ones pass.

No implementation imports. Each case owns a temporary git repository, ignores
system/global git configuration, disables signing and never installs hooks.
CHECK_SPLIT_FRONTEND may select a mutated copy for a red/green experiment.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

CLI = Path(os.environ.get("CHECK_SPLIT_FRONTEND", Path(__file__).with_name("check-split-frontend.py"))).resolve()


def sfc(script: str = "", template: str = '<button id="action" aria-label="Action">Go</button>') -> str:
    return f'<script setup lang="ts">\n{script}\n</script>\n<template>{template}</template>\n'


class SplitContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="split-frontend-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_NAME="Split test", GIT_AUTHOR_EMAIL="split@example.invalid",
                        GIT_COMMITTER_NAME="Split test", GIT_COMMITTER_EMAIL="split@example.invalid")
        self.git("init", "-q")

    def git(self, *args):
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                                 "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *args],
                                env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write(self, file, text):
        p = self.root / file
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def baseline(self, text, companions=None):
        files = {"Panel.vue": text, **(companions or {})}
        for file, content in files.items():
            self.write(file, content)
        self.git("add", "--", *files)
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD")

    def invoke(self, expected, files=("Panel.vue",), *, base=None, cli=CLI, env=None, extra=()):
        command = [sys.executable, str(cli), "--repo-root", str(self.root), "--base-ref", base or self.base,
                   "--old-file", "Panel.vue"]
        for file in files:
            command += ["--new-file", file]
        result = subprocess.run([*command, *extra], env=env or self.env, cwd=self.tmp.name,
                                capture_output=True, text=True, timeout=100)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, output)
        return output

    def test_aria_loss_and_repair(self):
        self.baseline(sfc())
        self.write("Panel.vue", sfc(template='<button id="action">Go</button>'))
        self.assertIn("[1 aria]", self.invoke(1))
        self.write("Panel.vue", sfc())
        self.invoke(0)

    def test_aria_false_is_not_undefined(self):
        script = "const props = defineProps<{ expanded?: boolean }>()"
        original = sfc(script, '<button id="action" :aria-expanded="props.expanded ?? false"/>')
        self.baseline(original)
        self.write("Panel.vue", original.replace("props.expanded ?? false", "props.expanded ? props.expanded : undefined"))
        self.assertIn("aria-expanded", self.invoke(1))
        self.write("Panel.vue", original)
        self.invoke(0)

    def test_unrelated_element_cannot_compensate(self):
        self.baseline(sfc(template='<button id="one" aria-label="First"/><button id="two"/>'))
        self.write("Panel.vue", sfc(template='<button id="one"/><button id="two" aria-label="First"/>'))
        self.invoke(1)

    def test_child_migration_and_cross_file_compensation(self):
        self.baseline(sfc(template='<button id="one" aria-label="First"/><button id="two"/>'))
        self.write("Panel.vue", sfc("import Child from './Child.vue'", '<Child/><button id="two"/>'))
        self.write("Child.vue", sfc(template='<button id="one" aria-label="First"/>'))
        self.invoke(0, ("Panel.vue", "Child.vue"))
        self.write("Panel.vue", sfc("import Child from './Child.vue'", '<Child/><button id="two" aria-label="First"/>'))
        self.write("Child.vue", sfc(template='<button id="one"/>'))
        self.invoke(1, ("Panel.vue", "Child.vue"))

    def test_unrendered_child_cannot_supply_aria(self):
        self.baseline(sfc())
        self.write("Panel.vue", sfc("import './Child.vue'", '<div/>'))
        self.write("Child.vue", sfc())
        self.invoke(2, ("Panel.vue", "Child.vue"))
        self.write("Panel.vue", sfc("import Child from './Child.vue'", '<div/>'))
        self.invoke(2, ("Panel.vue", "Child.vue"))
        self.write("Panel.vue", sfc("import Child from './Child.vue'", '<Child/>'))
        self.invoke(0, ("Panel.vue", "Child.vue"))

    def test_duplicate_and_unanchored_elements_are_unjudged(self):
        self.baseline(sfc(template='<button id="one" aria-label="First"/><button id="one"/>'))
        self.assertIn("ambiguous", self.invoke(2))
        self.write("Panel.vue", sfc(template='<button aria-label="First"/>'))
        self.git("add", "Panel.vue")
        self.git("commit", "-qm", "unanchored baseline")
        self.base = self.git("rev-parse", "HEAD")
        self.assertIn("static id/data-testid", self.invoke(2))

    def test_type_only_name_missing_and_repair(self):
        self.baseline(sfc("type widget = { id: string }; const list: Array<widget> = []"))
        self.write("Panel.vue", sfc("import { list } from './state'; void list"))
        self.write("state.ts", "export const list: Array<widget> = []")
        self.assertIn("widget", self.invoke(1, ("Panel.vue", "state.ts")))
        self.write("state.ts", "type widget = { id: string }; export const list: Array<widget> = []")
        self.invoke(0, ("Panel.vue", "state.ts"))

    def test_aliases_generics_builtins_and_lexical_types(self):
        script = '''type box<t> = Readonly<{value: t}>;
        function make<t extends object>(value: t): box<t> { return {value}; }
        const values: Map<string, Set<Partial<Record<string, unknown>>>>> = new Map();
        void values; void make;'''.replace(">>>>>", ">>>>")
        self.baseline(sfc(script))
        self.invoke(0)
        self.write("Panel.vue", sfc(script + " const outside: t = {}"))
        self.assertIn("Cannot find name 't'", self.invoke(1))

    def test_both_script_blocks_share_types_not_setup_locals(self):
        original = '<script lang="ts">type row = {id: string}</script>' + sfc("const rows: row[] = []")
        self.baseline(original)
        self.invoke(0)
        self.write("Panel.vue", sfc("const rows: row[] = []"))
        self.invoke(1)

    def watch_baseline(self):
        self.baseline(sfc("import { watch } from 'vue'; const props = defineProps<{open: boolean}>(); "
                          "watch(() => props.open, () => {});"))

    def migrated_watch(self, child):
        self.write("Panel.vue", sfc("import { usePanel } from './usePanel'; "
                                  "const props = defineProps<{open: boolean}>(); usePanel(props);"))
        self.write("usePanel.ts", child)

    def test_watch_loss_and_real_props_migration(self):
        self.watch_baseline()
        self.migrated_watch("export function usePanel(p: {open: boolean}) {}")
        self.assertIn("[1 watch]", self.invoke(1, ("Panel.vue", "usePanel.ts")))
        self.migrated_watch("import {watch as observe} from 'vue'; export function usePanel(p: {open: boolean}) "
                            "{ observe(() => p.open, () => {}); }")
        self.invoke(0, ("Panel.vue", "usePanel.ts"))

    def test_fake_watch_and_fake_props_do_not_compensate(self):
        self.watch_baseline()
        self.migrated_watch("export function usePanel(props: {open: boolean}) { "
                            "function watch(...args: any[]) {} watch(() => props.open, () => {}); }")
        self.invoke(1, ("Panel.vue", "usePanel.ts"))
        self.migrated_watch("import {watch} from 'vue'; export function usePanel(p: {open: boolean}) { "
                            "const props = {open: false}; watch(() => props.open, () => {}); }")
        self.invoke(1, ("Panel.vue", "usePanel.ts"))
        self.migrated_watch("import * as Vue from 'vue'; export function usePanel(p: {open: boolean}) { "
                            "Vue.watch(() => p.open, () => {}); }")
        self.invoke(0, ("Panel.vue", "usePanel.ts"))

    def test_unused_helper_watch_does_not_compensate(self):
        self.watch_baseline()
        self.write("Panel.vue", sfc("import {watch} from 'vue'; const props = defineProps<{open: boolean}>(); "
                                  "function unused() { watch(() => props.open, () => {}); }"))
        self.invoke(1)

    def test_watch_callback_options_and_multiplicity(self):
        original = sfc("import {watch} from 'vue'; const props = defineProps<{open: boolean}>(); "
                       "watch(() => props.open, () => {}, {immediate: true}); "
                       "watch(() => props.open, () => {}, {immediate: true});")
        self.baseline(original)
        self.write("Panel.vue", original.replace("{immediate: true}", "{immediate: false}", 1))
        self.invoke(1)
        self.write("Panel.vue", original)
        self.invoke(0)

    def test_missing_export_and_repair(self):
        self.baseline(sfc("const size = 5; void size"))
        self.write("Panel.vue", sfc("import { size } from './state'; void size"))
        self.write("state.ts", "const size = 5; export {}")
        self.assertIn("typescript", self.invoke(1, ("Panel.vue", "state.ts")))
        self.write("state.ts", "export const size = 5")
        self.invoke(0, ("Panel.vue", "state.ts"))

    def test_missing_return_and_binding_of_dom_global(self):
        self.baseline(sfc("function blur() {} blur();"))
        self.write("Panel.vue", sfc("import {usePanel} from './usePanel'; usePanel(); blur();"))
        self.write("usePanel.ts", "export function usePanel() { function blur() {} return {}; }")
        self.assertIn("formerly local blur", self.invoke(1, ("Panel.vue", "usePanel.ts")))
        self.write("Panel.vue", sfc("import {usePanel} from './usePanel'; const {blur} = usePanel(); blur();"))
        self.invoke(1, ("Panel.vue", "usePanel.ts"))
        self.write("usePanel.ts", "export function usePanel() { function blur() {} return {blur}; }")
        self.invoke(0, ("Panel.vue", "usePanel.ts"))

    def test_template_global_fallback_and_local_shadow(self):
        self.baseline(sfc("function close() {}", '<button id="action" @click="close()" aria-label="Close"/>'))
        self.write("Panel.vue", sfc("", '<button id="action" @click="close()" aria-label="Close"/>'))
        self.invoke(1)
        self.write("Panel.vue", sfc("const close = () => {}; function inner(close: () => void) { close(); }",
                                  '<button id="action" @click="close()" aria-label="Close"/>'))
        self.invoke(0)

    def test_destructured_local_binding_cannot_fall_back_to_global(self):
        declarations = [
            "const {blur} = {blur: () => {}};",
            "const {action: blur} = {action: () => {}};",
            "const [{nested: [blur]}] = [{nested: [() => {}]}];",
            "const [blur = () => {}] = [];",
        ]
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                self.baseline(sfc(declaration + " blur();"))
                self.write("Panel.vue", sfc("import {usePanel} from './usePanel'; usePanel(); blur();"))
                self.write("usePanel.ts", "export function usePanel() { " + declaration + " return {}; }")
                self.assertIn("formerly local blur", self.invoke(1, ("Panel.vue", "usePanel.ts")))
                self.write("Panel.vue", sfc("import {usePanel} from './usePanel'; const {blur} = usePanel(); blur();"))
                self.write("usePanel.ts", "export function usePanel() { " + declaration + " return {blur}; }")
                self.invoke(0, ("Panel.vue", "usePanel.ts"))

    def test_unused_nested_rest_binding_is_still_a_local_export_candidate(self):
        declaration = "const {actions: {...blur}} = {actions: {}};"
        self.baseline(sfc(declaration))
        self.write("Panel.vue", sfc("import {usePanel} from './usePanel'; const api = usePanel(); void api;"))
        self.write("usePanel.ts", "export function usePanel() { return {blur}; }")
        self.assertIn("formerly local blur", self.invoke(1, ("Panel.vue", "usePanel.ts")))
        self.write("usePanel.ts", "export function usePanel() { " + declaration + " return {blur}; }")
        self.invoke(0, ("Panel.vue", "usePanel.ts"))

    def test_shorthand_export_uses_its_value_binding(self):
        self.baseline(sfc("function blur() {} blur();"))
        self.write("Panel.vue", sfc("import {usePanel} from './usePanel'; const api = usePanel(); api.blur();"))
        for returned in ("{blur}", "{blur: blur}"):
            with self.subTest(returned=returned):
                self.write("usePanel.ts", "export function usePanel() { return " + returned + "; }")
                self.assertIn("formerly local blur", self.invoke(1, ("Panel.vue", "usePanel.ts")))
                self.write("usePanel.ts", "export function usePanel() { const blur = () => {}; return " + returned + "; }")
                self.invoke(0, ("Panel.vue", "usePanel.ts"))

    def test_aria_property_binding_no_longer_supplies_attribute(self):
        original = sfc("const label = 'Action'", '<button id="action" :aria-label="label"/>')
        self.baseline(original)
        self.write("Panel.vue", original.replace(":aria-label=", ":aria-label.prop="))
        self.assertIn("[1 aria]", self.invoke(1))
        self.write("Panel.vue", original)
        self.invoke(0)
        self.write("Panel.vue", original.replace(":aria-label=", ":aria-label.attr="))
        self.invoke(0)

    def test_unchanged_import_with_unrelated_local_shadow(self):
        original = sfc("import {ref} from 'vue'; const value = ref(0); "
                       "function unused() { const ref = 1; return ref; } void value;")
        self.baseline(original)
        self.invoke(0)
        self.baseline(original.replace("const ref = 1; return ref;", "const inner = 1; return inner;"))
        self.invoke(0)

    def test_extracted_shadow_preserves_preexisting_global_reference(self):
        helper = "function helper() { const blur = () => {}; blur(); }"
        self.baseline(sfc(helper + " helper(); blur();"))
        self.write("Panel.vue", sfc("import {helper} from './helper'; helper(); blur();"))
        self.write("helper.ts", "export " + helper)
        self.invoke(0, ("Panel.vue", "helper.ts"))
        self.write("helper.ts", "export function helper() { blur(); }")
        self.assertIn("formerly local blur", self.invoke(1, ("Panel.vue", "helper.ts")))

    def test_existing_global_does_not_whitelist_new_fallback(self):
        original = sfc("function systemAction() { blur(); } "
                       "function localAction() { const blur = () => {}; blur(); } "
                       "systemAction(); localAction();")
        self.baseline(original)
        self.invoke(0)
        self.write("Panel.vue", original.replace("const blur = () => {}; ", ""))
        self.invoke(1)
        # Equal total global-use counts must not hide a different reference's loss.
        self.write("Panel.vue", original.replace("function systemAction() { blur(); }", "function systemAction() {}")
                   .replace("const blur = () => {}; ", ""))
        self.invoke(1)
        self.write("Panel.vue", original)
        self.invoke(0)

    def test_ambiguous_lexical_relocation_cannot_be_whitelisted(self):
        original = sfc("function systemAction() { blur(); } "
                       "function localAction() { const blur = () => {}; blur(); } "
                       "systemAction(); localAction();")
        self.baseline(original)
        # Removing/renaming the local scope is ambiguous even if every remaining
        # external expression matches a pre-existing global expression.
        self.write("Panel.vue", sfc("function systemAction() { blur(); } systemAction();"))
        self.assertIn("ambiguous lexical provenance", self.invoke(2))
        self.write("Panel.vue", original)
        self.invoke(0)

    def test_user_marker_cannot_suppress_script_type_diagnostic(self):
        original = sfc("const marker = '/* template probes */'; const rows: string[] = []; void marker;")
        self.baseline(original)
        self.write("Panel.vue", original.replace("string[]", "Array<missingType>"))
        self.assertIn("missingType", self.invoke(1))
        self.write("Panel.vue", original)
        self.invoke(0)

    def test_missing_baseline_files_syntax_and_scope(self):
        self.baseline(sfc())
        self.invoke(2, base="not-a-ref")
        self.invoke(2, ("Panel.vue", "missing.ts"))
        self.write("Panel.vue", sfc("const value: = ;"))
        self.invoke(2)
        self.write("Panel.vue", '<template><div></template>')
        self.invoke(2)
        self.write("Panel.vue", sfc("import {x} from './omitted'; void x"))
        self.write("omitted.ts", "export const x = 1")
        self.invoke(2)
        self.write("Panel.vue", sfc())
        self.write("unrelated.ts", "export const x = 1")
        self.invoke(2, ("Panel.vue", "unrelated.ts"))

    def test_unsupported_scope_fails_closed(self):
        self.baseline(sfc())
        # Keep the original anchor and attribute; do not reference the loop
        # variable. Only the unsupported v-for scope may make this unjudged.
        self.write("Panel.vue", sfc("const list = [1]", '<button v-for="n in list" id="action" aria-label="Action">Go</button>'))
        self.assertIn("template scope/spread/dynamic argument", self.invoke(2))
        self.write("Panel.vue", sfc("const list = [1]"))
        self.invoke(0)

    def test_missing_dependency_does_not_fall_back_to_fixture(self):
        self.baseline(sfc())
        isolated = Path(self.tmp.name) / "isolated/.claude/scripts/check-split-frontend.py"
        isolated.parent.mkdir(parents=True)
        shutil.copyfile(CLI, isolated)
        # A dependency tree in the inspected repository must not be consulted.
        (self.root / "frontend").mkdir()
        (self.root / "frontend/node_modules").symlink_to(CLI.parents[2] / "frontend/node_modules", target_is_directory=True)
        self.assertIn("missing parser dependency", self.invoke(2, cli=isolated))

    def test_missing_node_and_self_test_argument_contract(self):
        self.baseline(sfc())
        bin_dir = Path(self.tmp.name) / "bin"
        bin_dir.mkdir()
        (bin_dir / "git").symlink_to(shutil.which("git"))
        self.assertIn("node is unavailable", self.invoke(2, env={**self.env, "PATH": str(bin_dir)}))
        result = subprocess.run([sys.executable, str(CLI), "--self-test", "--base-ref", self.base],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be mixed", result.stderr)

    def test_indirect_watch_and_suppression_are_unjudged(self):
        self.baseline(sfc("import {toRef, watch} from 'vue'; const props = defineProps<{open: boolean}>(); "
                          "const open = toRef(props, 'open'); watch(open, () => {});"))
        self.assertIn("indirect", self.invoke(2))
        self.write("Panel.vue", sfc("const value = 1"))
        self.git("add", "Panel.vue")
        self.git("commit", "-qm", "simple baseline")
        self.base = self.git("rev-parse", "HEAD")
        self.write("Panel.vue", sfc("// @ts-ignore\nconst value: missingType = 1"))
        self.assertIn("suppression", self.invoke(2))

    def test_conditional_composable_is_not_proof_of_watch(self):
        self.watch_baseline()
        self.migrated_watch("import {watch} from 'vue'; export function usePanel(p: {open: boolean}) "
                            "{ watch(() => p.open, () => {}); }")
        self.write("Panel.vue", sfc("import { usePanel } from './usePanel'; "
                                  "const props = defineProps<{open: boolean}>(); if (false) usePanel(props);"))
        self.invoke(2, ("Panel.vue", "usePanel.ts"))

    def test_project_source_is_not_executed(self):
        self.baseline(sfc())
        self.write("Panel.vue", sfc("import './effect';"))
        self.write("effect.ts", "throw new Error('must never execute'); export {}")
        self.invoke(0, ("Panel.vue", "effect.ts"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
