#!/usr/bin/env python3
"""Gate: the module boundaries declared in `backend/.importlinter`.

    cd backend && uv run python scripts/check_boundaries.py
    cd backend && uv run python scripts/check_boundaries.py --base origin/main
    cd backend && uv run python scripts/check_boundaries.py --self-test

    0 = every contract held      1 = a contract broke, or the exceptions are
                                 malformed in a way the format must not allow
    2 = could not judge

WHY A SCRIPT AND NOT JUST `lint-imports`. Two reasons, and only the second one
is about the exit code.

**Exit 2.** `lint-imports` has two statuses, 0 and 1. A run that cannot judge
anything — no config, a config import-linter cannot parse, a contract whose
declaration no longer matches the tree — exits 1, exactly like a real violation,
and a run that judges *nothing* exits 0: a config with the contracts deleted
leaves an empty run, which is a green tick over an empty file. Neither is
acceptable for a gate whose whole job is to go red. This script separates the
three answers and treats "cannot judge" as red, per the convention the hand
written guards in `backend/tests/unit/` already use.

**Where the baseline comes from.** The tree at `git merge-base HEAD --base`
(default `origin/main`), graded by these same contracts. Every violation that
tree already has is one this tree may keep: the baseline is measured, not
stored, so no pull request has to touch a list of exemptions and no writer has
to keep one in step. A violation this tree has and that tree does not is new —
that is the whole verdict. A `--base` that does not resolve is not something to
guess at: it is exit 2.

**The exceptions.** A contract may still carry an `ignore_imports` block, and
here it means one thing: a violation knowingly accepted that the merge-base tree
does not have. It is meant to be short, and each line on it should say why. The
one check that cannot be measured away is a tightened rule: it is graded against
the same base tree with the new rule, so a rule that catches something already
in the tree grandfathers it rather than turning a batch of files red.

import-linter also accepts wildcards in `ignore_imports`, and a wildcard is
exactly how a ratchet turns into a rubber stamp: `app.api.** -> app.domain.**`
would silence every future violation of C2 while looking like one considered
exemption. The format cannot prevent it, so this check refuses one — it is a
violation of the rule, not a problem with the tooling, and it exits 1 rather
than 2.

Everything else is import-linter's own machinery: the contracts live in
`.importlinter`, and `lint-imports` alone, against a checkout of the merge base,
is enough to reproduce a verdict by hand. This script adds the two things above
and nothing more.

    ... --self-test   plant each violation the nine contracts exist to catch,
                      in a throwaway tree, and require them to fire
    ... --json        one JSON record on stdout instead of the report; the exit
                      code is unchanged. The record's shape is fixed in
                      docs/topics/棘轮页方案 section 3.1.
"""

from __future__ import annotations

import argparse
import configparser
import contextlib
import copy
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, NoReturn

HERE = Path(__file__).resolve()
BACKEND_ROOT = HERE.parents[1]
DEFAULT_CONFIG = BACKEND_ROOT / ".importlinter"

#: The revision the baseline is read from — the tree HEAD and this revision last
#: agreed on, not this revision itself. CI and the commit hooks pass it
#: explicitly; a branch that cannot reach it is not judged.
DEFAULT_BASE = "origin/main"

#: The root package the contracts are written against. The check runs from the
#: directory that contains it, because that is how grimp finds it.
ROOT_PACKAGE = "app"

#: The contracts that declare a layer table.
_LAYER_CONTRACTS = ("domain-layers", "turn-layers")


def declared_layers(config_text: str) -> dict[str, list[str]]:
    """The layers each layer contract declares, one entry per layer, top first.

    The self-test plants trees that hold none of the repository's turn or
    domain packages, so it rewrites these two tables to name the packages its
    seed does hold. Carrying the list across from the declaration instead of
    re-typing it is what keeps that copy from going stale: a layer this helper
    missed would come back as an undeclared module, which reads like a broken
    rule rather than a stale fixture.
    """
    parser = configparser.ConfigParser()
    parser.read_string(config_text)
    found: dict[str, list[str]] = {}
    for contract_id in _LAYER_CONTRACTS:
        section = f"importlinter:contract:{contract_id}"
        raw = parser.get(section, "layers", fallback=None)
        if raw is None:
            raise RuntimeError(
                f"{section} declares no `layers`; the self-test cannot carry a "
                "layer table the declaration does not have"
            )
        # One layer per line: import-linter takes `layers` as a list, which its
        # reader builds from the continuation lines of the option.
        found[contract_id] = [line.strip() for line in raw.splitlines() if line.strip()]
    return found


#: The contract ids this check claims to be running. Kept here, next to the
#: check, on purpose: a declaration that quietly lost a contract would otherwise
#: run fewer contracts and still report success — a check that narrowed itself
#: is indistinguishable from a tree that got cleaner. Adding or renaming a
#: contract means editing this tuple, which is the tripwire working.
CONTRACT_IDS = (
    "api-domain-core",
    "routes-touch-no-models",
    "domains-acyclic",
    "turn-layers",
    "turn-no-composition-imports",
    "realtime-no-turn",
    "turn-storage-purity",
    "routes-independent",
    "domain-layers",
)

OK, BROKEN, CANNOT_JUDGE = 0, 1, 2

#: The check id in the ratchet snapshot, and the longest a `reason` may be.
CHECK_ID = "be-contracts"
REASON_LIMIT = 2000

#: import-linter's wording for an `ignore_imports` entry that matched nothing
#: (`importlinter.application.contract_utils`). Read back here to tell a written
#: exception that stopped matching from a measured one that stopped being
#: needed — the first is a line to delete, the second is debt paid down.
UNMATCHED_PREFIX = "No matches for ignored import "


def _unmatched(warnings, written: set[str]) -> list[str]:
    """The written exceptions among `warnings` that no longer match an import."""
    stale = []
    for warning in warnings:
        text = str(warning)
        if not text.startswith(UNMATCHED_PREFIX) or not text.endswith("."):
            continue
        entry = text[len(UNMATCHED_PREFIX) : -1]
        if entry in written:
            stale.append(entry)
    return stale


# --- the JSON record ---------------------------------------------------------
# Fixed in docs/topics/棘轮页方案 section 3.1, and written by hand here rather
# than imported from .claude/scripts/ratchet_report.py: this script is run from
# backend/ and reaching across directories for fifteen lines would tie two CI
# working directories together. The frontend ratchets share that helper.
def _as_json() -> bool:
    return "--json" in sys.argv


def _emit(record: dict[str, Any]) -> None:
    """One JSON line on stdout, and only in --json mode.

    Raising otherwise is the point: a checker whose human output quietly grew a
    JSON line would change every CI log that reads it.
    """
    if not _as_json():
        raise RuntimeError("check_boundaries: _emit() is only for --json runs")
    print(json.dumps(record, ensure_ascii=False))


def _cannot_judge(reason: str) -> NoReturn:
    """Say why on stdout as the record, when a record was asked for, and exit 2."""
    if _as_json():
        _emit(
            {
                "id": CHECK_ID,
                "better": "down",
                "status": "cannot_judge",
                "reason": reason.strip()[:REASON_LIMIT],
            }
        )
    sys.exit(2)


def _record(
    code: int, rows: list[dict[str, Any]] | None, message: str
) -> dict[str, Any]:
    """The record for a run that reached a verdict.

    `actual` is how many module pairs this tree still has to tolerate, `frozen`
    how many were supplied to it: those measured from the tree at the merge base
    plus the few written in the declaration. Both are measured on every run —
    the board used to take them from the baseline's line count, which is why an
    exemption that stopped matching anything could sit there unnoticed until the
    day it was counted. Without rows (a wildcard in the declaration, refused
    before the contracts run) neither number was measured, so both are null
    rather than 0.
    """
    record: dict[str, Any] = {
        "id": CHECK_ID,
        "better": "down",
        "status": "pass" if code == OK else "fail",
    }
    if rows is None:
        record |= {
            "actual": None,
            "frozen": None,
            "stale": [],
            "details": [],
            "reason": message.strip().splitlines()[0][:REASON_LIMIT] if message else "",
        }
        return record
    record |= {
        "actual": sum(row["actual"] for row in rows),
        "frozen": sum(row["frozen"] for row in rows),
        "stale": [
            {"file": row["file"], "frozen": 1, "actual": 0, "why": warning}
            for row in rows
            for warning in row["unused"]
        ],
        # One row per contract. `file` carries the contract id: this check's
        # unit is a contract, not a path, and the id is what the config and the
        # page both name it by.
        "details": [
            {
                key: row[key]
                for key in ("file", "name", "kept", "actual", "frozen", "stale")
            }
            for row in rows
        ],
    }
    if message:
        record["reason"] = message.strip().splitlines()[0][:REASON_LIMIT]
    return record


