#!/usr/bin/env python3
"""Freeze three kinds of frontend debt as exact sets, never just totals.

    python3 .claude/scripts/scene-debt-ratchet.py
    python3 .claude/scripts/scene-debt-ratchet.py --update
    python3 .claude/scripts/scene-debt-ratchet.py --kind children --json
    python3 .claude/scripts/scene-debt-ratchet.py --self-test

Children are (debt route, rendered non-A SFC) edges, routes are debt pages
calling useRoute, and network is every production source file importing the
old network layer (including types, re-exports and literal dynamic imports).
A debt page is non-A and not a verified container under the EXISTING scene
rule. Panels and sibling views are not route pages. Each set may only shrink:
one old edge disappearing never pays for a different edge appearing.

The initial baseline is checked in with this rule. There is no bootstrap flag:
a missing/unreadable baseline is exit 2, and --update refuses all new debt
without changing the file. --base also rejects a hand-expanded baseline against
the merge base, so editing JSON is not an escape in CI.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ratchet_report import as_json, cannot_judge, emit, verdict

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "frontend/scene-debt-baseline.json"
KINDS = {
    "children": "scene-non-a-children",
    "routes": "scene-use-route",
    "network": "legacy-network",
}

#: One fix per kind: the three have three repairs and three landing spots, so a
#: catch-all sentence would send a `children` failure where a `routes` one is wrong.
FIXES = {
    "children": (
        "scene-non-a-children — a scene renders from props and emits alone, so a "
        "child it renders must not fetch/read the route/read a business store. Make "
        "that child grade A (it takes the data as props), or make the page a "
        "container of a grade-A <PageName>View.vue. docs/manual/dev/scenes.md"
    ),
    "routes": (
        "scene-use-route — the route read belongs to the container page, not the "
        "scene it renders. Keep the page as the container, and put the rendering in a "
        "sibling <PageName>View.vue that takes the route's values as props. Moving "
        "useRoute around inside the same page (still rendering in place) is not the "
        "fix. docs/manual/dev/scenes.md"
    ),
    "network": (
        "legacy-network — new code reaches the server through @/api, and a scene not "
        "at all: replace the @/network import at its caller and pass the data in as "
        "props. docs/manual/dev/frontend.md#state-network"
    ),
}


def baseline_line(baseline: str, kind: str, entry: str) -> int | None:
    """The 1-based line of `entry` inside `kind`'s array, or None.

    Read-only, and scoped to the one kind: a path that is both a route page and a
    network importer appears in two arrays, so the search never crosses into a
    sibling kind's list. It matches the exact quoted token json.dumps writes, so
    it works for indented JSON (one line per entry) and one-line JSON (line 1)
    alike. parse_baseline stays the only judge; this just points a BASELINE GREW
    entry at the line to delete.
    """
    start = baseline.find(f'"{kind}":')
    if start < 0:
        return None
    end = baseline.find("]", start)
    region = baseline[start:] if end < 0 else baseline[start:end]
    index = region.find(json.dumps(entry))
    if index < 0:
        return None
    return baseline.count("\n", 0, start + index) + 1


def failure_text(
    new: dict[str, set[str]], expanded: dict[str, set[str]], baseline: str = ""
) -> str:
    """The per-kind entries and what to do about each.

    Printed on stdout, or on stderr under --json. Each kind lists its NEW and
    BASELINE GREW entries as the exact path/edge; a BASELINE GREW entry also
    carries the line in the baseline JSON to put back by hand.
    """
    lines = ["FAIL: new debt cannot be frozen."]
    for kind in KINDS:
        if new[kind]:
            lines.append(f"  {FIXES[kind]}")
            for entry in sorted(new[kind]):
                lines.append(f"    NEW: {entry}")
        if expanded[kind]:
            lines.append(
                f"  {KINDS[kind]}: BASELINE GREW — the baseline may only shrink, and "
                "--update refuses while this is present, so it cannot remove the line "
                "for you: put the added baseline line back by hand."
            )
            for entry in sorted(expanded[kind]):
                where = baseline_line(baseline, kind, entry)
                marker = f" (baseline line {where})" if where is not None else ""
                lines.append(f"    BASELINE GREW: {entry}{marker}")
    lines.append(
        "The baseline only ever shrinks; the documented tighten command is "
        "'pnpm --dir frontend run lint:scene-debt:update' (run from the repo root)."
    )
    return "\n".join(lines)


def scene_module() -> Any:
    source = Path(__file__).with_name("scene-ratchet.py")
    spec = importlib.util.spec_from_file_location("_scene_ratchet", source)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_baseline(text: str) -> dict[str, set[str]]:
    data = json.loads(text)
    if (
        not isinstance(data, dict)
        or set(data) != {"version", *KINDS}
        or data["version"] != 1
    ):
        raise ValueError("expected version 1 with children, routes and network sets")
    result = {}
    for kind in KINDS:
        entries = data[kind]
        if not isinstance(entries, list) or any(
            not isinstance(key, str) for key in entries
        ):
            raise ValueError(f"{kind}: expected a list of paths")
        if len(entries) != len(set(entries)):
            raise ValueError(f"{kind}: duplicate entries")
        for key in entries:
            paths = key.split(" -> ") if kind == "children" else [key]
            if len(paths) != (2 if kind == "children" else 1):
                raise ValueError(f"{kind}: invalid edge {key!r}")
            for name in paths:
                if (
                    not name.startswith("src/")
                    or "\\" in name
                    or "*" in name
                    or any(part in ("", ".", "..") for part in name.split("/"))
                ):
                    raise ValueError(f"{kind}: invalid path {name!r}")
        result[kind] = set(entries)
    return result


def baseline_text(sets: dict[str, set[str]]) -> str:
    return (
        json.dumps(
            {"version": 1, **{kind: sorted(sets[kind]) for kind in KINDS}}, indent=2
        )
        + "\n"
    )


def write_baseline(path: Path, sets: dict[str, set[str]]) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(baseline_text(sets))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(path.stat().st_mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def scan_scripts(root: Path, grade: Any) -> dict[str, Any]:
    src = grade.repo_src(root)
    files = []
    for file in sorted(src.rglob("*")):
        if not file.is_file() or file.suffix not in (
            ".vue",
            ".ts",
            ".js",
            ".tsx",
            ".jsx",
        ):
            continue
        if (
            ".spec." in file.name
            or ".test." in file.name
            or file.name.endswith(".d.ts")
        ):
            continue
        text = file.read_text(encoding="utf-8")
        files.append((file.relative_to(root / "frontend").as_posix(), text))
    scanner = ROOT / "frontend/scripts/scene-debt-scan.mjs"
    run = subprocess.run(
        ["node", str(scanner)],
        input=json.dumps(files),
        text=True,
        capture_output=True,
        timeout=120,
    )
    if run.returncode:
        raise ValueError(run.stderr.strip() or "script scan did not complete")
    return json.loads(run.stdout)


def collect(root: Path) -> dict[str, set[str]]:
    root = root.resolve()
    scene = scene_module()
    grade = scene.load_frontend_grade()
    reach = grade.api_reach(root)
    try:
        grades = scene.grade_scenes(root, reach)
        pages = set(scene.route_pages(root))
        pairs = scene.paired_views(root, pages, reach)
    except scene.Unjudgeable as exc:
        raise ValueError(str(exc)) from exc
    scripts = scan_scripts(root, grade)
    actual: dict[str, set[str]] = {kind: set() for kind in KINDS}
    for key, scan in scripts.items():
        if scan["network"]:
            actual["network"].add(key)
    child_grades = {}
    for page in sorted(pages):
        if grades[page].standalone or scene.container_view(page, grades, pairs):
            continue
        key = scene.key_of(page)
        scan = scripts[key]
        if scan["useRoute"]:
            actual["routes"].add(key)
        text = (root / page).read_text(encoding="utf-8")
        rendered = scene.template_tags(grade, text) | set(scan["rendered"])
        for tag in rendered:
            for local in scene.resolve_names(tag):
                if local not in scan["components"]:
                    continue
                for spec in scan["components"][local]:
                    target = grade.resolve_spec(
                        spec, root / page, root / "frontend/src"
                    )
                    if target is None or target.suffix != ".vue":
                        continue
                    if target not in child_grades:
                        child_grades[target] = grade.grade_component(
                            root, target, reach
                        )
                    if not child_grades[target].standalone:
                        actual["children"].add(
                            f"{key} -> {scene.key_of(target.relative_to(root).as_posix())}"
                        )
                break  # Vue uses the first matching binding.
    return actual


def compare(
    actual: dict[str, set[str]], frozen: dict[str, set[str]]
) -> dict[str, set[str]]:
    return {kind: actual[kind] - frozen[kind] for kind in KINDS}


def approved_baseline(root: Path, path: Path, base: str) -> dict[str, set[str]] | None:
    """None means this is the PR introducing the baseline, not an empty set."""
    relative = path.relative_to(root).as_posix()
    merge = subprocess.run(
        ["git", "merge-base", "HEAD", base], cwd=root, text=True, capture_output=True
    )
    if merge.returncode:
        raise ValueError(f"cannot find merge base with {base}: {merge.stderr.strip()}")
    ref = f"{merge.stdout.strip()}:{relative}"
    exists = subprocess.run(
        ["git", "ls-tree", merge.stdout.strip(), "--", relative],
        cwd=root,
        text=True,
        capture_output=True,
    )
    if exists.returncode:
        raise ValueError("cannot inspect the approved baseline")
    if not exists.stdout.strip():
        return None
    old = subprocess.run(["git", "show", ref], cwd=root, text=True, capture_output=True)
    if old.returncode:
        raise ValueError(f"cannot read the approved baseline: {old.stderr.strip()}")
    return parse_baseline(old.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--base", help="reject baseline growth against this git merge base"
    )
    parser.add_argument("--kind", choices=KINDS)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return subprocess.call(
            [
                sys.executable,
                str(Path(__file__).with_name("test_scene_debt_ratchet.py")),
            ]
        )
    root = args.root.resolve()
    path = args.baseline or root / BASELINE
    check_id = KINDS[args.kind] if args.kind else "scene-debt"
    raw = ""
    try:
        raw = path.read_text(encoding="utf-8")
        frozen = parse_baseline(raw)
        actual = collect(root)
        new = compare(actual, frozen)
        expanded = {kind: set() for kind in KINDS}
        if args.base:
            approved = approved_baseline(root, path, args.base)
            if approved is not None:
                expanded = compare(frozen, approved)
    except (
        OSError,
        ValueError,
        LookupError,
        KeyError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        cannot_judge(check_id, str(exc))
    kinds = [args.kind] if args.kind else list(KINDS)
    # Even a per-kind update must not absorb a violation in another kind.
    update_ok = not any(new.values()) and not any(expanded.values())
    ok = not any(new[kind] or expanded[kind] for kind in kinds)
    if args.update:
        if not update_ok:
            kinds = list(KINDS)
            ok = False
        else:
            try:
                write_baseline(path, actual)
            except OSError as exc:
                print(f"cannot update baseline: {exc}", file=sys.stderr)
                cannot_judge(check_id, str(exc))
    if as_json():
        # --json (what ci:fast and the snapshot collector call) keeps stdout the
        # one record line; on failure the fix report rides on stderr instead.
        if not ok:
            print(failure_text(new, expanded, raw), file=sys.stderr)
        selected = kinds[0] if len(kinds) == 1 else None
        emit(
            verdict(
                check_id=check_id,
                ok=ok,
                actual=len(actual[selected])
                if selected
                else {k: len(actual[k]) for k in kinds},
                frozen=len(frozen[selected])
                if selected
                else {k: len(frozen[k]) for k in kinds},
                stale=[
                    f"{k}: {entry}"
                    for k in kinds
                    for entry in sorted(frozen[k] - actual[k])
                ],
                details=[
                    {"kind": k, "entry": entry, "reason": reason}
                    for k in kinds
                    for entries, reason in (
                        (new[k], "new debt"),
                        (expanded[k], "baseline grew"),
                    )
                    for entry in sorted(entries)
                ],
            )
        )
    else:
        for kind in kinds:
            print(
                f"{KINDS[kind]}: {len(actual[kind])} current / {len(frozen[kind])} frozen"
            )
            if args.list:
                for entry in sorted(actual[kind]):
                    print(f"  {entry}")
        if not ok:
            # failure_text carries the per-kind NEW / BASELINE GREW entries, so the
            # count loop above stays counts-only (--list still lists every entry).
            print(failure_text(new, expanded, raw))
        elif args.update:
            print(f"Updated {path}: only removed paid-down entries")
        else:
            print(
                "PASS: no new debt; pnpm --dir frontend run lint:scene-debt:update tightens the baseline"
            )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
