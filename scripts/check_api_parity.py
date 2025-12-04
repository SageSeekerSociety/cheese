#!/usr/bin/env python3
"""Compare FastAPI routes against the legacy OpenAPI catalog.

Outputs:
- missing endpoints (by method + path)
- extra endpoints (registered in FastAPI but not present in spec)
- parameter-name mismatches (same canonical path shape but different placeholder names)
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from app.main import app

CATALOG_PATH = Path(__file__).resolve().parents[1] / "tests" / "contract" / "api_catalog.json"


def canonicalize(path: str) -> str:
    """Replace placeholder names with '{}' to compare templates ignoring parameter casing."""
    return re.sub(r"\{[^{}]+\}", "{}", path)


def load_spec() -> set[tuple[str, str]]:
    payload = json.loads(CATALOG_PATH.read_text())
    return {(item["method"], item["path"]) for item in payload}


def collect_app_routes() -> set[tuple[str, str]]:
    entries: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        for method in methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            entries.add((method, route.path))
    return entries


def group_by_canonical(entries: Iterable[tuple[str, str]]):
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for method, path in entries:
        grouped[(method, canonicalize(path))].add(path)
    return grouped


def main() -> None:
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
        print("\nParameter/placeholder mismatches (same canonical shape, different names):")
        for method, canon, spec_paths, app_paths in canonical_mismatches:
            print(f"- {method} {canon}")
            print(f"    spec paths: {sorted(spec_paths)}")
            print(f"    app paths:  {sorted(app_paths)}")
    else:
        print("\nNo placeholder naming mismatches detected.")


if __name__ == "__main__":
    main()