def _contract_rows(
    report, options, exceptions: dict[str, list[str]], derived: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """What each contract was allowed, what it still needs, and what is unused.

    `actual` is how many of the supplied pairs the tree still needs and `frozen`
    how many were supplied — the merge-base violations plus the written
    exceptions. A warning is a *written* exception that no longer matches any
    import: the measured half is re-read every run, so a violation paid down
    simply stops being in it, and only a line somebody typed needs saying.
    """
    ids = {option.get("name"): option.get("id") for option in options.contracts_options}
    rows: list[dict[str, Any]] = []
    for contract, check in report.get_contracts_and_checks():
        cid = ids.get(contract.name) or "?"
        unused = _unmatched(check.warnings, set(exceptions.get(cid, ())))
        rows.append(
            {
                "file": cid,
                "name": contract.name,
                "kept": bool(check.kept),
                "actual": int(check.ignored_import_count),
                "frozen": len(getattr(contract, "ignore_imports", None) or []),
                "stale": len(unused),
                "unused": unused,
            }
        )
    return rows


def _module_file(module: str) -> Path | None:
    """Find the source file for an import-linter module name."""
    stem = Path.cwd() / module.replace(".", "/")
    for candidate in (stem.with_suffix(".py"), stem / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _source_location(importer: str, imported: str, line_numbers=()) -> str:
    """Turn import-linter's module/line pair into a clickable repo location."""
    path = _module_file(importer)
    lines = [int(line) for line in line_numbers if isinstance(line, int)]
    line = str(lines[0]) if lines else "?"
    if path is None:
        shown = importer.replace(".", "/") + ".py"
    else:
        relative = path.relative_to(Path.cwd()).as_posix()
        shown = f"backend/{relative}" if Path.cwd().name == "backend" else relative
    return f"{shown}:{line} ({importer} -> {imported})"


def _failure_locations(report) -> list[str]:
    """Extract file:line locations from import-linter's failed metadata."""
    locations: list[str] = []
    seen: set[str] = set()

    def add(importer: str, imported: str, line_numbers=()) -> None:
        location = _source_location(importer, imported, line_numbers)
        if location not in seen:
            seen.add(location)
            locations.append(location)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("line_numbers") and isinstance(value.get("importer"), str):
                add(value["importer"], value["imported"], value["line_numbers"])
            for child in value.values():
                walk(child)
        elif isinstance(value, (list, tuple, set)):
            # C3 exposes cycle-breaker pairs as a set of (importer, imported).
            if (
                isinstance(value, tuple)
                and len(value) == 2
                and all(isinstance(item, str) for item in value)
            ):
                for detail in report.graph.get_import_details(
                    importer=value[0], imported=value[1]
                ):
                    add(value[0], value[1], (detail["line_number"],))
            else:
                for child in value:
                    walk(child)

    for _contract, check in report.get_contracts_and_checks():
        if not check.kept:
            metadata = getattr(check, "metadata", {})
            walk(metadata)
            for module in sorted(metadata.get("undeclared_modules", ())):
                add(module, "undeclared layer", (1,))
    return locations


_BOUNDARY_FIXES = {
    "C1": (
        "C1 layers: api -> domain -> core, no imports upward. Move the dependency "
        "down into the owning domain/core module, or call the lower layer through "
        "the declared service seam; do not add a reverse import."
    ),
    "C2": (
        "C2 routes do not import a domain's models directly. Replace the model import "
        "with that domain's service call; the service owns model access and writes."
    ),
    "C3": (
        "C3 sibling domains are acyclic. Remove one edge from the reported cycle, "
        "or expose a declared seam instead of importing the sibling directly."
    ),
    "T1": (
        "T1 turn layers are intake -> steps -> store -> state. Put the module in its "
        "declared layer and import only downward."
    ),
    "T2": (
        "T2 turn code must not import chat/runtime/API directly. Pass composed facts "
        "through the service seam."
    ),
    "T3": (
        "T3 realtime must not reach turn execution or composition. Keep broker "
        "plumbing separate from turn orchestration."
    ),
    "T4": (
        "T4 turn storage/state must not reach upper layers or realtime. Move "
        "orchestration up and keep storage focused on persistence."
    ),
    "C4": (
        "C4 route modules are independent of one another. Move what both routes "
        "need into a neutral module (app/api/request_deps.py for request "
        "dependencies, a domain service for behaviour) and import it from both."
    ),
    "C5": (
        "C5 domain packages form seven layers, dependencies point down. Move the "
        "dependency into the lower layer (or call it through the declared seam); "
        "an unplaced child of app.domain goes into the layer table in "
        "`.importlinter` first — this contract is exhaustive, so every child of "
        "app.domain, package or top-level module, must be named in it."
    ),
}


def _boundary_failure_message(
    config_path: Path, report, base: str, rev: str, held: int, written: int
) -> str:
    failed = []
    for contract, check in report.get_contracts_and_checks():
        if not check.kept:
            failed.append(
                _BOUNDARY_FIXES.get(contract.name.split(" ", 1)[0], contract.name)
            )
    locations = _failure_locations(report)
    location_text = "\n".join(f"  {location}" for location in locations)
    baseline = (
        f"the tree at {rev[:12]}, the last one HEAD and {base} agreed on, is the "
        f"baseline: it already breaks {held} module pairs, and this run holds "
        "every one of them open"
    )
    if written:
        baseline += f" plus the {written} exception(s) written in {config_path.name}"
    return (
        f"a boundary broke. {baseline}.\n"
        + "\n".join(f"{fix}" for fix in failed)
        + (
            f"\nViolations (fix the import at this file:line):\n{location_text}"
            if location_text
            else ""
        )
        + "\nNot one of the pairs above is in that tree: they are new.\n"
        "A pair this tree is allowed to keep on purpose — a judgement, not a "
        f"measurement — goes in {config_path.name} under its contract, with a "
        "comment saying why.\n"
        "Run `cd backend && uv run python scripts/check_boundaries.py` to recheck. "
        "Rule: `.claude/rules/architecture.md`, `Backend: the import graph is "
        "checked, not assumed`."
    )


def _declared_exceptions(options) -> dict[str, list[str]]:
    """The `ignore_imports` this tree writes down, by contract id.

    An entry here is a decision: a violation this tree knows about, accepts, and
    says why about. Everything else the check holds open is measured from the
    merge-base tree and never written down, so this list is meant to stay short
    enough to read.
    """
    written: dict[str, list[str]] = {}
    for contract in options.contracts_options:
        raw = contract.get("ignore_imports")
        if raw is None:
            continue
        lines = raw if isinstance(raw, list) else [raw]
        entries = [str(line).strip() for line in lines if str(line).strip()]
        if entries:
            written[contract.get("id", "?")] = entries
    return written


# ---------------------------------------------------------------------------
# The baseline. The tree at the merge base, graded by these same contracts:
# one ruler, two trees. Nothing about it is stored, so no pull request has to
# carry a list of exemptions and no writer has to keep one in step.
# ---------------------------------------------------------------------------


class Unjudgeable(Exception):
    """The tree a baseline would be read from could not be graded — exit 2."""


def _importlinter():
    """import-linter's entry points, imported late.

    Importing `api` is what registers its built-in contract types and its
    settings; without it `create_report` raises. Late, so that a missing install
    is a verdict (exit 2) and not a traceback out of argparse.
    """
    from importlinter.api import read_configuration
    from importlinter.application import rendering
    from importlinter.application.use_cases import (
        _register_contract_types,
        create_report,
    )
    from importlinter.application.user_options import UserOptions

    return (
        read_configuration,
        rendering,
        _register_contract_types,
        create_report,
        UserOptions,
    )


def _load_declarations(config_path: Path) -> tuple[int, str, Any]:
    """(exit code, message, options) for the declarations in `config_path`.

    The two shapes this format must not accept are refused here, before any
    tree is graded: a declaration that lost one of the nine contracts, and an
    exception written as a wildcard.
    """
    try:
        read_configuration, _, _, _, UserOptions = _importlinter()
    except Exception as exc:  # pragma: no cover - depends on the installed version
        return (
            CANNOT_JUDGE,
            f"import-linter is not usable here: {exc!r}. A system python3 "
            "does not have the project's dependencies; run `cd backend && uv run "
            "python scripts/check_boundaries.py`",
            None,
        )

    try:
        parsed = read_configuration(str(config_path))
    except FileNotFoundError:
        return (
            CANNOT_JUDGE,
            f"no {config_path.name} to read — nothing declared to check",
            None,
        )
    except Exception as exc:
        return CANNOT_JUDGE, f"{config_path.name} could not be parsed: {exc!r}", None

    options = UserOptions(parsed["session_options"], parsed["contracts_options"])

    ids = [contract.get("id") for contract in options.contracts_options]
    missing = [contract_id for contract_id in CONTRACT_IDS if contract_id not in ids]
    if missing:
        return (
            CANNOT_JUDGE,
            f"{config_path.name} declares {ids}, which is missing {missing}. "
            "The check is designed to run all of "
            f"{list(CONTRACT_IDS)}; a declaration that lost one is not judged.",
            None,
        )

    written = _declared_exceptions(options)
    wildcards = [
        (contract_id, entry)
        for contract_id, entries in written.items()
        for entry in entries
        if "*" in entry
    ]
    if wildcards:
        source_lines = config_path.read_text(encoding="utf-8").splitlines()
        line_of = {line.strip(): i for i, line in enumerate(source_lines, 1)}
        listing = "\n".join(
            f"  {config_path}:{line_of.get(entry, '?')} [{contract_id}] {entry}"
            for contract_id, entry in wildcards
        )
        return (
            BROKEN,
            (
                "an exception is written as a wildcard, which this format does "
                f"not allow:\n{listing}\n\n"
                "Name the module and its target exactly. A wildcard exemption\n"
                "silences every future violation of that contract while looking\n"
                "like one considered exception — the ratchet has to be able to\n"
                "say which pair it is holding open.\n"
                "Write the accepted importer -> imported pair out in "
                f"{config_path.name}; do not widen ignore_imports. Recheck with "
                "`cd backend && uv run python scripts/check_boundaries.py`. "
                "Rule: .claude/rules/architecture.md, "
                "Backend: the import graph is checked, not assumed."
            ),
            None,
        )

    return OK, "", options


def _run_contracts(options, render: bool):
    """Run `options` against the tree at the working directory.

    `lint-imports` finds the root package on sys.path, and its CLI puts the
    working directory there first. Do the same, so this check and the tool it
    wraps judge the same tree from the same place.
    """
    _, rendering, register_contract_types, create_report, _ = _importlinter()
    sys.path.insert(0, str(Path.cwd()))
    register_contract_types(options)
    report = create_report(options, cache_dir=None)
    if render:
        rendering.render_report(report)
    return report


def _merge_base(root: Path, base: str) -> str:
    """The commit HEAD and `base` last agreed on: where the baseline comes from.

    Not `base` itself — a branch may be behind it, and what this tree is judged
    against is the tree it actually started from. A ref that does not resolve is
    not a guess to make: it is exit 2.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "merge-base", "HEAD", base],
        capture_output=True,
        text=True,
    )
    revs = result.stdout.split()
    if result.returncode or not revs:
        why = (result.stderr or result.stdout).strip().splitlines()
        raise Unjudgeable(
            f"no merge base between HEAD and {base}"
            + (f": {why[-1]}" if why else "")
            + " — the baseline is the tree they last agreed on (a fetch may be "
            "missing)"
        )
    # Several best common ancestors (a criss-cross history) is not a tree to
    # guess at: any one of them is a real answer, so take the first.
    return revs[0]


def _repo_root(work: Path) -> Path:
    """The checkout `work` sits in, for the git commands that read its history."""
    result = subprocess.run(
        ["git", "-C", str(work), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode or not result.stdout.strip():
        why = (result.stderr or result.stdout).strip().splitlines()
        raise Unjudgeable(
            f"{work} is not inside a git checkout" + (f": {why[-1]}" if why else "")
        )
    return Path(result.stdout.strip())


def _base_tree(work: Path, rev: str, dest: Path) -> Path:
    """`rev`'s root package unpacked under `dest`; returns where to grade it.

    `git archive` of one directory rather than a worktree add: several checkouts
    of this clone run the check at once, and a worktree writes an entry into the
    shared `.git` that nothing prunes if the run dies before it can remove it.
    """
    root = _repo_root(work)
    prefix = work.resolve().relative_to(root)
    package = f"{prefix}/{ROOT_PACKAGE}" if str(prefix) != "." else ROOT_PACKAGE
    archive = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", rev, package],
        capture_output=True,
    )
    if archive.returncode:
        why = archive.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Unjudgeable(
            f"cannot read {package} at {rev}" + (f": {why[-1]}" if why else "")
        )
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            # `filter="data"` refuses absolute paths and `..` out of the
            # archive, which git does not write and a corrupted object could.
            tar.extractall(dest, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise Unjudgeable(f"cannot unpack the tree at {rev}: {exc}") from exc
    where = dest / package.rsplit("/", 1)[0] if "/" in package else dest
    if not (where / ROOT_PACKAGE / "__init__.py").is_file():
        raise Unjudgeable(f"the tree at {rev} has no {package}/ package to grade")
    return where


def _without_exceptions(text: str) -> str:
    """`text` with every `ignore_imports` option removed, and nothing else.

    The baseline is measured, so the tree it is measured from is graded by the
    declarations alone. The exceptions are decisions this tree made about
    itself; what is wanted from the other tree is what it breaks on its own.
    """
    kept: list[str] = []
    dropping = False
    for line in text.splitlines():
        if re.match(r"^[ \t]*ignore_imports[ \t]*=", line):
            dropping = True
            continue
        if dropping and (not line.strip() or line[:1] in " \t"):
            continue
        dropping = False
        kept.append(line)
    return "\n".join(kept).rstrip("\n") + "\n"


def _overlay_new_modules(where: Path, work: Path) -> int:
    """Give `where` an empty placeholder for every module `work` added.

    A declaration may name a module this branch adds — a new domain in the
    package table, a new route-shared module among C2's sources — and the tree
    from before the branch has no such module. import-linter refuses to grade
    that tree at all (`Missing layer ... does not exist`, `Module ... does not
    exist`), which would leave the branch with a check it cannot satisfy. An
    empty file answers the refusal and nothing else: it holds no imports, so it
    can neither create a violation nor hide one, and every rule is still read
    against the tree that has no history of these modules.

    Returns how many placeholders were added, for the self-test to count.
    """
    added = 0
    for path in sorted((work / ROOT_PACKAGE).rglob("*.py")):
        target = where / path.relative_to(work)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
        added += 1
    return added


def _derive_base(config_path: Path, rev: str) -> dict[str, list[str]]:
    """The module pairs the tree at `rev` breaks, per contract id.

    Graded in a process of its own, from a directory holding that tree:
    import-linter finds the root package on sys.path and in the working
    directory, and running it twice in this process would be two answers to
    which `app` is on disk.
    """
    work = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="boundary-base-") as scratch:
        where = _base_tree(work, rev, Path(scratch))
        declaration = where / config_path.name
        declaration.write_text(
            _without_exceptions(config_path.read_text(encoding="utf-8"))
        )
        _overlay_new_modules(where, work)
        result = subprocess.run(
            [sys.executable, str(HERE), "--config", str(declaration), "--measure"],
            cwd=where,
            capture_output=True,
            text=True,
        )
    if result.returncode != OK:
        why = (result.stderr.strip().splitlines() or ["no output"])[-1]
        raise Unjudgeable(f"cannot grade the tree at {rev}: {why}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise Unjudgeable(
            f"grading the tree at {rev} printed {len(lines)} lines, not its one"
        )
    try:
        derived = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise Unjudgeable(
            f"grading the tree at {rev} did not print a JSON object: {exc}"
        ) from exc
    if not isinstance(derived, dict) or set(derived) != set(CONTRACT_IDS):
        raise Unjudgeable(
            f"the tree at {rev} was graded for {sorted(derived)}, not "
            f"{list(CONTRACT_IDS)}"
        )
    for contract_id, entries in derived.items():
        for entry in entries:
            if not isinstance(entry, str) or "*" in entry:
                raise Unjudgeable(
                    f"grading the tree at {rev} offered {entry!r} for "
                    f"{contract_id}; a baseline is module pairs, never a pattern"
                )
    return derived


def entries_from_metadata(metadata: dict) -> list[str]:
    """The `ignore_imports` expression behind each violation in one contract.

    One line per *direct* import, because that is what `ignore_imports` matches:
    a layer contract reports chains of imports, and the pair that is held open
    is a link of one. Every link of every chain is named.
    """
    edges: set[tuple[str, str]] = set()

    # layers: invalid_dependencies -> routes -> chain -> links
    for dependency in metadata.get("invalid_dependencies", []):
        for route in dependency["routes"]:
            for link in route["chain"]:
                edges.add((link["importer"], link["imported"]))

    # forbidden: invalid_chains -> chains -> links
    # independence: invalid_chains -> chains -> {chain, extra_firsts, extra_lasts}
    for invalid in metadata.get("invalid_chains", []):
        for chain in invalid["chains"]:
            if isinstance(chain, dict):
                links = [*chain["chain"], *chain["extra_firsts"], *chain["extra_lasts"]]
            else:
                links = chain
            for link in links:
                edges.add((link["importer"], link["imported"]))

    # acyclic_siblings: {package: {(importer, imported)}}
    for breakers in metadata.get("cycle_breakers_by_package", {}).values():
        edges.update(breakers)

    return sorted(f"{importer} -> {imported}" for importer, imported in edges)


def _derived_by_contract(report, options) -> dict[str, list[str]]:
    """What a graded tree breaks, per contract id, as `ignore_imports` lines."""
    ids = {option.get("name"): option.get("id") for option in options.contracts_options}
    derived: dict[str, list[str]] = {}
    for contract, check in report.get_contracts_and_checks():
        contract_id = ids.get(contract.name)
        if contract_id is None:
            raise Unjudgeable(
                f"{contract.name!r} was graded but is not one of the declared "
                f"contracts ({sorted(ids)})"
            )
        derived[contract_id] = entries_from_metadata(check.metadata)
    missing = [
        contract_id for contract_id in CONTRACT_IDS if contract_id not in derived
    ]
    if missing:
        raise Unjudgeable(f"grading this tree reported nothing for {missing}")
    return derived


def _with_baseline(options, derived, written):
    """`options` with the exceptions it needs: measured plus written, in one list.

    import-linter has one way to hold a pair open, so the two kinds are
    indistinguishable to it — the difference is where they come from, and this
    check is the only place that has to know.
    """
    contracts = copy.deepcopy(options.contracts_options)
    for contract in contracts:
        contract_id = contract.get("id")
        allowed = sorted(
            set(derived.get(contract_id, ())) | set(written.get(contract_id, ()))
        )
        if allowed:
            contract["ignore_imports"] = allowed
        else:
            contract.pop("ignore_imports", None)
    return type(options)(options.session_options, contracts)


def judge(config_path: Path, base: str) -> tuple[int, str, list[dict[str, Any]] | None]:
    """Grade the tree at the cwd against the tree at the merge base with `base`.

    The third element is the per-contract breakdown when the contracts actually
    ran, and None when the run stopped before it could count anything.
    """
    code, message, options = _load_declarations(config_path)
    if code != OK:
        return code, message, None
    written = _declared_exceptions(options)

    try:
        rev = _merge_base(Path.cwd(), base)
        derived = _derive_base(config_path, rev)
    except Unjudgeable as exc:
        return (
            CANNOT_JUDGE,
            f"{exc}. Nothing was judged: what this tree may keep is read from the "
            f"tree HEAD and {base} last agreed on, and without that tree there is "
            "nothing to hold anything to.",
            None,
        )

    try:
        report = _run_contracts(_with_baseline(options, derived, written), render=True)
    except Exception as exc:
        return CANNOT_JUDGE, f"the contracts could not be run at all: {exc!r}", None

    rows = _contract_rows(report, options, written, derived)
    if report.could_not_run:
        return CANNOT_JUDGE, "import-linter could not run one of the contracts", None
    if report.contains_failures:
        held = sum(len(entries) for entries in derived.values())
        exceptions = sum(len(entries) for entries in written.values())
        return (
            BROKEN,
            _boundary_failure_message(config_path, report, base, rev, held, exceptions),
            rows,
        )
    return OK, "", rows


def measure(config_path: Path) -> int:
    """Grade the tree at the cwd by the declarations alone, and print what they cost.

    One line of JSON, `{contract id: [importer -> imported, ...]}`: the baseline
    a tree implies. This is how the check itself grades the merge-base tree —
    not the ratchet's interface, but the same run underneath it, and readable
    by hand when a verdict needs explaining.
    """
    code, message, options = _load_declarations(config_path)
    if code != OK:
        print(f"cannot grade: {message}", file=sys.stderr)
        return code

    try:
        report = _run_contracts(options, render=False)
    except Exception as exc:
        print(
            f"cannot grade: the contracts could not be run at all: {exc!r}",
            file=sys.stderr,
        )
        return CANNOT_JUDGE
    if report.could_not_run:
        print(
            "cannot grade: import-linter could not run one of the contracts",
            file=sys.stderr,
        )
        return CANNOT_JUDGE

    try:
        derived = _derived_by_contract(report, options)
    except Unjudgeable as exc:
        print(f"cannot grade: {exc}", file=sys.stderr)
        return CANNOT_JUDGE
    print(json.dumps(derived, ensure_ascii=False))
    return OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None, help="the .importlinter to read")
    parser.add_argument(
        "--base",
        default=DEFAULT_BASE,
        help=(
            "the revision the baseline is read from: the tree HEAD and this "
            f"revision last agreed on (default {DEFAULT_BASE})"
        ),
    )
    parser.add_argument(
        "--measure",
        action="store_true",
        help=(
            "print the baseline the tree at the cwd implies, one JSON object, "
            "instead of judging it"
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="plant each violation in a throwaway tree and require the check to fire",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON record on stdout instead of the report",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    config_path = Path(args.config) if args.config else DEFAULT_CONFIG

    if not (Path.cwd() / ROOT_PACKAGE / "__init__.py").is_file():
        reason = (
            f"no {ROOT_PACKAGE}/ package in {Path.cwd()} — run this from the "
            "directory that holds it (backend/)"
        )
        print(f"cannot judge: {reason}", file=sys.stderr)
        _cannot_judge(reason)

    if args.measure:
        return measure(config_path)

    # Under --json stdout carries one record and nothing else, so the whole
    # judgement runs with stdout pointed at stderr: import-linter and grimp
    # print as they work, and none of it may land around the record.
    if _as_json():
        with contextlib.redirect_stdout(sys.stderr):
            code, message, rows = judge(config_path, args.base)
    else:
        code, message, rows = judge(config_path, args.base)
    if code == CANNOT_JUDGE:
        print(f"cannot judge: {message}", file=sys.stderr)
        _cannot_judge(message)
    if code == BROKEN:
        print(f"broken: {message}", file=sys.stderr)
    if _as_json():
        _emit(_record(code, rows, message))
    elif code == OK:
        print(f"PASS: module boundaries held ({config_path.name})")
    return code


# ---------------------------------------------------------------------------
# Self test. Every contract above was red on the day it was written, so the
# tree passing it proves nothing about whether it still fires — the baseline
# would make a contract that stopped matching anything look identical to a
# clean repo. These plant the violations the contracts exist to catch,
# in the smallest tree that satisfies their declarations, and require each one
# to come back red for the stated reason.
# ---------------------------------------------------------------------------

_SEED = {
    "app/__init__.py": "",
    "app/api/__init__.py": "",
    "app/api/deps.py": "def get_db():\n    ...\n",
    # C2 names the route-shared dependency modules as sources; the seed is the
    # smallest tree every declaration holds on, so they have to exist here too.
    "app/api/admin_deps.py": "",
    "app/api/users_deps.py": "",
    "app/api/space_deps.py": "",
    "app/api/space_payloads.py": "",
    "app/api/task_deps.py": "",
    "app/api/topics_deps.py": "",
    "app/api/awaiting_deps.py": "",
    "app/api/legal_deps.py": "",
    "app/api/teams_deps.py": "",
    "app/api/topics_file_sources.py": "",
    "app/api/ws_pipe.py": "",
    "app/api/preview_relay.py": "",
    "app/api/project_skill_rows.py": "",
    "app/api/review_comment_rows.py": "",
    "app/api/topic_member_rows.py": "",
    "app/api/topic_pin_rows.py": "",
    "app/api/routes/__init__.py": "",
    "app/api/routes/demo.py": "from app.domain.bar.services import read_bar\n",
    "app/api/routes/other.py": "",
    "app/api/shared.py": "",
    "app/core/__init__.py": "",
    "app/domain/__init__.py": "",
    "app/domain/bar/__init__.py": "",
    "app/domain/bar/models.py": "class Bar:\n    ...\n",
    "app/domain/bar/services.py": "def read_bar():\n    ...\n",
    "app/domain/alpha/__init__.py": "",
    "app/domain/agent/__init__.py": "",
    "app/domain/agent/chat.py": "",
    "app/domain/agent/runtime.py": "",
    "app/domain/agent/fixture_bridge.py": "",
    "app/domain/agent/run_records.py": "",
    "app/domain/agent/realtime/__init__.py": "",
    "app/domain/agent/realtime/broker.py": "",
    "app/domain/agent/turn/__init__.py": "",
    "app/domain/agent/turn/intake/__init__.py": "",
    "app/domain/agent/turn/steps/__init__.py": "",
    "app/domain/agent/turn/store/__init__.py": "",
    "app/domain/agent/turn/state/__init__.py": "",
}

#: (what it plants, files beyond the seed, expected exit code, a needle the
#: output must carry — so a red run is red for the reason claimed and not
#: because the tree failed to build)
_IMPORT_INTAKE = "import app.domain.agent.turn.intake\n"
_IMPORT_STEPS = "import app.domain.agent.turn.steps\n"
_IMPORT_STORE = "import app.domain.agent.turn.store\n"
_IMPORT_STATE = "import app.domain.agent.turn.state\n"
_IMPORT_BRIDGE = "import app.domain.agent.fixture_bridge\n"
_IMPORT_BROKER = "import app.domain.agent.realtime.broker\n"
_IMPORT_RECORDS = "import app.domain.agent.run_records\n"
_IMPORT_AGENT = "import app.domain.agent\n"


_CASES: list[tuple[str, dict[str, str], int, str | None, tuple[str, ...]]] = [
    (
        "a route reaching a model directly (C2)",
        {
            "app/api/routes/demo.py": (
                "from app.domain.bar.models import Bar\n"
                "from app.domain.bar.services import read_bar\n"
            )
        },
        BROKEN,
        "app.api.routes is not allowed to import app.domain.bar.models",
        ("routes-touch-no-models",),
    ),
    (
        "core reaching up into api (C1)",
        {"app/core/jobs.py": "from app.api.deps import get_db\n"},
        BROKEN,
        "app.core is not allowed to import app.api",
        ("api-domain-core",),
    ),
    (
        "two sibling domains in a cycle (C3)",
        {
            "app/domain/alpha/__init__.py": "",
            "app/domain/alpha/services.py": (
                "from app.domain.bar.services import read_bar\n"
            ),
            "app/domain/bar/services.py": (
                "from app.domain.alpha.services import alpha_read\n"
            ),
        },
        BROKEN,
        "No cycles are allowed in app.domain",
        ("domains-acyclic",),
    ),
    (
        "a tree inside every declared boundary",
        {},
        OK,
        None,
        (),
    ),
    (
        "steps importing intake directly (T1)",
        {"app/domain/agent/turn/steps/execute.py": _IMPORT_INTAKE},
        BROKEN,
        None,
        ("turn-layers",),
    ),
    (
        "steps reaching intake through a bridge (T1 transitive)",
        {
            "app/domain/agent/turn/steps/execute.py": _IMPORT_BRIDGE,
            # Crossing a turn sibling and coming back also creates a C3 cycle.
            "app/domain/agent/fixture_bridge.py": _IMPORT_INTAKE,
        },
        BROKEN,
        None,
        ("turn-layers", "domains-acyclic"),
    ),
    (
        "an unclassified turn module (T1 exhaustive)",
        {"app/domain/agent/turn/extra.py": ""},
        BROKEN,
        None,
        ("turn-layers",),
    ),
    (
        "intake importing chat directly (T2)",
        {"app/domain/agent/turn/intake/messages.py": "import app.domain.agent.chat\n"},
        BROKEN,
        None,
        ("turn-no-composition-imports",),
    ),
    (
        "intake reaching chat indirectly is deliberately allowed (T2)",
        {
            "app/domain/agent/turn/intake/messages.py": _IMPORT_BRIDGE,
            "app/domain/agent/fixture_bridge.py": "import app.domain.agent.chat\n",
        },
        OK,
        None,
        (),
    ),
    (
        "realtime reaching intake through a bridge (T3 transitive)",
        {
            "app/domain/agent/realtime/broker.py": _IMPORT_BRIDGE,
            "app/domain/agent/fixture_bridge.py": _IMPORT_INTAKE,
        },
        BROKEN,
        None,
        ("realtime-no-turn",),
    ),
    (
        "storage reaching realtime through run records (T4 transitive)",
        {
            "app/domain/agent/turn/store/events.py": _IMPORT_RECORDS,
            "app/domain/agent/run_records.py": _IMPORT_BROKER,
        },
        BROKEN,
        None,
        ("turn-storage-purity",),
    ),
    (
        "a package reaching a higher layer (C5)",
        {"app/domain/bar/services.py": _IMPORT_AGENT},
        BROKEN,
        "app.domain.bar is not allowed to import app.domain.agent",
        ("domain-layers",),
    ),
    (
        "an unclassified domain package (C5 exhaustive)",
        {"app/domain/unplaced/__init__.py": ""},
        BROKEN,
        "app.domain.unplaced",
        ("domain-layers",),
    ),
    (
        "one route importing another (C4)",
        {"app/api/routes/demo.py": "import app.api.routes.other\n"},
        BROKEN,
        None,
        ("routes-independent",),
    ),
    (
        "two routes meeting through a shared helper (C4 transitive)",
        {
            "app/api/routes/demo.py": "import app.api.shared\n",
            "app/api/shared.py": "import app.api.routes.other\n",
        },
        BROKEN,
        None,
        ("routes-independent",),
    ),
    (
        "routes sharing a neutral module that imports no route remain allowed (C4)",
        {
            "app/api/routes/demo.py": "import app.api.shared\n",
            "app/api/routes/other.py": "import app.api.shared\n",
        },
        OK,
        None,
        (),
    ),
    (
        "real downward layers and a steps publisher remain allowed",
        {
            "app/domain/agent/turn/intake/messages.py": _IMPORT_STEPS,
            "app/domain/agent/turn/steps/__init__.py": _IMPORT_STORE + _IMPORT_BROKER,
            "app/domain/agent/turn/store/__init__.py": _IMPORT_STATE,
        },
        OK,
        None,
        (),
    ),
    (
        "a shared route dependency module reaching a model directly (C2)",
        {"app/api/space_payloads.py": "from app.domain.bar.models import Bar\n"},
        BROKEN,
        "app.api.space_payloads is not allowed to import app.domain.bar.models",
        ("routes-touch-no-models",),
    ),
]

#: A declaration that names no contract at all: valid INI, valid session
#: options, no contracts — the shape a silent narrowing takes.
_THIN_CONFIG = "[importlinter]\nroot_package = app\n"


def _invoke(tmp: Path, *extra: str) -> tuple[int, str, str]:
    """Run the real check, as a process, from the tree it is judging — the same
    way CI and a commit run it. In-process would have to juggle sys.path entries
    for a root package named `app`, which is exactly the kind of cleverness that
    makes a check pass for a reason nobody can name.

    stdout and stderr are kept apart on purpose: which stream a thing lands on
    is part of what --json promises, and a merged stream cannot check it.

    `--base HEAD` is the fixture's baseline: every case plants its violation
    *after* committing the tree the check should treat as the base, so HEAD is
    exactly the tree this branch starts from. A case that wants another base
    passes its own.
    """
    arguments = list(extra)
    if "--base" not in arguments:
        arguments += ["--base", "HEAD"]
    result = subprocess.run(
        [sys.executable, str(HERE), "--config", DEFAULT_CONFIG.name, *arguments],
        cwd=tmp,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)


#: git for a fixture repository: no hooks (the shared config may point
#: core.hooksPath at the project's own), no signing, and an identity that is
#: nobody's.
_FIXTURE_GIT = (
    "git",
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "user.email=check-boundaries@self.test",
    "-c",
    "user.name=check_boundaries self-test",
)


def _commit(tmp: Path) -> None:
    """Commit the tree as it stands — the base every case is judged against."""
    subprocess.run(
        [*_FIXTURE_GIT, "init", "-q"], cwd=tmp, check=True, capture_output=True
    )
    subprocess.run(
        [*_FIXTURE_GIT, "add", "--", ROOT_PACKAGE, DEFAULT_CONFIG.name],
        cwd=tmp,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [*_FIXTURE_GIT, "commit", "-q", "-m", "the tree this branch starts from"],
        cwd=tmp,
        check=True,
        capture_output=True,
    )


def _plant(config_text: str | None, files: dict[str, str], tmp: Path) -> None:
    """Lay down the seed and the declaration, commit them, then plant `files`.

    Commit first, and every planted file is new by construction: a case that
    wants baseline debt commits its violation too, with `_commit`.
    """
    _write(tmp, _SEED)
    if config_text is None:
        shutil.copy(DEFAULT_CONFIG, tmp / DEFAULT_CONFIG.name)
    else:
        (tmp / DEFAULT_CONFIG.name).write_text(config_text)
    _commit(tmp)
    _write(tmp, files)


def _run(config_text: str | None, files: dict[str, str], tmp: Path) -> tuple[int, str]:
    _plant(config_text, files, tmp)
    code, out, err = _invoke(tmp)
    return code, out + err


#: Exit code -> the status its record must carry. An exit code and a status that
#: can disagree is two answers to one question.
_STATUS_OF_EXIT = {OK: "pass", BROKEN: "fail", CANNOT_JUDGE: "cannot_judge"}


def _record_problem(
    tmp: Path, expected: int, extra: tuple[str, ...] = ()
) -> tuple[str | None, dict[str, Any] | None]:
    """(what is wrong with the --json run, the record).

    The record mode gets the same treatment as the rules themselves: a real
    process, a real exit code, and exactly one line of JSON on stdout in every
    outcome, because the snapshot parses that line blind.
    """
    code, output, _err = _invoke(tmp, *extra, "--json")
    if code != expected:
        return f"expected exit {expected}, got {code}", None
    lines = [line for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        return f"expected one line on stdout, got {len(lines)}: {lines!r}", None
    record = json.loads(lines[0])
    if expected == BROKEN:
        for needle in (
            "scripts/check_boundaries.py",
            "Backend: the import graph is checked, not assumed",
        ):
            if needle not in _err:
                return f"missing repair guidance on stderr: {needle!r}", record
    if record.get("id") != CHECK_ID:
        return f"record names {record.get('id')!r}", record
    want = _STATUS_OF_EXIT[expected]
    if record.get("status") != want:
        return f"status {record.get('status')!r}, want {want!r}", record
    return None, record


def _section_span(lines: list[str], contract_id: str) -> tuple[int, int]:
    """The inclusive-exclusive span of one `[importlinter:contract:...]`.

    The header is matched on its stripped text, because that is how the two
    readers that count — `configparser` for `declared_layers`, and
    import-linter itself — read it: a trailing space on the line, or a CR if the
    file is ever checked out with CRLF, is the same section to them. Exact
    equality here would make such a line a declaration the gate accepts and the
    self-test cannot find, and the meta-test would fail as a traceback rather
    than as the named failure it exists to print.
    """
    header = f"[importlinter:contract:{contract_id}]"
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == header), None
    )
    if start is None:
        raise RuntimeError(
            f".importlinter has no `{header}` line; the self-test cannot rewrite "
            "a layer table it cannot find"
        )
    for index_after in range(start + 1, len(lines)):
        if lines[index_after].lstrip().startswith("[importlinter"):
            return start, index_after
    return start, len(lines)


_SEED_DOMAIN_TABLE = ["layers =", "    agent", "    alpha : bar"]


def _with_option(text: str, contract_id: str, option: str, entry: str) -> str:
    """`text` with `entry` added to one option of one contract.

    The layer tables are carried across by `_seed_config`; this is for the two
    things it does not rewrite: a written exception — the real declaration
    carries no such block any more, so a case that wants one has to put one
    there — and a declaration naming a module the tree before the branch cannot
    have. It goes after the option's last line, because the format's rule is
    that a section's options come before anything else in it.
    """
    lines = text.split("\n")
    start, end = _section_span(lines, contract_id)
    head = next(
        (
            index
            for index in range(start + 1, end)
            if re.match(rf"^\s*{re.escape(option)}\s*=", lines[index])
        ),
        None,
    )
    if head is None:
        stop = end
        while stop > start + 1 and not lines[stop - 1].strip():
            stop -= 1
        lines[stop:stop] = [f"{option} =", f"    {entry}"]
        return "\n".join(lines)
    stop = head + 1
    while stop < end and re.match(r"^\s+\S", lines[stop]):
        stop += 1
    lines[stop:stop] = [f"    {entry}"]
    return "\n".join(lines)


def _seed_domain_children() -> set[str]:
    """The children of `app.domain` that `_SEED` plants.

    A child is a directory under `app/domain/` or a module directly in it —
    the same two shapes the contract's own layer table has to name.
    """
    children: set[str] = set()
    for relative in _SEED:
        parts = relative.split("/")
        if parts[:2] != ["app", "domain"] or len(parts) < 3:
            continue
        if len(parts) == 3 and parts[2].endswith(".py"):
            if parts[2] != "__init__.py":
                children.add(parts[2][:-3])
        else:
            children.add(parts[2])
    return children


def _seed_domain_table() -> list[str]:
    """The seed's domain table, checked against what `_SEED` plants.

    The table cannot be derived from the tree — which layer each package goes
    in is the thing the cases exercise — so the two are checked against each
    other instead. A package added to `_SEED` and not named here would come
    back as C5 reporting an unlisted module on a tree that is meant to satisfy
    it: a red that reads like the rule breaking, on a fixture nobody edited.
    """
    named = {
        name
        for line in _SEED_DOMAIN_TABLE[1:]
        for name in line.replace(":", " ").split()
    }
    planted = sorted(_seed_domain_children())
    if named != set(planted):
        raise RuntimeError(
            f"the seed's domain table names {sorted(named)} but _SEED plants "
            f"{planted} under app/domain; add the new one to _SEED_DOMAIN_TABLE "
            "in the layer it belongs to, or drop it from the seed"
        )
    return list(_SEED_DOMAIN_TABLE)


def _seed_config(declared: dict[str, str], text: str | None = None) -> str:
    """The real declaration, rewritten so the planted trees satisfy it.

    The trees `_SEED` plants hold none of the repository's domain packages, so
    the package table becomes the seed's own, cross-checked against the tree by
    `_seed_domain_table`: `agent` on top, `alpha` and `bar` in the layer below
    it — the smallest table with a downward edge to allow and an upward edge to
    catch. The turn table is the repository's, which the seed already builds the
    modules for. Everything else — the exemptions and the plumbing around them —
    is untouched, so what a case exercises is the contract and not the fixture.

    `text` is the declaration to rewrite, defaulting to the real file. A caller
    that already read one — `declared_layers` returns layer lists, not the text
    they came from, so the two can be a mutation apart — passes it, and the
    rewrite then works on what the gate read rather than on what is on disk.
    """
    lines = (text if text is not None else DEFAULT_CONFIG.read_text()).split("\n")
    turn_lines = [f"    {layer}" for layer in declared["turn-layers"]]
    for contract_id, replacement in (
        ("domain-layers", _seed_domain_table()),
        ("turn-layers", ["layers =", *turn_lines]),
    ):
        try:
            start, end = _section_span(lines, contract_id)
        except RuntimeError as exc:
            raise RuntimeError(f"the {contract_id} table: {exc}") from exc
        head = next(
            (
                index
                for index in range(start + 1, end)
                if re.match(r"^\s*layers\s*=", lines[index])
            ),
            None,
        )
        if head is None:
            raise RuntimeError(
                f"the {contract_id} table: the section has no `layers` option to "
                "rewrite for the planted trees"
            )
        stop = head + 1
        while stop < end and re.match(r"^\s+\S", lines[stop]):
            stop += 1
        lines[head:stop] = replacement
    return "\n".join(lines)


def _tail(output: str, lines: int = 25) -> str:
    """The end of a report — the part that says why it is red."""
    kept = output.strip().splitlines()
    if len(kept) <= lines:
        return "\n".join(kept)
    return f"... ({len(kept) - lines} earlier lines)\n" + "\n".join(kept[-lines:])


def _self_test_verdict(failures: list[str]) -> int:
    """The self-test's exit code and closing line, decided in one place.

    One is the meta-test's own breakage — a case that did not fire, or a
    fixture that could not be built — and stays distinct from what 2 means
    everywhere else: the check could not judge the tree.
    """
    if failures:
        print("\n".join(failures), file=sys.stderr)
        print(
            f"\nFAIL: check_boundaries self-test ({len(failures)} case(s))",
            file=sys.stderr,
        )
        return 1
    print(
        "PASS: check_boundaries self-test (9 contracts fire on the violation they "
        "exist to catch; an absent, a trimmed and a wildcarded declaration each "
        "come back not-passing; a base tree's own violations are held open while "
        "the ones this tree adds are reported, and a declaration naming a module "
        "this branch adds is still judged; every case agrees with the exit code "
        "under --json, on one line of stdout, naming all nine contracts)"
    )
    return 0


def self_test() -> int:
    failures: list[str] = []
    # Only self-tests need the project's default declaration. Normal --config,
    # --json and --help must not read it before judge can report exit 2.
    wildcard_config = _with_option(
        DEFAULT_CONFIG.read_text(),
        "routes-touch-no-models",
        "ignore_imports",
        "app.api.** -> app.domain.**",
    )

    # The planted trees replace every package under app.domain, so they carry
    # the layer tables across instead of re-typing them: a layer this file
    # forgot to copy would come back as an undeclared module, and the failure
    # would read like a broken rule rather than a stale fixture.
    #
    # A declaration that stopped naming one of those tables cannot seed them at
    # all. That is this run's failure and not a planted case — and the gate
    # already reports the state as `cannot judge: missing [...]` — so it is said
    # in one line rather than unwound as a traceback out of the meta-test.
    try:
        declared = declared_layers(DEFAULT_CONFIG.read_text())
    except RuntimeError as exc:
        failures.append(f"the default declaration: {exc}")
        print(f"  [FAIL] the default declaration: {exc}")
        return _self_test_verdict(failures)

    # Seeding the trees is the other half of that bootstrap: it rewrites the
    # tables in the declaration, so it fails for the same class of reason — a
    # line the gate reads differently than this file does, or a fixture that
    # drifted from the table it seeds. Same one-line treatment.
    try:
        seed_config = _seed_config(declared)
    except RuntimeError as exc:
        failures.append(f"the seed's declaration: {exc}")
        print(f"  [FAIL] the seed's declaration: {exc}")
        return _self_test_verdict(failures)

    def report(what: str, code: int, output: str, expected: int, needle: str | None):
        verdict = "ok"
        if code != expected:
            verdict = f"FAIL: expected {expected}, got {code}"
        elif needle and needle not in output:
            verdict = f"FAIL: red, but not for the stated reason (no {needle!r})"
        if verdict != "ok":
            failures.append(f"{what}: {verdict}\n{_tail(output)}")
        print(f"  [{verdict}] {what}")

    with tempfile.TemporaryDirectory(prefix="boundary-selftest-") as raw:
        sandbox = Path(raw)

        for index, (what, files, expected, needle, _broken) in enumerate(_CASES):
            tmp = sandbox / f"case{index}"
            tmp.mkdir()
            code, output = _run(seed_config, files, tmp)
            report(what, code, output, expected, needle)
            if expected == BROKEN:
                report(
                    f"{what}: repair command and rule chapter",
                    code,
                    output,
                    expected,
                    "scripts/check_boundaries.py` to recheck",
                )
                for contract_id in _broken:
                    # In `CONTRACT_IDS` order: the ids in `.importlinter` order.
                    prefix = ("C1", "C2", "C3", "T1", "T2", "T3", "T4", "C4", "C5")[
                        CONTRACT_IDS.index(contract_id)
                    ]
                    report(
                        f"{what}: specific fix",
                        code,
                        output,
                        expected,
                        _BOUNDARY_FIXES[prefix],
                    )
                report(
                    f"{what}: rule chapter",
                    code,
                    output,
                    expected,
                    "Backend: the import graph is checked, not assumed",
                )
                if index == 0:
                    report(
                        f"{what}: file and line",
                        code,
                        output,
                        expected,
                        "app/api/routes/demo.py:1",
                    )
                if index == 6:
                    report(
                        f"{what}: undeclared source",
                        code,
                        output,
                        expected,
                        "app/domain/agent/turn/extra.py:1",
                    )

        # The baseline: the tree at the merge base, graded by these same
        # contracts. Every case up to here plants its violation on top of a
        # committed seed, so what it plants is new by construction — which is
        # what those cases are about. These four are about the other half: the
        # tree the branch started from, which no case above can see.
        def baseline_case(
            name: str,
            what: str,
            base: dict[str, str],
            planted: dict[str, str],
            expected: int,
            broken: tuple[str, ...] = (),
            config_text: str | None = None,
            extra: tuple[str, ...] = (),
            frozen_at_least: int = 0,
            needle: str | None = None,
        ) -> dict[str, Any] | None:
            """One case whose base tree is a commit of its own.

            `base` is committed as the tree the branch starts from, `planted` is
            written afterwards and uncommitted, and `expected` is what the check
            must make of the difference.
            """
            tmp = sandbox / name
            tmp.mkdir()
            _plant(config_text if config_text is not None else seed_config, base, tmp)
            # `_plant` committed the seed; `base` was written onto it after that
            # commit, so this is what makes it the tree the branch starts from.
            # A case with no base debt of its own has nothing to add to it.
            if base:
                _commit(tmp)
            _write(tmp, planted)
            code, out, err = _invoke(tmp, *extra)
            # Both streams: the verdict and where the violations are go to
            # stderr when the run is broken, the record to stdout.
            output = out + err
            problem: str | None = None
            record: dict[str, Any] | None = None
            if code != expected:
                problem = f"expected {expected}, got {code}"
            else:
                problem, record = _record_problem(tmp, expected, extra)
            if problem is None and needle and needle not in output:
                problem = f"not for the stated reason (no {needle!r})"
            if problem is None and broken:
                # A broken run got its exit code past `_record_problem`, and that
                # is the only outcome where it withholds the record.
                assert record is not None
                got = sorted(
                    row["file"] for row in record["details"] if not row["kept"]
                )
                if got != sorted(broken):
                    problem = f"broken contracts {got}, want {sorted(broken)}"
            if problem is None and expected == BROKEN:
                for contract_id in broken:
                    fix = _BOUNDARY_FIXES[
                        ("C1", "C2", "C3", "T1", "T2", "T3", "T4", "C4", "C5")[
                            CONTRACT_IDS.index(contract_id)
                        ]
                    ]
                    if fix not in output:
                        problem = f"no fix for {contract_id} in the message"
                        break
            if problem is None and frozen_at_least:
                frozen = (record or {}).get("frozen")
                if not isinstance(frozen, int) or frozen < frozen_at_least:
                    problem = (
                        f"frozen={frozen!r}, want at least {frozen_at_least}: the "
                        "baseline did not come from the base tree"
                    )
            if problem is not None:
                failures.append(f"{what}: {problem}\n{_tail(output)}")
            print(f"  [{'ok' if problem is None else 'FAIL: ' + problem}] {what}")
            return record

        # A violation the tree at the merge base already has. Nothing else
        # changed, so this tree owes nothing for it — and `frozen` in the record
        # has to be non-zero, because a run that counted nothing would have
        # judged this tree against an empty base.
        baseline_case(
            "debt",
            "a violation the base tree already has",
            {"app/domain/bar/services.py": _IMPORT_AGENT},
            {},
            OK,
            frozen_at_least=1,
        )

        # The same base, one more violation. The new one is caught while the old
        # one stays held open; a baseline that silenced both would answer OK
        # here, which is the failure this case exists to catch.
        baseline_case(
            "debt-and-new",
            "a new violation on top of the base tree's own",
            {"app/domain/bar/services.py": _IMPORT_AGENT},
            {"app/api/routes/demo.py": "from app.domain.bar.models import Bar\n"},
            BROKEN,
            broken=("routes-touch-no-models",),
            frozen_at_least=1,
        )

        # A declaration naming a module this branch adds. The tree before the
        # branch has no such module, and import-linter refuses to grade a tree
        # whose declaration names a missing one — so without a placeholder for
        # the new module this is a verdict the branch cannot satisfy, on a
        # change that is exactly what the declaration is there to describe.
        baseline_case(
            "new-module",
            "a declaration naming a module this branch adds",
            {},
            {"app/api/brand_new_payloads.py": ""},
            OK,
            config_text=_with_option(
                seed_config,
                "routes-touch-no-models",
                "source_modules",
                "app.api.brand_new_payloads",
            ),
        )

        # A base that is not there. Not a pass and not a guess: exit 2, the one
        # answer for a tree that cannot be reached.
        baseline_case(
            "no-base",
            "a base that does not resolve",
            {},
            {},
            CANNOT_JUDGE,
            extra=("--base", "refs/heads/no-such-branch"),
            needle="merge base",
        )

        # A config with its declarations trimmed. This is the shape a silent
        # narrowing takes: valid INI, valid contracts, fewer of them.
        tmp = sandbox / "thin"
        tmp.mkdir()
        report(
            "a declaration that lost all nine contracts",
            *_run(_THIN_CONFIG, {}, tmp),
            CANNOT_JUDGE,
            "missing",
        )

        # Every individual contract is mandatory, not just a nonempty config.
        import configparser

        for contract_id in CONTRACT_IDS:
            config = configparser.ConfigParser()
            config.read(DEFAULT_CONFIG)
            config.remove_section(f"importlinter:contract:{contract_id}")
            import io

            contents = io.StringIO()
            config.write(contents)
            missing_tmp = sandbox / f"missing-{contract_id}"
            missing_tmp.mkdir()
            report(
                f"missing mandatory contract {contract_id}",
                *_run(contents.getvalue(), {}, missing_tmp),
                CANNOT_JUDGE,
                f"missing ['{contract_id}']",
            )

        # The state no case above can plant: a default declaration that stopped
        # naming a layer table, which is where the planted trees get theirs.
        # The self-test has to say so and fail — unwinding a traceback through
        # the same exit code is the failure nobody can read. A renamed section
        # is the shape a contract rename leaves behind.
        unlayered = sandbox / "unlayered"
        unlayered.mkdir()
        _plant(seed_config, {}, unlayered)
        declaration = unlayered / DEFAULT_CONFIG.name
        declaration.write_text(
            declaration.read_text().replace(
                "[importlinter:contract:domain-layers]",
                "[importlinter:contract:domain-layers-renamed]",
            )
        )
        copied = unlayered / "scripts" / HERE.name
        copied.parent.mkdir()
        shutil.copy(HERE, copied)
        result = subprocess.run(
            [sys.executable, str(copied), "--self-test"],
            cwd=unlayered,
            capture_output=True,
            text=True,
        )
        report(
            "a declaration that lost a layer table",
            result.returncode,
            result.stdout + result.stderr,
            1,
            # The named failure, not the exit code: a traceback out of the
            # bootstrap carries the same text and the same 1.
            "  [FAIL] the default declaration:",
        )

        # No config at all. Nothing to judge, and 2 is not a pass.
        tmp = sandbox / "absent"
        tmp.mkdir()
        for relative, contents in _SEED.items():
            path = tmp / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
        result = subprocess.run(
            [sys.executable, str(HERE), "--config", ".importlinter"],
            cwd=tmp,
            capture_output=True,
            text=True,
        )
        report(
            "no config to read at all",
            result.returncode,
            result.stdout + result.stderr,
            CANNOT_JUDGE,
            "no .importlinter",
        )

        # A wildcard exemption: parseable, honoured by import-linter, and the
        # one thing the baseline format must not accept.
        tmp = sandbox / "wildcard"
        tmp.mkdir()
        report(
            "an exception written as a wildcard",
            *_run(wildcard_config, {}, tmp),
            BROKEN,
            "wildcard",
        )

        # Copy the checker away from its default config. --config must remain
        # independent of that file, and an absent default must be judged, not
        # crash while importing self-test fixture constants. The explicit config
        # is the seed's rewrite of the real one, so this run judges the seed
        # tree the way every other case does.
        detached = sandbox / "detached"
        detached.mkdir()
        _plant(seed_config, {}, detached)
        alternate = detached / "explicit.ini"
        (detached / DEFAULT_CONFIG.name).rename(alternate)
        copied = detached / "scripts" / HERE.name
        copied.parent.mkdir()
        shutil.copy(HERE, copied)
        detached_cases = [
            ("help with no default config", ("--help",), OK, False),
            (
                "missing default config",
                ("--base", "HEAD", "--json"),
                CANNOT_JUDGE,
                True,
            ),
            (
                "explicit config with no default config",
                ("--config", str(alternate), "--base", "HEAD", "--json"),
                OK,
                True,
            ),
        ]
        for what, arguments, expected, has_record in detached_cases:
            result = subprocess.run(
                [sys.executable, str(copied), *arguments],
                cwd=detached,
                capture_output=True,
                text=True,
            )
            report(what, result.returncode, result.stderr, expected, None)
            if has_record:
                try:
                    output_lines = result.stdout.strip().splitlines()
                    assert len(output_lines) == 1
                    record = json.loads(output_lines[0])
                    assert record["id"] == CHECK_ID
                    assert record["status"] == _STATUS_OF_EXIT[expected]
                except (AssertionError, KeyError, ValueError) as exc:
                    failures.append(f"{what}: invalid single-line JSON: {exc!r}")
        # A non-file at the default path also must not be touched by --config.
        (detached / DEFAULT_CONFIG.name).mkdir()
        result = subprocess.run(
            [
                sys.executable,
                str(copied),
                "--config",
                str(alternate),
                "--base",
                "HEAD",
                "--json",
            ],
            cwd=detached,
            capture_output=True,
            text=True,
        )
        report(
            "explicit config with unreadable default path",
            result.returncode,
            result.stdout + result.stderr,
            OK,
            None,
        )

        # Every planted tree again under --json: the record has to agree with
        # the exit code the gate uses, and name every contract it claims to
        # have run — a check that quietly narrowed itself must not read as a
        # cleaner tree.
        json_cases = [
            (f"case{index}", what, expected)
            for index, (what, _f, expected, _n, _broken) in enumerate(_CASES)
        ]
        json_cases.append(
            ("thin", "a declaration that lost all nine contracts", CANNOT_JUDGE)
        )
        json_cases.append(("wildcard", "an exception written as a wildcard", BROKEN))
        json_cases.append(("absent", "no config to read at all", CANNOT_JUDGE))
        json_cases.extend(
            (f"missing-{cid}", f"missing mandatory contract {cid}", CANNOT_JUDGE)
            for cid in CONTRACT_IDS
        )
        for where, what, expected in json_cases:
            problem, record = _record_problem(sandbox / where, expected)
            if problem is None and where.startswith("case"):
                ids = [row.get("file") for row in record.get("details", [])]
                if ids != list(CONTRACT_IDS):
                    problem = f"details name {ids}, want {list(CONTRACT_IDS)}"
                else:
                    expected_broken = set(_CASES[int(where[4:])][4])
                    broken = {
                        row["file"] for row in record["details"] if not row["kept"]
                    }
                    if broken != expected_broken:
                        problem = (
                            f"broken contracts {sorted(broken)}, "
                            f"want {sorted(expected_broken)}"
                        )
            if (
                problem is None
                and where == "wildcard"
                and record.get("actual") is not None
            ):
                # The wildcard is refused before the contracts run, so nothing
                # was counted. 0 there would read as a tree with no violations.
                problem = (
                    "a run that counted nothing reports "
                    f"actual={record.get('actual')!r}"
                )
            verdict = "ok" if problem is None else f"FAIL: {problem}"
            if problem is not None:
                failures.append(f"--json {what}: {problem}")
            print(f"  [{verdict}] --json {what}")

    return _self_test_verdict(failures)


if __name__ == "__main__":
    sys.exit(main())
