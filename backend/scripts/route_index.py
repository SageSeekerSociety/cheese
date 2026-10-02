#!/usr/bin/env python3
"""Effective route index and first-FULL guard (v6 reading-boundary pilot).

Literal later routes have a unique URL: an earlier FULL match proves that
URL unreachable for the reported method. Parameterized routes only yield
verified concrete witnesses; sampling is not a proof of disjointness or
whole-branch coverage. Mounts remain unsupported and fail loudly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import islice, product, zip_longest
from pathlib import Path

from starlette.routing import Match, Mount, Route, WebSocketRoute, compile_path

sys.path.insert(0, str(Path.cwd()))

_PARAM = re.compile(r"{([a-zA-Z_][a-zA-Z0-9_]*)(?::[a-zA-Z_][a-zA-Z0-9_]*)?}")
_SAMPLES = ("x", "1", "1.5", "00000000-0000-0000-0000-000000000001", "", "a/b")


@dataclass(frozen=True)
class RouteRecord:
    """One effective leaf; its global index is never reset per protocol."""

    index: int
    protocol: str
    path: str
    methods: frozenset[str]
    endpoint: str
    name: str
    path_regex: re.Pattern | None = field(default=None, compare=False, repr=False)
    param_convertors: dict = field(default_factory=dict, compare=False, repr=False)
    matcher: Callable | None = field(default=None, compare=False, repr=False)

    def __post_init__(self):
        # Hand-built records use the same compiler as Starlette. Collected
        # records always carry the effective regex and matcher, not a recompile.
        if self.path_regex is None:
            regex, _, convertors = compile_path(self.path)
            object.__setattr__(self, "path_regex", regex)
            object.__setattr__(self, "param_convertors", convertors)

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(seg for seg in self.path.split("/") if seg)

    @property
    def all_literal(self) -> bool:
        return not self.param_convertors


def collect(app) -> list[RouteRecord]:
    """Use FastAPI's effective leaves, including prefixed Starlette/WS routes."""
    records: list[RouteRecord] = []

    def append(original, effective):
        if isinstance(original, Mount):
            raise RuntimeError(f"Mount at {effective.path} is not expanded")
        if not isinstance(original, (Route, WebSocketRoute)):
            raise RuntimeError(f"Unsupported route: {type(original).__name__}")
        if effective.path_regex is None:
            raise RuntimeError(f"Missing effective regex for {effective.path}")
        endpoint = effective.endpoint
        records.append(
            RouteRecord(
                index=len(records),
                protocol="ws" if isinstance(original, WebSocketRoute) else "http",
                path=effective.path,
                methods=frozenset(getattr(effective, "methods", None) or ()),
                endpoint=f"{endpoint.__module__}.{endpoint.__name__}",
                name=effective.name,
                path_regex=effective.path_regex,
                param_convertors=effective.param_convertors,
                matcher=effective.matches,
            )
        )

    for route in app.routes:
        if type(route).__name__ == "_IncludedRouter":
            for ctx in route.effective_route_contexts():
                append(ctx.original_route, ctx.starlette_route or ctx)
        else:
            append(route, route)
    return records


def _full(record: RouteRecord, path: str, protocol: str, method: str) -> bool:
    if record.protocol != protocol:
        return False
    if protocol == "http" and method not in record.methods:
        return False
    match = record.path_regex.fullmatch(path)
    if match is None:
        return False
    if record.matcher is not None:
        scope = {
            "type": "websocket" if protocol == "ws" else "http",
            "method": method,
            "path": path,
            "root_path": "",
        }
        return record.matcher(scope)[0] == Match.FULL
    # Synthetic records still validate the convertor, not only the regex.
    for name, value in match.groupdict().items():
        record.param_convertors[name].convert(value)
    return True


def _first_full(records, path, protocol, method):
    return next((r for r in records if _full(r, path, protocol, method)), None)


