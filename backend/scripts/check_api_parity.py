#!/usr/bin/env python3
"""Compare FastAPI routes against the legacy OpenAPI catalog.

Outputs:
- missing endpoints (by method + path)
- extra endpoints (registered in FastAPI but not present in spec)
- parameter-name mismatches (same canonical path shape but different placeholder names)

Exits 1 when there is anything to report, so a caller can fail on drift instead of
reading a report that always succeeds.
"""

import json
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from app.main import app

CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "tests" / "contract" / "api_catalog.json"
)


def canonicalize(path: str) -> str:
    """Replace placeholder names with '{}' to compare templates ignoring parameter casing."""  # noqa: E501
    return re.sub(r"\{[^{}]+\}", "{}", path)


def load_spec() -> set[tuple[str, str]]:
    payload = json.loads(CATALOG_PATH.read_text())
    return {(item["method"], item["path"]) for item in payload}


def collect_app_routes() -> set[tuple[str, str]]:
    """Every HTTP method and effective path the app serves.

    Since FastAPI 0.137 an app keeps an included router as one
    ``_IncludedRouter`` in ``app.routes``, and only that object's contexts carry
    the prefixed path and methods. Reading ``app.routes`` directly would see
    just the handful of routes declared on the app itself and call everything
    else missing; ``scripts/route_index.py`` and ``app/api/write_access.py``
    unwrap routers the same way.
    """
    entries: set[tuple[str, str]] = set()
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        for effective in contexts() if contexts is not None else (route,):
            methods = getattr(effective, "methods", None)
            if not methods:
                continue
            for method in methods:
                if method in {"HEAD", "OPTIONS"}:
                    continue
                entries.add((method, effective.path))
    return entries


def group_by_canonical(entries: Iterable[tuple[str, str]]):
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for method, path in entries:
        grouped[(method, canonicalize(path))].add(path)
    return grouped


def main() -> int:
    spec = load_spec()
    app_routes = collect_app_routes()

    spec_canon = group_by_canonical(spec)
    app_canon = group_by_canonical(app_routes)

    missing = sorted(spec - app_routes)
    extra = sorted(app_routes - spec)

    print(f"Spec endpoints: {len(spec)}")
    print(f"App endpoints: {len(app_routes)}")
    print(f"Missing exact matches: {len(missing)}")
    if missing:
        for method, path in missing[:20]:
            print("  ", method, path)
        if len(missing) > 20:
            print("  ...")
    print(f"Extra endpoints: {len(extra)}")
    if extra:
        for method, path in extra[:20]:
            print("  ", method, path)
        if len(extra) > 20:
            print("  ...")

    # Detect canonical matches with naming mismatches
    canonical_mismatches: list[tuple[str, str, set[str], set[str]]] = []
    for key, spec_paths in spec_canon.items():
        app_paths = app_canon.get(key)
        if not app_paths:
            continue
        if spec_paths == app_paths:
            continue
        canonical_mismatches.append((key[0], key[1], spec_paths, app_paths))

    if canonical_mismatches:
        print(
            "\nParameter/placeholder mismatches (same canonical shape, different names):"  # noqa: E501
        )
        for method, canon, spec_paths, app_paths in canonical_mismatches:
            print(f"- {method} {canon}")
            print(f"    spec paths: {sorted(spec_paths)}")
            print(f"    app paths:  {sorted(app_paths)}")
    else:
        print("\nNo placeholder naming mismatches detected.")

    return 1 if missing or extra or canonical_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
