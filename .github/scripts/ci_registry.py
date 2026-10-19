"""Every check this repository runs, declared once, with where it belongs.

This is the shadow registry of stage 0. Nothing below selects work yet: the
old tables still decide what runs — ``FAST_HOOKS``/``TYPES_HOOKS``/
``PRE_PUSH_HOOKS`` in ci-fast.py, the plan branches in ci_fast_checks.py,
``TREE_GUARDS`` in ci_fast_backend.py, and the steps named in the workflows.
What this file adds is one place where each check says the three things a
selection needs:

- ``requires``: what the machine must have before the check is runnable at
  all. A check whose requirements the machine cannot meet is not a verdict
  about the change, so it is reported as not run rather than as a failure.
- ``when``: the lane it belongs to, from the closed set in ``WHEN``. Adding a
  value means adding a shared predicate and its tests, so the set cannot
  quietly grow a fourth kind of "sometimes".
- ``triggers``: the changed paths that make the check relevant, as globs.
  An empty tuple means the check's own declaration decides — for a
  pre-commit hook that is ``files:`` in .pre-commit-config.yaml, which is
  read by an outside tool and stays hand-written; a test holds the two lists
  to each other instead of restating it here.

``TREE_GUARD_READS`` is the one part that could not be copied from an existing
table: a whole-tree guard has no import edge to the files it judges, so
nothing says what it watches. It was measured on 2026-10-19 by running each
guard under an audit hook that records every ``open()`` under the repository
root (the measurement is described in the PR that added this file). The
triggers are derived from those readings. A guard that starts reading a new
tree has to say so here; otherwise it would silently stop running for changes
to the thing it now judges.

Nothing in this module decides what runs today. The tests in
test_ci_registry.py keep it equal to the tables that do.
"""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch

#: What a check can be, by how it is executed.
KINDS = frozenset({"hook", "plan", "tree-guard", "ci-step"})

#: What the machine must have. A check is runnable only when every requirement
#: is met; one that is not runnable is reported not run, never as a failure.
REQUIRES = frozenset({
    "python",          # an interpreter and the repository's scripts
    "node",            # the JS toolchain (node, pnpm)
    "node_modules",    # a checkout that ran `pnpm install`
    "frontend-src",    # frontend/src exists and is not empty
    "merge-base",      # origin/main reachable for the merge-base hooks
    "pinned-harness",  # the pinned Claude Code/Codex/pi builds
    "postgres",        # a database a test can connect to
    "windows",         # a Windows runner
})

#: Where a check runs. Closed on purpose: a new value is a new selection rule,
#: and it has to arrive with the predicate and tests that implement it.
WHEN = frozenset({
    "every_plan",       # every tier, whichever suites are in scope
    "quick_only",       # quick and extended; the static tier does not build a plan
    "static_only",      # the static tier and the pre-push entry point
    "backend_scope",    # when the shared path map routes the change to backend
    "frontend_scope",   # when it routes the change to frontend
    "guards_scope",     # when it routes the change to guards
    "types_backend",    # quick tier with --types
    "types_frontend",   # quick tier with --types
    "pre_push_only",    # added by the pre-push entry point
    "full_only",        # Required CI's full tier on main; never a pull request
    "not_local",        # never selected by ci-fast
})


@dataclass(frozen=True)
class CheckSpec:
    """One check: what it is called, how it runs, and where it belongs."""

    id: str
    kind: str
    run: tuple[str, ...]
    requires: frozenset[str]
    when: str
    triggers: tuple[str, ...] = ()
    #: A report-only id: it names a row the tool writes when it leaves
    #: something out (the pinned-harness omission), so it has no command of its
    #: own. Everything else must say how it runs.
    report_only: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.id}: unknown kind {self.kind!r}")
        if self.when not in WHEN:
            raise ValueError(f"{self.id}: unknown when {self.when!r}")
        unknown = self.requires - REQUIRES
        if unknown:
            raise ValueError(f"{self.id}: unknown requires {sorted(unknown)}")
        if not self.id:
            raise ValueError("a check needs an id")


def _spec(identifier, kind, run, requires, when, triggers=(), report_only=False):
    return CheckSpec(identifier, kind, run, frozenset(requires), when, tuple(triggers), report_only)


