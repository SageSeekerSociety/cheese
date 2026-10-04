"""New b9 review regressions; no app.main/settings/database imports."""

import json
import re
from dataclasses import replace

import pytest
from fastapi import APIRouter, FastAPI
from starlette.convertors import Convertor, register_url_convertor
from starlette.routing import Mount, Route

from scripts.route_index import (
    RouteRecord,
    _full,
    _sync,
    checked_findings,
    collect,
    first_match_findings,
)
from scripts.route_index_frozen import FROZEN, FrozenPair
from scripts.route_index_profile import profile_header


def record(index, path, methods=("GET",), protocol="http"):
    return RouteRecord(
        index, protocol, path, frozenset(methods), f"m.h{index}", f"h{index}"
    )


def write_index(tmp_path, records):
    path = tmp_path / "index.json"
    path.write_text(
        json.dumps({"settings_profile": profile_header()})
        + "\n"
        + "\n".join(
            json.dumps(
                {
                    "index": r.index,
                    "protocol": r.protocol,
                    "path": r.path,
                    "methods": sorted(r.methods),
                    "endpoint": r.endpoint,
                    "auth": "reviewed outside this slice",
                    "owner": "test",
                }
            )
            for r in records
        ),
        encoding="utf-8",
    )
    write_annotations(tmp_path, records)
    return str(path)


def write_annotations(tmp_path, records):
    keys = {
        (r.protocol, method, r.path)
        for r in records
        for method in (sorted(r.methods) if r.protocol == "http" else ["WS"])
    }
    data = {
        "version": 1,
        "auth_entries": {"fixture": "Synthetic fixture authorization"},
        "owners": {"fixture": "Synthetic fixture owner"},
        "routes": [
            {
                "protocol": p,
                "method": m,
                "path": value,
                "auth": "fixture",
                "owner": "fixture",
            }
            for p, m, value in sorted(keys)
        ],
    }
    path = tmp_path / "sidecar.yaml"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path, data


@pytest.mark.parametrize("header_kind", ["missing", "wrong_profile"])
def test_sync_rejects_profile_before_decoding_route_rows(tmp_path, capsys, header_kind):
    path = tmp_path / "index.json"
    if header_kind == "missing":
        header = {"index": 0, "path": "/old-unprofiled-index"}
        expected = "settings profile header missing"
    else:
        header = {"settings_profile": {**profile_header(), "name": "another-profile"}}
        expected = "settings profile mismatch"
    # Bad row JSON must not mask the first, actionable profile error.
    path.write_text(json.dumps(header) + "\nnot-json", encoding="utf-8")
    assert _sync(str(path), [record(0, "/a")]) == 1
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize(
    "mutation", ["swap", "index", "add_duplicate", "drop_duplicate"]
)
def test_sync_preserves_order_leaf_and_multiplicity(tmp_path, mutation):
    a, b = record(0, "/a"), record(1, "/b")
    stored, live = [a, b], [a, b]
    if mutation == "swap":
        live = [replace(b, index=0), replace(a, index=1)]
    elif mutation == "index":
        live = [replace(a, index=5), b]
    elif mutation == "add_duplicate":
        live = [a, b, replace(a, index=2)]
    else:
        stored = [a, b, a]
    assert _sync(write_index(tmp_path, stored), live) == 1


def test_sync_preserves_matching_duplicate_registrations(tmp_path):
    records = [record(0, "/same"), replace(record(0, "/same"), index=1)]
    assert (
        _sync(write_index(tmp_path, records), records, tmp_path / "sidecar.yaml") == 0
    )


def test_annotations_cover_every_http_method_and_explicit_ws(tmp_path):
    from scripts.route_index_annotations import check_annotation_coverage

    records = [record(0, "/same", ("GET", "HEAD")), record(1, "/same", (), "ws")]
    path, data = write_annotations(tmp_path, records)
    rows = [
        {"protocol": r.protocol, "methods": sorted(r.methods), "path": r.path}
        for r in records
    ]
    assert check_annotation_coverage(rows, path) == 3
    data["routes"] = [r for r in data["routes"] if r["method"] != "HEAD"]
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="missing=.*HEAD"):
        check_annotation_coverage(rows, path)


