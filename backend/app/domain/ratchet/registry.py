"""The checks currently collected for the ratchet board."""

from dataclasses import dataclass, field

#: The four areas the page is divided into. A check belongs to exactly one.
SCENES = "场景"
BOUNDARY = "边界"
SIZE = "规模"
TYPES = "类型与样式"

#: How long one check may take. The frontend checks are the slow ones (a few
#: minutes on a cold checkout); a check still running after this did not fail,
#: it never finished, and the snapshot says so.
CHECK_TIMEOUT = 1800


@dataclass(frozen=True)
class Check:
    """One check, its area, and the files whose bytes decide it."""

    id: str
    area: str
    argv: tuple[str, ...]
    cwd: str = "."
    rules: tuple[str, ...] = ()
    #: `ignore_imports` for a file whose baseline lives beside its rules.
    strip: str | None = None
    #: A pytest ledger's table (`_EXEMPT`, `_LEDGER`) lives inside
    #: the module that is also its rule. Naming it here cuts it out of the
    #: fingerprint, because paying an entry off is the debt payment the page
    #: counts — without this, dropping one exemption reads as a rule change.
    baseline: str | None = None
    #: (path under cwd, expression) for a pytest ledger: how much it registers.
    #: Read by importing the module and evaluating the expression in its
    #: namespace — the ledger IS the number, and the test asserts the tree still
    #: matches it. An expression rather than an attribute name because a
    #: ledger's unit is not always its length: a table that maps each file to
    #: HOW MANY entries it holds is counted by summing, and `len()` would report
    #: the files instead.
    probe: tuple[str, str] | None = None
    #: True for a check whose verdict comes from a test run rather than a record.
    ledger: bool = False
    timeout: int = field(default=CHECK_TIMEOUT)