# --- pre-commit hooks -------------------------------------------------------
#
# ``requires`` is read off each hook's entry point: the ones that run `pnpm`,
# `node` or a frontend script need the node toolchain and an installed tree;
# the rest run python or bash. ``triggers`` is empty because the config's
# `files:` is the declaration for a hook, and the test keeps the two in step.
HOOKS = (
    _spec("repo-rules", "hook", ("pre-commit", "run", "repo-rules"), {"python"}, "every_plan"),
    _spec("action-pins", "hook", ("pre-commit", "run", "action-pins"), {"python"}, "every_plan"),
    _spec("manual-anchors", "hook", ("pre-commit", "run", "manual-anchors"), {"python"}, "every_plan"),
    _spec("doc-references", "hook", ("pre-commit", "run", "doc-references"), {"python"}, "every_plan"),
    _spec("ruff", "hook", ("pre-commit", "run", "ruff"), {"python"}, "backend_scope"),
    _spec("boundary-check", "hook", ("pre-commit", "run", "boundary-check"),
          {"python", "merge-base"}, "backend_scope"),
    _spec("migration-fork", "hook", ("pre-commit", "run", "migration-fork"),
          {"python", "merge-base"}, "backend_scope"),
    _spec("eslint", "hook", ("pre-commit", "run", "eslint"),
          {"node", "node_modules"}, "frontend_scope"),
    _spec("stylelint", "hook", ("pre-commit", "run", "stylelint"),
          {"node", "node_modules"}, "frontend_scope"),
    _spec("boundary-check-frontend", "hook", ("pre-commit", "run", "boundary-check-frontend"),
          {"node", "node_modules"}, "frontend_scope"),
    _spec("scene-ratchet", "hook", ("pre-commit", "run", "scene-ratchet"),
          {"python", "merge-base"}, "frontend_scope"),
    _spec("pyright", "hook", ("pre-commit", "run", "pyright"), {"python"}, "types_backend"),
    _spec("frontend-typecheck", "hook", ("pre-commit", "run", "frontend-typecheck"),
          {"node", "node_modules", "frontend-src"}, "types_frontend"),
    _spec("openapi-document", "hook", ("pre-commit", "run", "openapi-document"),
          {"python"}, "pre_push_only"),
    # Not in any ci-fast lane. They stay executable units — pre-commit runs
    # them from a developer's commit — but the tier this file is about never
    # selects them, and saying so is what keeps "why did this not run here"
    # answerable.
    _spec("ruff-format", "hook", ("pre-commit", "run", "ruff-format"), {"python"}, "not_local"),
    _spec("module-split", "hook", ("pre-commit", "run", "module-split"), {"python"}, "not_local"),
    _spec("migration-safety", "hook", ("pre-commit", "run", "migration-safety"),
          {"python", "merge-base"}, "not_local"),
    _spec("row-read-in-loop", "hook", ("pre-commit", "run", "row-read-in-loop"), {"python"}, "not_local"),
    _spec("frontend-format", "hook", ("pre-commit", "run", "frontend-format"),
          {"node", "node_modules"}, "not_local"),
    _spec("i18n-cjk", "hook", ("pre-commit", "run", "i18n-cjk"), {"node", "node_modules"}, "not_local"),
    _spec("global-fallback", "hook", ("pre-commit", "run", "global-fallback"),
          {"node", "node_modules"}, "not_local"),
    _spec("sfc-attribute-diff", "hook", ("pre-commit", "run", "sfc-attribute-diff"),
          {"node", "node_modules"}, "not_local"),
    _spec("sfc-attribute-diff-self-test", "hook",
          ("pre-commit", "run", "sfc-attribute-diff-self-test"), {"node", "node_modules"}, "not_local"),
    _spec("scene-debt-ratchet", "hook", ("pre-commit", "run", "scene-debt-ratchet"),
          {"python", "merge-base"}, "not_local"),
    _spec("pytest", "hook", ("pre-commit", "run", "pytest"),
          {"python", "postgres", "pinned-harness"}, "not_local"),
    _spec("frontend-build", "hook", ("pre-commit", "run", "frontend-build"),
          {"node", "node_modules", "frontend-src"}, "not_local"),
)

# --- plan items: what ci-fast runs itself, by tier ---------------------------
#
# These are not hooks: `pre-commit run <id>` answers "No hook with id ...".
# ci-fast spawns them in their own tree with their own environment.
PLAN_CHECKS = (
    _spec("commit-title", "plan", ("python", "check-commit-title.py"),
          {"python"}, "quick_only", ()),
    _spec("frontend-unit", "plan", ("pnpm", "exec", "vitest", "run"),
          {"node", "node_modules", "frontend-src"}, "quick_only", ("frontend/**",)),
    _spec("frontend-i18n", "plan", ("pnpm", "exec", "vitest", "run"),
          {"node", "node_modules", "frontend-src"}, "quick_only",
          ("frontend/src/i18n/messages/**",)),
    _spec("frontend-typecheck-changed", "plan", ("node", "scripts/typecheck.mjs"),
          {"node", "node_modules", "frontend-src"}, "quick_only", ("frontend/**",)),
    _spec("frontend-typecheck-full", "plan", ("pnpm", "run", "typecheck"),
          {"node", "node_modules", "frontend-src"}, "quick_only", ("frontend/**",)),
    _spec("backend-pure", "plan", ("uv", "run", "pytest"),
          {"python"}, "quick_only", ("backend/**",)),
    # Not a run of its own: the omission ci-fast records when the pinned
    # harness builds are absent. It is a check id in the report, and the
    # reason a selection differs between a runner that has them and one that
    # does not, so it is declared here like the rest.
    _spec("backend-pure:pinned-harness", "plan", (), {"python"}, "quick_only", ("backend/**",),
          report_only=True),
    # Only the static tier runs the guards as a check of their own; the quick
    # tier folds the same files into backend-pure's selection.
    _spec("tree-guards", "plan", ("uv", "run", "pytest"),
          {"python"}, "static_only", ("backend/**",)),
)

