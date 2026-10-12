#!/usr/bin/env python3
"""Gate: the module boundaries declared in `backend/.importlinter`.

    cd backend && uv run python scripts/check_boundaries.py
    cd backend && uv run python scripts/check_boundaries.py --self-test

    0 = every contract held      1 = a contract broke, or the baseline is
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

**The baseline's shape.** Each contract freezes its existing violations in an
`ignore_imports` block, one line per exception, each naming a module and a
target. import-linter also accepts wildcards there, and a wildcard is exactly
how a ratchet turns into a rubber stamp: `app.api.** -> app.domain.**` would
silence every future violation of C2 while looking like a considered exemption.
The format cannot prevent it, so this check refuses one — it is a violation of
the rule, not a problem with the tooling, and it exits 1 rather than 2.

Everything else is import-linter's own machinery: the contracts live in
`.importlinter`, the exceptions live in the same file, and `lint-imports` alone
is enough to reproduce a verdict by hand. This script adds the two things above
and nothing more.

    ... --self-test   plant each violation the eight contracts exist to catch,
                      in a throwaway tree, and require them to fire
    ... --json        one JSON record on stdout instead of the report; the exit
                      code is unchanged. The record's shape is fixed in
                      docs/topics/棘轮页方案 section 3.1.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn

HERE = Path(__file__).resolve()
BACKEND_ROOT = HERE.parents[1]
DEFAULT_CONFIG = BACKEND_ROOT / ".importlinter"

#: The root package the contracts are written against. The check runs from the
#: directory that contains it, because that is how grimp finds it.
ROOT_PACKAGE = "app"

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
)

OK, BROKEN, CANNOT_JUDGE = 0, 1, 2

#: The check id in the ratchet snapshot, and the longest a `reason` may be.
CHECK_ID = "be-contracts"
REASON_LIMIT = 2000


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

    `actual` is how many frozen exceptions the tree still needs, `frozen` how
    many are on the books. Those are the two numbers the board used to take
    from the baseline's line count — which is why an exception that stopped
    matching anything could sit there unnoticed until the day it was counted.
    Without rows (a wildcard in the baseline, refused before the contracts run)
    neither number was measured, so both are null rather than 0.
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


def _contract_rows(report, options) -> list[dict[str, Any]]:
    """What each contract froze, what it still needs, and how many are unused.

    import-linter's own bookkeeping, not the baseline: `ignored_import_count`
    is the number of exceptions the tree still needs, and a warning is an
    exception on the books that no longer matches any import.
    """
    ids = {option.get("name"): option.get("id") for option in options.contracts_options}
    rows: list[dict[str, Any]] = []
    for contract, check in report.get_contracts_and_checks():
        frozen = getattr(contract, "ignore_imports", None) or []
        rows.append(
            {
                "file": ids.get(contract.name) or "?",
                "name": contract.name,
                "kept": bool(check.kept),
                "actual": int(check.ignored_import_count),
                "frozen": len(frozen),
                "stale": len(check.warnings),
                "unused": [str(warning) for warning in check.warnings],
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
                add(module, "undeclared turn layer", (1,))
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
}


def _boundary_failure_message(config_path: Path, report, frozen: int) -> str:
    failed = []
    for contract, check in report.get_contracts_and_checks():
        if not check.kept:
            failed.append(
                _BOUNDARY_FIXES.get(contract.name.split(" ", 1)[0], contract.name)
            )
    locations = _failure_locations(report)
    location_text = "\n".join(f"  {location}" for location in locations)
    return (
        "a boundary broke. The exceptions already known are frozen in "
        f"{config_path.name} ({frozen} entries).\n"
        + "\n".join(f"{fix}" for fix in failed)
        + (
            f"\nViolations (fix the import at this file:line):\n{location_text}"
            if location_text
            else ""
        )
        + "\nRun `cd backend && uv run python scripts/check_boundaries.py` to recheck. "
        "After removing a stale frozen edge, run `cd backend && uv run python "
        "scripts/boundary_baseline.py --update`; never use --update to freeze a new "
        "violation. Rule: `.claude/rules/architecture.md`, `Backend: the import "
        "graph is checked, not assumed`."
    )


def _baseline_entries(options) -> list[tuple[str, str]]:
    """(contract id, raw expression) for every frozen exception."""
    entries = []
    for contract in options.contracts_options:
        raw = contract.get("ignore_imports")
        if raw is None:
            continue
        lines = raw if isinstance(raw, list) else [raw]
        entries.extend((contract.get("id", "?"), line) for line in lines)
    return entries


def judge(config_path: Path) -> tuple[int, str, list[dict[str, Any]] | None]:
    """Run the contracts in `config_path` against the tree at the cwd.

    The third element is the per-contract breakdown when the contracts actually
    ran, and None when the run stopped before it could count anything.
    """
    # `lint-imports` finds the root package on sys.path, and its CLI puts the
    # working directory there first. Do the same, so this check and the tool it
    # wraps judge the same tree from the same place.
    sys.path.insert(0, str(Path.cwd()))
    try:
        # Importing `api` is what registers import-linter's built-in contract
        # types and its settings; without it `create_report` raises.
        from importlinter.api import read_configuration
        from importlinter.application import rendering
        from importlinter.application.use_cases import (
            _register_contract_types,
            create_report,
        )
        from importlinter.application.user_options import UserOptions
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

    wildcards = [entry for entry in _baseline_entries(options) if "*" in entry[1]]
    if wildcards:
        source_lines = config_path.read_text(encoding="utf-8").splitlines()
        line_of = {line.strip(): i for i, line in enumerate(source_lines, 1)}
        listing = "\n".join(
            f"  {config_path}:{line_of.get(expr, '?')} [{cid}] {expr}"
            for cid, expr in wildcards
        )
        return (
            BROKEN,
            (
                "a frozen exception uses a wildcard, which the baseline does not "
                f"allow:\n{listing}\n\n"
                "Name the module and its target exactly. A wildcard exemption\n"
                "silences every future violation of that contract while looking\n"
                "like one considered exception — the ratchet has to be able to\n"
                "say which pair it is holding open.\n"
                "Restore the exact frozen importer -> imported pair in "
                f"{config_path.name}; do not widen ignore_imports. Recheck with "
                "`cd backend && uv run python scripts/check_boundaries.py`. "
                "Rule: .claude/rules/architecture.md, "
                "Backend: the import graph is checked, not assumed."
            ),
            None,
        )

    try:
        _register_contract_types(options)
        report = create_report(options, cache_dir=None)
    except Exception as exc:
        return CANNOT_JUDGE, f"the contracts could not be run at all: {exc!r}", None

    rendering.render_report(report)

    rows = _contract_rows(report, options)
    if report.could_not_run:
        return CANNOT_JUDGE, "import-linter could not run one of the contracts", None
    if report.contains_failures:
        frozen = len(_baseline_entries(options))
        return (
            BROKEN,
            _boundary_failure_message(config_path, report, frozen),
            rows,
        )
    return OK, "", rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None, help="the .importlinter to read")
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

    # Under --json stdout carries one record and nothing else, so the whole
    # judgement runs with stdout pointed at stderr: import-linter and grimp
    # print as they work, and none of it may land around the record.
    if _as_json():
        with contextlib.redirect_stdout(sys.stderr):
            code, message, rows = judge(config_path)
    else:
        code, message, rows = judge(config_path)
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
    "app/api/routes/__init__.py": "",
    "app/api/routes/demo.py": "from app.domain.bar.services import read_bar\n",
    "app/api/routes/other.py": "",
    "app/api/shared.py": "",
    "app/core/__init__.py": "",
    "app/domain/__init__.py": "",
    "app/domain/bar/__init__.py": "",
    "app/domain/bar/models.py": "class Bar:\n    ...\n",
    "app/domain/bar/services.py": "def read_bar():\n    ...\n",
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
]

#: A config that declares nothing, and one that declares all eight contracts
#: but freezes an exception as a wildcard — the one thing this format must not
#: accept, and which import-linter itself honours happily.
_THIN_CONFIG = "[importlinter]\nroot_package = app\n"


def _invoke(tmp: Path, *extra: str) -> tuple[int, str, str]:
    """Run the real check, as a process, from the tree it is judging — the same
    way CI and a commit run it. In-process would have to juggle sys.path entries
    for a root package named `app`, which is exactly the kind of cleverness that
    makes a check pass for a reason nobody can name.

    stdout and stderr are kept apart on purpose: which stream a thing lands on
    is part of what --json promises, and a merged stream cannot check it.
    """
    result = subprocess.run(
        [sys.executable, str(HERE), "--config", DEFAULT_CONFIG.name, *extra],
        cwd=tmp,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def _plant(config_text: str | None, files: dict[str, str], tmp: Path) -> None:
    for relative, contents in {**_SEED, **files}.items():
        path = tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    if config_text is None:
        shutil.copy(DEFAULT_CONFIG, tmp / DEFAULT_CONFIG.name)
    else:
        (tmp / DEFAULT_CONFIG.name).write_text(config_text)


def _run(config_text: str | None, files: dict[str, str], tmp: Path) -> tuple[int, str]:
    _plant(config_text, files, tmp)
    code, out, err = _invoke(tmp)
    return code, out + err


#: Exit code -> the status its record must carry. An exit code and a status that
#: can disagree is two answers to one question.
_STATUS_OF_EXIT = {OK: "pass", BROKEN: "fail", CANNOT_JUDGE: "cannot_judge"}


def _record_problem(
    tmp: Path, expected: int
) -> tuple[str | None, dict[str, Any] | None]:
    """(what is wrong with the --json run, the record).

    The record mode gets the same treatment as the rules themselves: a real
    process, a real exit code, and exactly one line of JSON on stdout in every
    outcome, because the snapshot parses that line blind.
    """
    code, output, _err = _invoke(tmp, "--json")
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


def _tail(output: str, lines: int = 25) -> str:
    """The end of a report — the part that says why it is red."""
    kept = output.strip().splitlines()
    if len(kept) <= lines:
        return "\n".join(kept)
    return f"... ({len(kept) - lines} earlier lines)\n" + "\n".join(kept[-lines:])


def self_test() -> int:
    failures: list[str] = []
    # Only self-tests need the project's default declaration. Normal --config,
    # --json and --help must not read it before judge can report exit 2.
    wildcard_config = DEFAULT_CONFIG.read_text().replace(
        "ignore_imports =\n", "ignore_imports =\n    app.api.** -> app.domain.**\n", 1
    )

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
            code, output = _run(None, files, tmp)
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
                    prefix = ("C1", "C2", "C3", "T1", "T2", "T3", "T4", "C4")[
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

        # A config with its declarations trimmed. This is the shape a silent
        # narrowing takes: valid INI, valid contracts, fewer of them.
        tmp = sandbox / "thin"
        tmp.mkdir()
        report(
            "a declaration that lost all eight contracts",
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
        # crash while importing self-test fixture constants.
        detached = sandbox / "detached"
        detached.mkdir()
        _plant(None, {}, detached)
        alternate = detached / "explicit.ini"
        (detached / DEFAULT_CONFIG.name).rename(alternate)
        copied = detached / "scripts" / HERE.name
        copied.parent.mkdir()
        shutil.copy(HERE, copied)
        detached_cases = [
            ("help with no default config", ("--help",), OK, False),
            ("missing default config", ("--json",), CANNOT_JUDGE, True),
            (
                "explicit config with no default config",
                ("--config", str(alternate), "--json"),
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
            [sys.executable, str(copied), "--config", str(alternate), "--json"],
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
            ("thin", "a declaration that lost all eight contracts", CANNOT_JUDGE)
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

    if failures:
        print("\n".join(failures), file=sys.stderr)
        print(
            f"\nFAIL: check_boundaries self-test ({len(failures)} case(s))",
            file=sys.stderr,
        )
        return 1
    print(
        "PASS: check_boundaries self-test (8 contracts fire on the violation they "
        "exist to catch; an absent, a trimmed and a wildcarded declaration each "
        "come back not-passing; every case agrees with the exit code under --json, "
        "on one line of stdout, naming all eight contracts)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