#: Every check the page shows, in the order it shows them. A check missing from
#: this list is a check the page silently stops showing, so this list is the
#: inventory the design's four areas were agreed on.
CHECKS: tuple[Check, ...] = (
    Check(
        id="scene-ratchet",
        area=SCENES,
        argv=("python", ".claude/scripts/scene-ratchet.py", "--json"),
        rules=(
            ".claude/scripts/scene-ratchet.py",
            ".claude/scripts/frontend_grade.py",
            ".claude/scripts/ratchet_report.py",
        ),
    ),
    *(
        Check(
            id=check_id,
            area=SCENES if kind != "network" else BOUNDARY,
            argv=(
                "python",
                ".claude/scripts/scene-debt-ratchet.py",
                "--kind",
                kind,
                "--json",
            ),
            rules=(
                ".claude/scripts/scene-debt-ratchet.py",
                ".claude/scripts/test_scene_debt_ratchet.py",
                ".claude/scripts/scene-ratchet.py",
                ".claude/scripts/frontend_grade.py",
                ".claude/scripts/ratchet_report.py",
                "frontend/scripts/scene-debt-scan.mjs",
                "frontend/package.json",
                "frontend/pnpm-lock.yaml",
            ),
        )
        for kind, check_id in (
            ("children", "scene-non-a-children"),
            ("routes", "scene-use-route"),
            ("network", "legacy-network"),
        )
    ),
    Check(
        id="fe-boundary",
        area=BOUNDARY,
        argv=("node", "scripts/import-boundary-ratchet.mjs", "--json"),
        cwd="frontend",
        rules=(
            "frontend/eslint.boundary.config.mjs",
            # The boundary config builds on the base one; a rule added there
            # decides this check too.
            "frontend/eslint.config.mjs",
            "frontend/scripts/import-boundary-ratchet.mjs",
            "frontend/scripts/import-boundary-ratchet-core.mjs",
            # BETTER, the status mapping and the plain verdict live here.
            "frontend/scripts/ratchet-report.mjs",
            # Reading eslint's JSON output into per-file counts, shared with the
            # storage ratchet — the boundary's counts come from it.
            "frontend/scripts/eslint-ratchet-json.mjs",
        ),
    ),
    Check(
        id="fe-storage",
        area=BOUNDARY,
        argv=("node", "scripts/browser-storage-ratchet.mjs", "--json"),
        cwd="frontend",
        rules=(
            "frontend/eslint.browser-storage.config.mjs",
            # The storage config builds on the base one; a rule added there
            # decides this check too.
            "frontend/eslint.config.mjs",
            "frontend/scripts/browser-storage-ratchet.mjs",
            "frontend/scripts/browser-storage-ratchet-core.mjs",
            # What the safe module does is what the rule means by "safe" — a
            # change to its API is a change to the fix the ratchet asks for.
            "frontend/src/lib/browserStorage.ts",
            "frontend/scripts/eslint-ratchet-json.mjs",
            "frontend/scripts/ratchet-report.mjs",
        ),
    ),
    Check(
        id="be-contracts",
        area=BOUNDARY,
        argv=("python", "scripts/check_boundaries.py", "--json"),
        cwd="backend",
        rules=(
            "backend/.importlinter",
            # The checker itself is where the verdict is decided: which
            # contracts run, and what counts as a violation.
            "backend/scripts/check_boundaries.py",
        ),
        strip="ignore_imports",
    ),
    Check(
        id="be-deferred-imports",
        area=BOUNDARY,
        argv=("python", "scripts/check_deferred_imports.py", "--json"),
        cwd="backend",
        # The counting, the annotation syntax and the verdict all live in the
        # script; the per-file allowances are its baseline, not its rule.
        rules=("backend/scripts/check_deferred_imports.py",),
    ),
    Check(
        id="domain-import-guard",
        area=BOUNDARY,
        argv=("python", "-m", "pytest", "tests/unit/test_domain_import_guard.py", "-q"),
        cwd="backend",
        rules=("backend/tests/unit/test_domain_import_guard.py",),
        probe=("tests/unit/test_domain_import_guard.py", "len(_EXEMPT)"),
        baseline="_EXEMPT",
        ledger=True,
    ),
    Check(
        id="file-sizes",
        area=SIZE,
        argv=(
            "python",
            ".claude/scripts/check-file-sizes.py",
            "--json",
            "--base",
            "origin/main",
        ),
        rules=(
            ".claude/scripts/check-file-sizes.py",
            ".claude/scripts/ratchet_report.py",
        ),
    ),
    Check(
        id="vue-tsc",
        area=TYPES,
        # Through the wrapper, not `tsc-ratchet.mjs` directly: the types the
        # ratchet reads are generated rather than committed, and a bare run on
        # a checkout that has not run the type check yet reports four TS2307
        # cannot-find-module errors for `api/openapi` and calls them type errors.
        #
        # The wrapper spawns both of its halves by name, and the closure follows
        # that (see `_JS_SCRIPT`), so the guard still walks everything this
        # check reads.
        argv=("node", "scripts/typecheck.mjs", "--json"),
        cwd="frontend",
        rules=(
            "frontend/scripts/typecheck.mjs",
            # The generator decides the types being judged, so it is a rule too,
            # and so is the exporter behind it: the document the generator reads
            # is built by `backend/scripts/export_openapi.py` (through `uv run`,
            # which is not a path the closure can follow), and what that file
            # renders is what the types are generated from.
            "frontend/scripts/gen-api-types.mjs",
            "backend/scripts/export_openapi.py",
            "frontend/scripts/tsc-ratchet.mjs",
            "frontend/scripts/tsc-ratchet-core.mjs",
            "frontend/scripts/ratchet-report.mjs",
            "frontend/tsconfig.json",
            "frontend/tsconfig.app.json",
        ),
    ),
    Check(
        id="stylelint-tokens",
        area=TYPES,
        argv=("node", "scripts/stylelint-ratchet.mjs", "--json"),
        cwd="frontend",
        rules=(
            "frontend/stylelint.config.cjs",
            "frontend/scripts/stylelint-ratchet.mjs",
            "frontend/scripts/stylelint-ratchet-core.mjs",
            "frontend/scripts/ratchet-report.mjs",
        ),
    ),
    Check(
        id="fe-contrast",
        area=TYPES,
        # The one check that needs a browser: it walks the three public pages in
        # both themes and judges the colours they actually painted, which no
        # reading of the source can decide. The driver starts the frontend and a
        # headless Chromium itself, so this is still one command whose stdout is
        # one record. It is the same walk the e2e gate runs on a pull request —
        # e2e/contrast-walk.mjs, shared — so the number recorded here and the
        # verdict that blocks a change come out of one piece of code.
        argv=("node", "contrast-ratchet.mjs", "--json"),
        cwd="e2e",
        rules=(
            "e2e/contrast-ratchet.mjs",
            "e2e/contrast-walk.mjs",
            # The rule itself: the floors, the ratio, the verdict and the
            # walker that runs inside the page.
            "frontend/scripts/contrast-ratchet-core.mjs",
            # The record's shape and the cannot-judge path, shared with the
            # other ratchets.
            "frontend/scripts/ratchet-report.mjs",
        ),
    ),
)
