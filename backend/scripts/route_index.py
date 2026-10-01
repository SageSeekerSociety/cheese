#!/usr/bin/env python3
"""The route index and the first-match guard (v6 reading-boundary pilot).

One collector over the LIVE app's effective routes — FastAPI 0.137's
``_IncludedRouter.effective_route_contexts()`` expands every include to the
routes that actually answer, preserving registration order, path, endpoint
and the protocol each route speaks (the seven WebSocket routes sit at top
level as ``APIWebSocketRoute``; nothing is inferred from names). On top of
that:

- ``--emit`` prints the index, one row per route, in registration order.
- ``--check`` runs the first-match guard: an earlier route that shadows a
  later one is a defect only when it is PROVABLE — the later route is
  all-literal and the earlier one matches everything it could. A later
  route with parameters is never declared unreachable; it is reported with
  one concrete witness path instead. Methods intersect on HTTP only;
  WebSocket pairs match by protocol. Exit 1 when a provable shadow exists.

No settings profile is read and no Mount is expanded here: this app builds
its routers statically (``app.main``), so the effective expansion above IS
the fixed profile's truth; a future Mount fails loudly below rather than
silently dropping routes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Same convention as check_boundaries: the backend root goes on sys.path so
# the script and the tools it wraps judge the same tree from the same place.
sys.path.insert(0, str(Path.cwd()))

_PARAM = re.compile(r"^\{[^}/]+(?::[^}/]+)?\}$")


@dataclass(frozen=True)
class RouteRecord:
    """One effective route, in the app's registration order."""

    index: int
    protocol: str  # "http" | "ws"
    path: str
    methods: frozenset[str]
    endpoint: str
    name: str

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(seg for seg in self.path.split("/") if seg)

    @property
    def all_literal(self) -> bool:
        return not any(_PARAM.match(seg) for seg in self.segments)


def collect(app) -> list[RouteRecord]:
    """Every route that can answer, in registration order (v3 collector)."""
    records: list[RouteRecord] = []
    for route in app.routes:
        kind = type(route).__name__
        if kind == "_IncludedRouter":
            for ctx in route.effective_route_contexts():
                # The seven WS routes surface as contexts whose endpoint is
                # None; the answering callable lives on the original route
                # (v3: protocol comes from the route's own type, never names).
                original = ctx.original_route
                protocol = "ws" if "WebSocket" in type(original).__name__ else "http"
                endpoint = ctx.endpoint or original.endpoint
                methods = ctx.methods or getattr(original, "methods", None) or ()
                path = ctx.path or original.path
                records.append(
                    RouteRecord(
                        index=len(records),
                        protocol=protocol,
                        path=path,
                        methods=frozenset(methods),
                        endpoint=f"{endpoint.__module__}.{endpoint.__name__}",
                        name=ctx.name or original.name,
                    )
                )
        elif kind in ("Route", "APIRoute", "APIWebSocketRoute", "WebSocketRoute"):
            protocol = "ws" if "WebSocket" in kind else "http"
            endpoint = route.endpoint
            records.append(
                RouteRecord(
                    index=len(records),
                    protocol=protocol,
                    path=route.path,
                    methods=frozenset(getattr(route, "methods", None) or ()),
                    endpoint=f"{endpoint.__module__}.{endpoint.__name__}",
                    name=route.name,
                )
            )
        elif kind == "Mount":
            # Fail loud, never silent-drop (v3): this app has none today, and
            # adding one without extending the collector must break the guard.
            raise RuntimeError(
                f"Mount at {getattr(route, 'path', '?')} is not expanded by the "
                "route-index collector"
            )
    return records


def _segments_cover(earlier: tuple[str, ...], later: tuple[str, ...]) -> bool:
    """Every path `later` matches, `earlier` also matches (segment subset)."""
    if len(earlier) != len(later):
        return False
    for first, second in zip(earlier, later, strict=True):
        if _PARAM.match(first):
            continue
        if first != second:
            return False
    return True


@dataclass(frozen=True)
class Shadow:
    """One earlier/later pair the guard judged."""

    earlier: RouteRecord
    later: RouteRecord
    kind: str  # "unreachable" (provable) | "witness"
    witness: str = field(default="")


def first_match_findings(records: list[RouteRecord]) -> list[Shadow]:
    """Earlier routes that swallow later ones (v3 first-match guard).

    A verdict of "unreachable" is provable only for an all-literal later
    route; a parameterized later route yields one concrete witness path and
    is otherwise left to the frozen pair list, which must cite the concrete
    registration records (leaf index + handler).
    """
    findings: list[Shadow] = []
    for later_pos, later in enumerate(records):
        for earlier in records[:later_pos]:
            if earlier.protocol != later.protocol:
                continue
            if later.protocol == "http" and not (earlier.methods & later.methods):
                continue
            if not _segments_cover(earlier.segments, later.segments):
                continue
            if later.all_literal:
                findings.append(Shadow(earlier, later, "unreachable"))
            else:
                witness = "/".join(
                    second if _PARAM.match(second) else "x" for second in later.segments
                )
                # A literal earlier segment wins over a param in the same
                # position for the witness path, so the witness is one the
                # earlier route provably takes.
                witness = "/".join(
                    (
                        second
                        if not _PARAM.match(second)
                        else (first if not _PARAM.match(first) else "x")
                    )
                    for first, second in zip(
                        earlier.segments, later.segments, strict=True
                    )
                )
                findings.append(Shadow(earlier, later, "witness", f"/{witness}"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--emit", action="store_true", help="print the index as JSON lines"
    )
    parser.add_argument(
        "--check", action="store_true", help="run the first-match guard"
    )
    args = parser.parse_args()

    from app.main import app

    records = collect(app)
    if args.emit:
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

        frozen = {
            (f.earlier_path, f.earlier_endpoint, f.later_path, f.later_endpoint)
            for f in FROZEN
        }
        findings = [
            finding
            for finding in first_match_findings(records)
            if (
                finding.earlier.path,
                finding.earlier.endpoint,
                finding.later.path,
                finding.later.endpoint,
            )
            not in frozen
        ]
        bad = [finding for finding in findings if finding.kind == "unreachable"]
        for finding in findings:
            print(
                f"[{finding.kind}] #{finding.earlier.index} "
                f"{sorted(finding.earlier.methods) or ['WS']} "
                f"{finding.earlier.path} ({finding.earlier.endpoint}) shadows "
                f"#{finding.later.index} {finding.later.path} "
                f"({finding.later.endpoint})"
                + (f" — witness {finding.witness}" if finding.witness else "")
            )
        if bad:
            print(f"first-match guard: {len(bad)} provable shadow(s)")
            return 1
        print(f"first-match guard: clean ({len(findings)} witness-only pairs)")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
