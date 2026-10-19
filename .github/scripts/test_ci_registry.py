"""Keeps the shadow registry equal to the tables that still select work.

Stage 0 adds no behaviour: ci-fast.py, ci_fast_checks.py, ci_fast_backend.py
and the workflows still decide what runs. What these tests buy is that the
declaration in ci_registry.py cannot drift away from them unnoticed — a hook
moved between tables, a plan item added, a guard dropped, a trigger aimed at a
directory this repository no longer has.

The comparisons are functions of the old table, not of the live one alone, so
each kind of drift is shown to fail: the last test in each group feeds a
mutated copy in and asserts the mismatch is reported.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest

from ci_registry import (
    CI_STEPS,
    HOOKS,
    PLAN_CHECKS,
    REGISTRY,
    TREE_GUARD_READS,
    TREE_GUARDS,
    tree_guards_for,
)

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONFIG = REPO / ".pre-commit-config.yaml"
WORKFLOWS = REPO / ".github" / "workflows"


def _literal_assignment(path: Path, name: str):
    """A module-level literal assignment, read without importing the module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path.name} has no module-level {name}")


def _hook_ids_in_config() -> list[str]:
    return re.findall(r"^\s*-\s+id:\s*(\S+)", CONFIG.read_text(encoding="utf-8"), re.MULTILINE)


def _plan_ids() -> list[str]:
    """Every check id ci-fast writes as a row of its own.

    The plan items are literal ``"id":`` entries in ci_fast_checks.py; a check
    ci-fast spawns directly names its id in an ``invoke(...)`` call instead
    (commit-title). Both are rows in the report, so both are registered here.
    """
    plan = (HERE / "ci_fast_checks.py").read_text(encoding="utf-8")
    driver = (HERE / "ci-fast.py").read_text(encoding="utf-8")
    return re.findall(r'"id": "([^"]+)"', plan) + re.findall(r'invoke\("([^"]+)"', driver)


FAST_HOOKS = _literal_assignment(HERE / "ci-fast.py", "FAST_HOOKS")
TYPES_HOOKS = _literal_assignment(HERE / "ci-fast.py", "TYPES_HOOKS")
PRE_PUSH_HOOKS = _literal_assignment(HERE / "ci-fast.py", "PRE_PUSH_HOOKS")
BACKEND_TREE_GUARDS = _literal_assignment(HERE / "ci_fast_backend.py", "TREE_GUARDS")

#: The lane each old table stands for. A hook found in none of them is one no
#: ci-fast tier selects, which is what `not_local` says.
LANES = {
    "every_plan": FAST_HOOKS["guards"],
    "backend_scope": FAST_HOOKS["backend"],
    "frontend_scope": FAST_HOOKS["frontend"],
    "types_backend": TYPES_HOOKS["backend"],
    "types_frontend": TYPES_HOOKS["frontend"],
    "pre_push_only": PRE_PUSH_HOOKS["backend"],
}


def _unregistered(expected, specs) -> list[str]:
    known = {spec.id for spec in specs}
    return sorted(set(expected) - known)


def _lane_conflicts(specs, lanes) -> list[str]:
    """Hooks whose declared lane disagrees with the table that names them."""
    conflicts = []
    for spec in specs:
        if spec.kind != "hook":
            continue
        named_by = sorted(lane for lane, ids in lanes.items() if spec.id in ids)
        if not named_by:
            if spec.when != "not_local":
                conflicts.append(f"{spec.id}: no old table selects it, declared {spec.when}")
        elif len(named_by) > 1:
            conflicts.append(f"{spec.id}: named by several tables {named_by}")
        elif named_by[0] != spec.when:
            conflicts.append(f"{spec.id}: old table says {named_by[0]}, registry says {spec.when}")
    return conflicts


def _missing_paths(specs, root: Path) -> list[str]:
    """Triggers that name a directory or file this repository does not have."""
    missing = []
    for spec in specs:
        for pattern in spec.triggers:
            prefix = re.sub(r"/\*\*$", "", pattern)
            if not (root / prefix).exists():
                missing.append(f"{spec.id}: trigger {pattern!r} points at nothing")
    return missing