def _sample_paths(route: RouteRecord, peer: RouteRecord) -> Iterator[str]:
    """Bounded witness search, never used to declare absence of overlap.

    Peer literals seed cross-segment overlaps; peer suffixes seed :path.
    Every candidate must subsequently pass both effective matchers. Unknown
    custom convertors can yield no sample; that is not a disjoint verdict.
    """
    params = list(_PARAM.finditer(route.path))
    if not params:
        yield route.path
        return
    peer_parts = peer.path.split("/")
    peer_literals = [part for part in peer_parts if not _PARAM.search(part)]
    peer_sample = peer.path
    for param in _PARAM.finditer(peer.path):
        convertor = peer.param_convertors[param[1]]
        sample = next((s for s in _SAMPLES if re.fullmatch(convertor.regex, s)), None)
        if sample is None:
            break
        peer_sample = peer_sample.replace(param[0], sample)
    suffixes = ["/".join(peer_sample.split("/")[i:]) for i in range(1, len(peer_parts))]
    choices = []
    for param in params:
        convertor = route.param_convertors[param[1]]
        position = route.path[: param.start()].count("/")
        aligned = peer_parts[position : position + 1]
        seeds = dict.fromkeys([*aligned, *peer_literals, *_SAMPLES, *suffixes])
        values = [
            s
            for s in seeds
            if not _PARAM.search(s) and re.fullmatch(convertor.regex, s)
        ]
        choices.append(values)
    for values in islice(product(*choices), 256):
        replacements = dict(zip((p[1] for p in params), values, strict=True))
        yield _PARAM.sub(
            lambda m, replacements=replacements: replacements[m[1]], route.path
        )


@dataclass(frozen=True)
class Shadow:
    earlier: RouteRecord
    later: RouteRecord
    kind: str
    witness: str = ""
    method: str = ""


def first_match_findings(records: list[RouteRecord]) -> list[Shadow]:
    """Report actual first FULL winners per URL and protocol/method."""
    findings: list[Shadow] = []
    seen = set()
    for later_pos, later in enumerate(records):
        earlier_records = records[:later_pos]
        for method in sorted(later.methods) if later.protocol == "http" else ["WS"]:
            for peer in earlier_records:
                if peer.protocol != later.protocol:
                    continue
                if later.protocol == "http" and method not in peer.methods:
                    continue
                paths = (
                    [later.path] if later.all_literal else _sample_paths(later, peer)
                )
                for path in paths:
                    if not _full(later, path, later.protocol, method):
                        continue
                    if not _full(peer, path, later.protocol, method):
                        continue
                    winner = _first_full(earlier_records, path, later.protocol, method)
                    key = (winner.index, later.index, method)
                    if key not in seen:
                        seen.add(key)
                        findings.append(
                            Shadow(
                                winner,
                                later,
                                "unreachable" if later.all_literal else "witness",
                                path,
                                method,
                            )
                        )
                    # One verified sample per candidate pair is enough. Other
                    # peers can expose a different first winner on another URL.
                    break
    return findings


def checked_findings(records, frozen):
    """Validate exact frozen registrations before subtracting any findings.

    Used by --check and intended for the real-index assertion as well.
    A stale entry fails even when no currently reported shadow remains.
    """
    findings = first_match_findings(records)
    exempt = set()
    for pair in frozen:

        def stale(pair=pair):
            raise ValueError(f"stale frozen pair: {pair}")

        earlier = [r for r in records if r.index == pair.earlier_index]
        later = [r for r in records if r.index == pair.later_index]
        if len(earlier) != 1 or len(later) != 1:
            stale()
        earlier, later = earlier[0], later[0]
        if (
            (earlier.path, earlier.endpoint)
            != (pair.earlier_path, pair.earlier_endpoint)
            or (later.path, later.endpoint) != (pair.later_path, pair.later_endpoint)
            or earlier.protocol != pair.protocol
            or later.protocol != pair.protocol
            or earlier.index >= later.index
            or records.index(earlier) >= records.index(later)
            or not pair.why.strip()
            or (pair.protocol == "ws" and pair.method != "WS")
        ):
            stale()
        if not _full(later, pair.witness, pair.protocol, pair.method):
            stale()
        winner = _first_full(records, pair.witness, pair.protocol, pair.method)
        if winner is not earlier:
            stale()
        key = (earlier.index, later.index, pair.protocol, pair.method)
        if key in exempt:
            stale()
        exempt.add(key)
    return [
        f
        for f in findings
        if (f.earlier.index, f.later.index, f.later.protocol, f.method) not in exempt
    ]