@pytest.mark.parametrize(
    "mutation",
    [
        "orphan",
        "duplicate",
        "ws_method",
        "http_ws",
        "unknown_auth",
        "unknown_owner",
        "empty_auth_note",
        "empty_owner_note",
    ],
)
def test_annotation_format_and_coverage_fail_loudly(tmp_path, mutation):
    from scripts.route_index_annotations import check_annotation_coverage

    records = [record(0, "/http"), record(1, "/socket", (), "ws")]
    path, data = write_annotations(tmp_path, records)
    rows = [
        {"protocol": r.protocol, "methods": sorted(r.methods), "path": r.path}
        for r in records
    ]
    if mutation == "orphan":
        data["routes"].append({**data["routes"][0], "path": "/removed"})
        expected = "orphan=.*removed"
    elif mutation == "duplicate":
        data["routes"].append(data["routes"][0])
        expected = "duplicate route annotation"
    elif mutation == "ws_method":
        data["routes"][1]["method"] = "GET"
        expected = "require method WS"
    elif mutation == "http_ws":
        data["routes"][0]["method"] = "WS"
        expected = "HTTP.*method invalid"
    elif mutation.startswith("unknown_"):
        field = mutation.removeprefix("unknown_")
        data["routes"][0][field] = "not-registered"
        expected = "unknown route"
    else:
        catalog = "auth_entries" if mutation == "empty_auth_note" else "owners"
        data[catalog]["fixture"] = "   "
        expected = "nonempty string"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        check_annotation_coverage(rows, path)


def test_annotations_reject_duplicate_yaml_keys_and_unreadable_files(tmp_path):
    from scripts.route_index_annotations import read_annotations

    path = tmp_path / "sidecar.yaml"
    with pytest.raises(ValueError, match="unreadable"):
        read_annotations(path)
    path.write_text("version: 1\nversion: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate YAML mapping key"):
        read_annotations(path)


def test_sync_gates_annotations_after_ordered_inventory(tmp_path, capsys):
    records = [record(0, "/same"), replace(record(0, "/same"), index=1)]
    index = write_index(tmp_path, records)
    sidecar, data = write_annotations(tmp_path, records)
    assert _sync(index, records, sidecar) == 0
    data["routes"] = []
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    assert _sync(index, records, sidecar) == 1
    assert "route annotations coverage mismatch" in capsys.readouterr().out


@pytest.mark.parametrize("convertor", ["int", "uuid", "float"])
def test_constrained_earlier_does_not_swallow_invalid_literal(convertor):
    assert (
        first_match_findings(
            [
                record(0, f"/x/{{id:{convertor}}}"),
                record(1, "/x/invite-codes"),
            ]
        )
        == []
    )


@pytest.mark.parametrize("later", ["/x/a/b", "/x/"])
def test_path_convertor_covers_multiple_or_empty_segments(later):
    findings = first_match_findings([record(0, "/x/{rest:path}"), record(1, later)])
    assert [(f.kind, f.witness) for f in findings] == [("unreachable", later)]


@pytest.mark.parametrize("later", ["/x/{id:int}", "/x/{id:uuid}", "/x/{id:float}"])
def test_witness_is_legal_for_both_convertors(later):
    records = [record(0, "/x/{anything}"), record(1, later)]
    findings = first_match_findings(records)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "witness"
    assert all(_full(r, f.witness, "http", "GET") for r in records)


def test_partial_overlap_has_legal_witness_and_reachable_sibling():
    records = [record(0, "/a/{x}/c"), record(1, "/a/b/{y}")]
    findings = first_match_findings(records)
    assert [(f.kind, f.witness) for f in findings] == [("witness", "/a/b/c")]
    assert not _full(records[0], "/a/b/d", "http", "GET")
    assert _full(records[1], "/a/b/d", "http", "GET")


def test_literal_before_parameter_yields_witness_not_unreachable():
    findings = first_match_findings([record(0, "/x/z"), record(1, "/x/{id}")])
    assert [(f.kind, f.witness) for f in findings] == [("witness", "/x/z")]


def test_embedded_parameter_is_not_a_literal_branch():
    findings = first_match_findings(
        [
            record(0, "/files/{rest:path}"),
            record(1, "/files/{name}.json"),
        ]
    )
    assert findings and all(f.kind == "witness" for f in findings)


def test_first_full_winner_is_unique_per_method_and_skips_partial():
    records = [
        record(0, "/x/{a}", ("DELETE",)),
        record(1, "/x/{b}", ("GET",)),
        record(2, "/x/{c}", ("GET", "POST")),
        record(3, "/x/z", ("GET", "POST")),
    ]
    findings = [f for f in first_match_findings(records) if f.later.index == 3]
    assert [(f.earlier.index, f.method) for f in findings] == [(1, "GET"), (2, "POST")]


def test_collector_keeps_effective_prefix_regex_and_actual_head_methods():
    async def handler(): ...

    router = APIRouter()
    router.add_api_route("/{id:int}", handler, methods=["GET"])
    router.add_api_websocket_route("/socket/{id:int}", handler)
    router.routes.append(Route("/plain", handler, methods=["GET"]))
    app = FastAPI()
    app.include_router(router, prefix="/prefix")
    wrapper = app.routes[-1]
    contexts = list(wrapper.effective_route_contexts())
    records = [r for r in collect(app) if r.path.startswith("/prefix")]
    assert len(records) == 3
    for r, ctx in zip(records, contexts, strict=True):
        effective = ctx.starlette_route or ctx
        assert r.path_regex is effective.path_regex
    api, ws, plain = records
    assert api.methods == {"GET"} and plain.methods == {"GET", "HEAD"}
    assert _full(api, "/prefix/1", "http", "GET")
    assert not _full(api, "/prefix/1", "http", "HEAD")
    assert _full(ws, "/prefix/socket/1", "ws", "WS")
    assert not _full(ws, "/socket/1", "ws", "WS")
    assert not _full(ws, "/prefix/socket/1", "http", "GET")