# --- whole-tree guards ------------------------------------------------------
#
# A guard reads the source tree instead of importing what it constrains, so the
# reverse import graph never selects it. It runs whenever the backend is in
# scope today; the triggers below are what it was measured to read, which is
# what a later stage will select on. `backend/**` covers the whole backend tree
# because the guard walks it; the narrower entries are the only reason to run a
# guard when no backend source changed (`docs/i18n.md`, the i18n catalogs, the
# workflows, `deploy/`).
TREE_GUARD_READS = {
    "test_domain_import_guard.py": ("backend/**", "backend/.importlinter"),
    "test_a_refusal_is_said_with_a_key.py": ("backend/**",),
    "test_sentences.py": ("backend/**", "frontend/src/i18n/messages/**", "docs/i18n.md"),
    "test_is_private_read_points.py": ("backend/**",),
    "test_harness_boundary.py": ("backend/**", "frontend/src/**"),
    "test_actor_is_not_typed.py": ("backend/**",),
    "test_author_type_two_values.py": ("backend/**", "scripts/**", "evals/**"),
    "test_nothing_reads_the_model_off_a_type_or_an_instance.py":
        ("backend/**", "frontend/src/**"),
    "test_social_notifications_through_the_ledger.py": ("backend/**",),
    "test_remote_hub_answers_every_hub_call.py": ("backend/**",),
    "test_the_platform_never_starts_a_turn.py": ("backend/**", "frontend/src/**"),
    "test_one_roster_agents_included.py": ("backend/**", "frontend/src/**"),
    "test_local_fs_sharing_guard.py": ("backend/**",),
    "test_get_by_id_has_batch_sibling.py": ("backend/**", ".claude/scripts/**"),
    "test_repositories_read_by_id_through_the_base.py": ("backend/**",),
    "test_changed_runner.py": (".claude/scripts/**",),
    "test_postgres_version_pins.py":
        (".github/workflows/**", "deploy/**", ".claude/scripts/**", "backend/alembic/**",
         "backend/scripts/device_connection_lifecycle_acceptance.py", "docker-compose.yml"),
    "test_event_landing_guard.py": ("backend/**", "scripts/**", "evals/**"),
    "test_metering_proxy_addon.py": ("deploy/metering-proxy/**",),
    "test_known_debt_is_not_an_xfail.py": ("backend/tests/**",),
    "test_dead_definitions_stay_dead.py": ("backend/**",),
    "test_success_envelope_single_home.py": ("backend/**",),
}
TREE_GUARDS = tuple(
    _spec(name, "tree-guard", ("pytest", f"tests/unit/{name}"),
          {"python"}, "backend_scope", triggers)
    for name, triggers in TREE_GUARD_READS.items()
)

# --- CI steps that live in a workflow, not in ci-fast ----------------------
#
# Registering them is what makes "this check never runs on a pull request"
# answerable from one place: `when` says the full tier owns them.
CI_STEPS = (
    _spec("guards-self-test", "ci-step", ("bash", ".claude/scripts/check-repo-rules.sh --self-test"),
          {"python"}, "full_only", (".claude/scripts/**",)),
    _spec("regression-proof-self-test", "ci-step",
          ("python3", ".claude/scripts/prove-regression.py --self-test"),
          {"python"}, "full_only", (".claude/scripts/**",)),
    _spec("trace-tooling-self-test", "ci-step",
          ("python3", ".claude/scripts/trace-sweep.py --self-test"),
          {"python"}, "full_only", (".claude/scripts/**",)),
    _spec("migration-safety-tree-check", "ci-step",
          ("python3", ".claude/scripts/check-migration-safety.py"),
          {"python", "merge-base"}, "full_only", ("backend/alembic/**",)),
)

REGISTRY = HOOKS + PLAN_CHECKS + TREE_GUARDS + CI_STEPS

BY_ID = {check.id: check for check in REGISTRY}

if len(BY_ID) != len(REGISTRY):  # four tables, one id per check
    _dupes = sorted({c.id for c in REGISTRY if sum(x.id == c.id for x in REGISTRY) > 1})
    raise ValueError(f"duplicate check ids: {_dupes}")


def matches(path: str, globs: tuple[str, ...]) -> bool:
    """Whether a changed path falls under one of a check's trigger globs."""
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in globs)


def tree_guards_for(changed) -> tuple[str, ...]:
    """The whole-tree guards a change makes relevant, in declaration order.

    Empty triggers would mean "nothing selects it", which is why every guard
    has at least one: a guard nobody selects is one that never runs.
    """
    return tuple(
        check.id for check in TREE_GUARDS
        if check.triggers and any(matches(path, check.triggers) for path in changed)
    )