def parse_snapshot(text: str) -> tuple[dict, list[dict]]:
    """Read the existing JSONL index, validating its profile before any rows.

    The first line is metadata; effective leaf rows keep their original shape,
    order and multiplicity. Extra handwritten row columns remain untouched.
    """
    from scripts.route_index_profile import profile_header

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("route index settings profile header missing")
    header = json.loads(lines[0])
    if not isinstance(header, dict) or "settings_profile" not in header:
        raise ValueError("route index settings profile header missing")
    if header["settings_profile"] != profile_header():
        raise ValueError("route index settings profile mismatch")
    rows = [json.loads(line) for line in lines[1:]]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("route index rows must be JSON objects")
    return header, rows


def _sync(
    index_file: str, records: list[RouteRecord], annotations_file: str | Path | None = None
) -> int:
    """One drift check: profile, ordered leaf rows, complete handwritten notes."""
    path = Path(index_file)
    if not path.exists():
        print(f"route index missing: {index_file}")
        return 1
    try:
        _, indexed = parse_snapshot(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"route index invalid: {exc}")
        return 1
    live = [
        {
            "index": r.index,
            "protocol": r.protocol,
            "path": r.path,
            "methods": sorted(r.methods),
            "endpoint": r.endpoint,
        }
        for r in records
    ]
    columns = ("index", "protocol", "path", "methods", "endpoint")
    generated = [{key: row.get(key) for key in columns} for row in indexed]
    if generated != live:
        for position, (old, new) in enumerate(zip_longest(generated, live)):
            if old != new:
                print(f"[row {position}] indexed={old!r} live={new!r}")
        print(f"route index out of sync: {len(indexed)} indexed / {len(live)} live")
        return 1
    from scripts.route_index_annotations import (
        DEFAULT_SIDECAR,
        check_annotation_coverage,
    )

    try:
        keys = check_annotation_coverage(indexed, annotations_file or DEFAULT_SIDECAR)
    except ValueError as exc:
        print(f"route annotations invalid: {exc}")
        return 1
    print(
        f"route index in sync ({len(indexed)} registrations; {keys} annotated addresses)"
    )
    return 0


def main() -> int:
    from scripts.route_index_profile import (
        in_profile_worker,
        profile_header,
        run_profiled,
    )

    # Application imports (including Settings/.env and engines) stay in the
    # isolated worker, for emission and every live inventory assertion alike.
    if not in_profile_worker():
        result = run_profiled(sys.argv[1:])
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emit",
        action="store_true",
        help="print the profile header and index as JSON lines",
    )
    parser.add_argument(
        "--check", action="store_true", help="run the first-match guard"
    )
    parser.add_argument(
        "--sync", metavar="FILE", help="verify ordered index and handwritten notes"
    )
    parser.add_argument(
        "--annotations",
        metavar="FILE",
        help="override the route annotation sidecar for --sync",
    )
    args = parser.parse_args()

    from app.main import app

    records = collect(app)
    if args.emit:
        print(json.dumps({"settings_profile": profile_header()}, ensure_ascii=False))
        for record in records:
            print(
                json.dumps(
                    {
                        "index": record.index,
                        "protocol": record.protocol,
                        "path": record.path,
                        "methods": sorted(record.methods),
                        "endpoint": record.endpoint,
                        "name": record.name,
                    },
                    ensure_ascii=False,
                )
            )
        return 0
    if args.check:
        from scripts.route_index_frozen import FROZEN

        try:
            findings = checked_findings(records, FROZEN)
        except ValueError as exc:
            print(exc)
            return 1
        for finding in findings:
            print(
                f"[{finding.kind}] {finding.later.protocol}/{finding.method} "
                f"#{finding.earlier.index} {finding.earlier.path} "
                f"({finding.earlier.endpoint}) shadows "
                f"#{finding.later.index} {finding.later.path} "
                f"({finding.later.endpoint}) — witness {finding.witness}"
            )
        bad = [f for f in findings if f.kind == "unreachable"]
        if bad:
            print(f"first-match guard: {len(bad)} provable shadow(s)")
            return 1
        print(f"first-match guard: clean ({len(findings)} witness-only pairs)")
        return 0
    if args.sync:
        return _sync(args.sync, records, args.annotations)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