def test_nested_mount_fails_loudly():
    router = APIRouter()
    router.routes.append(Mount("/mount", app=FastAPI()))
    app = FastAPI()
    app.include_router(router, prefix="/prefix")
    with pytest.raises(RuntimeError, match="Mount"):
        collect(app)


def test_ws_first_winner_does_not_require_methods():
    records = [record(0, "/x/{p}", (), "ws"), record(1, "/x/z", (), "ws")]
    findings = first_match_findings(records)
    assert [(f.earlier.index, f.method, f.kind) for f in findings] == [
        (0, "WS", "unreachable")
    ]


def test_actual_effective_regex_overrides_template_approximation():
    records = [
        replace(record(0, "/x/{id}"), path_regex=re.compile(r"^/x/(?P<id>[0-9]+)$")),
        record(1, "/x/invite-codes"),
    ]
    assert first_match_findings(records) == []


def frozen_fixture():
    records = [record(0, "/x/{id}"), record(1, "/x/z")]
    pair = FrozenPair(
        earlier_index=0,
        later_index=1,
        protocol="http",
        method="GET",
        earlier_path=records[0].path,
        earlier_endpoint=records[0].endpoint,
        later_path=records[1].path,
        later_endpoint=records[1].endpoint,
        witness="/x/z",
        why="Test exact structured exception",
    )
    return records, pair


def test_frozen_exact_pair_is_suppressed():
    records, pair = frozen_fixture()
    assert checked_findings(records, (pair,)) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_earlier",
        "missing_later",
        "reverse",
        "leaf",
        "handler",
        "path",
        "protocol",
        "method",
        "regex",
        "witness",
        "winner",
        "duplicate_leaf",
        "duplicate_pair",
    ],
)
def test_frozen_stale_identity_or_overlap_fails(mutation):
    records, pair = frozen_fixture()
    frozen = (pair,)
    if mutation == "missing_earlier":
        records = records[1:]
    elif mutation == "missing_later":
        records = records[:1]
    elif mutation == "reverse":
        records.reverse()
    elif mutation == "leaf":
        records[0] = replace(records[0], index=20)
    elif mutation == "handler":
        records[0] = replace(records[0], endpoint="other.handler")
    elif mutation == "path":
        records[0] = replace(records[0], path="/x/{other}")
    elif mutation == "protocol":
        records[0] = replace(records[0], protocol="ws")
    elif mutation == "method":
        records[0] = replace(records[0], methods=frozenset({"DELETE"}))
    elif mutation == "regex":
        records[0] = replace(records[0], path_regex=re.compile(r"^/x/[0-9]+$"))
    elif mutation == "witness":
        frozen = (replace(pair, witness="/elsewhere"),)
    elif mutation == "winner":
        records.insert(0, record(-1, "/x/{new}"))
    elif mutation == "duplicate_leaf":
        records.insert(0, records[0])
    else:
        frozen = (pair, pair)
    with pytest.raises(ValueError, match="stale frozen"):
        checked_findings(records, frozen)


def test_frozen_does_not_exempt_other_method_or_new_registration():
    records, pair = frozen_fixture()
    records[0] = replace(records[0], methods=frozenset({"GET", "POST"}))
    records[1] = replace(records[1], methods=frozenset({"GET", "POST"}))
    records.append(record(2, "/x/new"))
    findings = checked_findings(records, (pair,))
    assert [(f.later.index, f.method) for f in findings] == [(1, "POST"), (2, "GET")]


def test_existing_frozen_debt_keeps_exact_b9_leaves():
    (pair,) = FROZEN
    assert (pair.earlier_index, pair.later_index, pair.protocol, pair.method) == (
        648,
        672,
        "http",
        "GET",
    )
    records = [
        replace(record(648, pair.earlier_path), endpoint=pair.earlier_endpoint),
        replace(record(672, pair.later_path), endpoint=pair.later_endpoint),
    ]
    assert checked_findings(records, FROZEN) == []


def test_unknown_custom_convertor_never_emits_invalid_witness(monkeypatch):
    from starlette.convertors import CONVERTOR_TYPES

    class Code(Convertor):
        regex = "Q[0-9]{3}"

        def convert(self, value):
            return value

    monkeypatch.setitem(CONVERTOR_TYPES, "guard_code", Code())
    # Register with the framework, not a separate guard regex registry.
    register_url_convertor("guard_code", CONVERTOR_TYPES["guard_code"])
    records = [record(0, "/x/Q123"), record(1, "/x/{id:guard_code}")]
    findings = first_match_findings(records)
    assert [(f.kind, f.witness) for f in findings] == [("witness", "/x/Q123")]
