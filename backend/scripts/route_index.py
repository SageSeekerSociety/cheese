#!/usr/bin/env python3
"""Effective route index, first-FULL guard and annotation coverage.

    uv run python -m scripts.route_index --emit  # the index as JSON lines
    uv run python -m scripts.route_index --check  # nothing provably shadowed
    uv run python -m scripts.route_index --check-annotations  # every route noted
    uv run python -m scripts.route_index --all  # all three, one child, one import

The three questions above are asked about the same collected inventory, and the
collector cannot run without the application imported — the expensive part of
each run. `--all` answers them in one child and delimits the three answers, so a
caller that wants more than one pays for the import once. Each section carries
the status code that command would have exited with on its own; the process
exits 0 only when all of them did.

The index this emits is NOT committed. It changes with every route, and as a
tracked file 84 commits from 83 PRs touched it in the twelve days it existed; it
is generated where it is judged instead (the frontend `check` job, and the
addressing contract suite). `--check` is the first-FULL guard (v6
reading-boundary pilot): literal later routes have a unique URL, so an earlier
FULL match proves that URL unreachable for the reported method. Parameterized
routes only yield verified concrete witnesses; sampling is not a proof of
disjointness or whole-branch coverage. Mounts remain unsupported and fail loudly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from itertools import islice, product
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


def section_marker(name: str, status: int) -> str:
    """The line that opens one `--all` section, carrying that section's status.

    No payload line can be mistaken for it: the emit section prints JSON
    objects and the other two print findings and summaries, none of which
    start with `##`.
    """
    return f"## route-index section {name} status {status}"


def print_sections(sections: Iterable[tuple[str, int, str]]) -> int:
    """Print each section under its marker; 0 only when every status was 0.

    Kept separate from the judgements it prints so the delimiting itself is
    testable without importing the application.
    """
    worst = 0
    for name, status, text in sections:
        print(section_marker(name, status))
        if text:
            print(text)
        worst = worst or status
    return worst


def _emit(records: list[RouteRecord], header: dict) -> tuple[int, str]:
    """The index as JSON lines, under the profile header it was collected in."""
    lines = [json.dumps({"settings_profile": header}, ensure_ascii=False)]
    lines += [
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
        for record in records
    ]
    return 0, "\n".join(lines)


def _check(records: list[RouteRecord]) -> tuple[int, str]:
    """The first-FULL guard: 1 as soon as one shadow is provable."""
    findings = first_match_findings(records)
    lines = [
        f"[{finding.kind}] {finding.later.protocol}/{finding.method} "
        f"#{finding.earlier.index} {finding.earlier.path} "
        f"({finding.earlier.endpoint}) shadows "
        f"#{finding.later.index} {finding.later.path} "
        f"({finding.later.endpoint}) — witness {finding.witness}"
        for finding in findings
    ]
    bad = [finding for finding in findings if finding.kind == "unreachable"]
    if bad:
        lines.append(f"first-match guard: {len(bad)} provable shadow(s)")
        return 1, "\n".join(lines)
    lines.append(f"first-match guard: clean ({len(findings)} witness-only pairs)")
    return 0, "\n".join(lines)


def _annotations(records: list[RouteRecord], sidecar: str | None) -> tuple[int, str]:
    """Annotation coverage: 1 when the sidecar is missing an address or is bad."""
    from scripts.route_index_annotations import (
        DEFAULT_SIDECAR,
        check_annotation_coverage,
    )

    rows = [
        {
            "protocol": record.protocol,
            "methods": sorted(record.methods),
            "path": record.path,
        }
        for record in records
    ]
    try:
        keys = check_annotation_coverage(rows, sidecar or DEFAULT_SIDECAR)
    except ValueError as exc:
        return 1, f"route annotations invalid: {exc}"
    return 0, (
        f"route annotations complete ({len(records)} registrations; "
        f"{keys} annotated addresses)"
    )


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
    judgement = parser.add_mutually_exclusive_group()
    judgement.add_argument(
        "--emit",
        action="store_true",
        help="print the profile header and index as JSON lines",
    )
    judgement.add_argument(
        "--check", action="store_true", help="run the first-match guard"
    )
    judgement.add_argument(
        "--check-annotations",
        action="store_true",
        help="verify every live route has its handwritten annotation",
    )
    judgement.add_argument(
        "--all",
        action="store_true",
        help="run all three in this process, each under its own status marker",
    )
    parser.add_argument(
        "--annotations",
        metavar="FILE",
        help="override the route annotation sidecar",
    )
    args = parser.parse_args()

    from app.main import app

    records = collect(app)
    if args.all:
        return print_sections(
            [
                ("emit", *_emit(records, profile_header())),
                ("check", *_check(records)),
                ("check-annotations", *_annotations(records, args.annotations)),
            ]
        )
    if args.emit:
        print(_emit(records, profile_header())[1])
        return 0
    if args.check:
        status, text = _check(records)
        print(text)
        return status
    if args.check_annotations:
        status, text = _annotations(records, args.annotations)
        print(text)
        return status
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