class RegistryTest(unittest.TestCase):
    def test_every_check_declares_a_runnable_command(self):
        for spec in REGISTRY:
            if spec.report_only:
                continue
            self.assertTrue(spec.run, f"{spec.id}: no command to run")

    def test_ids_are_unique(self):
        ids = [spec.id for spec in REGISTRY]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_hook_the_config_defines_is_registered(self):
        self.assertEqual(_unregistered(_hook_ids_in_config(), HOOKS), [])

    def test_the_registry_declares_no_hook_the_config_does_not(self):
        config = set(_hook_ids_in_config())
        self.assertEqual(sorted(spec.id for spec in HOOKS if spec.id not in config), [])

    def test_every_hook_sits_in_the_lane_of_the_table_that_selects_it(self):
        self.assertEqual(_lane_conflicts(HOOKS, LANES), [])

    def test_every_plan_item_ci_fast_emits_is_registered(self):
        self.assertEqual(_unregistered(_plan_ids(), PLAN_CHECKS), [])

    def test_the_registry_declares_no_plan_item_ci_fast_never_emits(self):
        emitted = set(_plan_ids())
        self.assertEqual(sorted(spec.id for spec in PLAN_CHECKS if spec.id not in emitted), [])

    def test_every_tree_guard_the_backend_selects_is_registered(self):
        self.assertEqual(_unregistered(BACKEND_TREE_GUARDS, TREE_GUARDS), [])
        self.assertEqual(sorted(spec.id for spec in TREE_GUARDS
                                if spec.id not in set(BACKEND_TREE_GUARDS)), [])

    def test_every_ci_step_names_a_script_a_workflow_runs(self):
        """Each entry's command has to name a script that appears in a workflow.

        The script is the first token of any argument that ends in ``.py`` or
        ``.sh`` — a command in this table carries its flags in the same
        argument (``bash .claude/scripts/x.sh --self-test``), so looking at the
        whole argument would skip every entry that has one.
        """
        text = "\n".join(p.read_text(encoding="utf-8") for p in WORKFLOWS.glob("*.yml"))
        for spec in CI_STEPS:
            scripts = [token for part in spec.run for token in part.split()
                       if token.endswith((".py", ".sh"))]
            self.assertTrue(scripts, f"{spec.id}: names no script")
            self.assertIn(scripts[0], text, f"{spec.id}: {scripts[0]} is in no workflow")

    def test_a_trigger_names_something_this_repository_has(self):
        self.assertEqual(_missing_paths(REGISTRY, REPO), [])

    def test_a_change_selects_only_the_guards_that_read_what_it_touched(self):
        def selected(changed):
            return set(tree_guards_for(changed))

        # Backend source: every guard that walks the backend tree runs.
        backend = selected(["backend/app/api/routes/topics.py"])
        self.assertIn("test_domain_import_guard.py", backend)
        self.assertIn("test_sentences.py", backend)
        self.assertNotIn("test_metering_proxy_addon.py", backend)
        self.assertNotIn("test_changed_runner.py", backend)

        # A frontend catalog: the sentence guard reads it, and the three guards
        # that walk frontend/src read that tree — the rest of the backend-tree
        # guards do not run.
        self.assertEqual(
            selected(["frontend/src/i18n/messages/zh-CN/global.json"]),
            {"test_sentences.py", "test_harness_boundary.py",
             "test_nothing_reads_the_model_off_a_type_or_an_instance.py",
             "test_the_platform_never_starts_a_turn.py",
             "test_one_roster_agents_included.py"},
        )

        # The metering proxy: one guard reads that tree, and the version-pin
        # guard reads all of deploy/.
        self.assertEqual(selected(["deploy/metering-proxy/compose.yml"]),
                         {"test_metering_proxy_addon.py", "test_postgres_version_pins.py"})

        # The scripts a workflow pins: the guards that read them.
        self.assertEqual(
            selected([".claude/scripts/test-changed.py"]),
            {"test_changed_runner.py", "test_get_by_id_has_batch_sibling.py",
             "test_postgres_version_pins.py"},
        )

        # Prose a guard does not read: nothing runs.
        self.assertEqual(selected(["docs/topics/anything.md"]), set())


class DriftTest(unittest.TestCase):
    """Each comparison above fails when the old table and registry disagree."""

    def test_a_hook_missing_from_the_registry_is_reported(self):
        self.assertEqual(_unregistered(["repo-rules", "a-hook-nobody-registered"], HOOKS),
                         ["a-hook-nobody-registered"])

    def test_a_hook_in_the_wrong_lane_is_reported(self):
        moved = [spec for spec in HOOKS if spec.id != "ruff"]
        moved.append(next(spec for spec in HOOKS if spec.id == "ruff").__class__(
            "ruff", "hook", ("pre-commit", "run", "ruff"),
            frozenset({"python"}), "every_plan", ()))
        self.assertTrue(any("ruff" in line for line in _lane_conflicts(moved, LANES)))

    def test_a_plan_item_missing_from_the_registry_is_reported(self):
        self.assertEqual(_unregistered(["backend-pure", "a-plan-nobody-registered"], PLAN_CHECKS),
                         ["a-plan-nobody-registered"])

    def test_a_tree_guard_missing_from_the_registry_is_reported(self):
        self.assertEqual(
            _unregistered(["test_sentences.py", "test_a_guard_nobody_registered.py"], TREE_GUARDS),
            ["test_a_guard_nobody_registered.py"],
        )

    def test_a_trigger_pointing_at_a_missing_directory_is_reported(self):
        broken = [spec for spec in REGISTRY if spec.id == "tree-guards"]
        problems = _missing_paths(
            [type(broken[0])("tree-guards", "plan", ("pytest",), frozenset({"python"}),
                             "backend_scope", ("backend/no-such-tree/**",))],
            REPO,
        )
        self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()
