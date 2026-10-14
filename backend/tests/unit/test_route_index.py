"""The route-index collector and first-match guard (v6 reading-boundary
pilot): the counter-example set — real shadows are provable, parameterized
later routes are witnesses, frozen pairs cite exact registrations, and
protocols never cross. Real-app inventory assertions run under the fixed
profile in the existing API-addressing contract suite. The index is never
committed, so nothing here reads or writes one.
"""

from fastapi import APIRouter, FastAPI

from scripts.route_index import (
    RouteRecord,
    _full,
    collect,
    first_match_findings,
)


def _app_with(*routes) -> FastAPI:
    app = FastAPI()
    for methods, path, name in routes:
        router = APIRouter()

        async def handler() -> None: ...

        handler.__name__ = name
        router.api_route(path, methods=methods or ["GET"])(handler)
        app.include_router(router)
    return app


def test_a_literal_later_route_is_provably_unreachable():
    app = _app_with(
        (["GET"], "/users/{user_id}", "get_user"),
        (["GET"], "/users/invite-codes", "list_invite_codes"),
    )
    findings = first_match_findings(collect(app))
    assert [(f.kind, f.later.path) for f in findings] == [
        ("unreachable", "/users/invite-codes")
    ]


def test_a_parameterized_later_route_is_a_witness_not_a_verdict():
    """Earlier covers everything the parameterized later matches — but a
    route with parameters is never declared unreachable, it is witnessed."""
    app = _app_with(
        (["GET"], "/users/{anything}", "get_any"),
        (["GET"], "/users/{user_id}", "get_user"),
    )
    (finding,) = first_match_findings(collect(app))
    assert (finding.kind, finding.method) == ("witness", "GET")
    assert _full(finding.earlier, finding.witness, "http", "GET")
    assert _full(finding.later, finding.witness, "http", "GET")


def test_a_literal_earlier_route_yields_only_a_partial_overlap_witness():
    """The literal wins its URL; the parameterized route still takes Alice."""
    app = _app_with(
        (["GET"], "/users/invite-codes", "list_invite_codes"),
        (["GET"], "/users/{user_id}", "get_user"),
    )
    (finding,) = first_match_findings(collect(app))
    assert (finding.kind, finding.witness, finding.method) == (
        "witness",
        "/users/invite-codes",
        "GET",
    )
    assert _full(finding.earlier, finding.witness, "http", "GET")
    assert _full(finding.later, finding.witness, "http", "GET")
    assert not _full(finding.earlier, "/users/alice", "http", "GET")
    assert _full(finding.later, "/users/alice", "http", "GET")


def test_different_methods_and_protocols_never_shadow():
    app = _app_with(
        (["GET"], "/users/{user_id}", "get_user"),
        (["DELETE"], "/users/invite-codes", "delete_invite_codes"),
        (["POST"], "/users/invite-codes", "create_invite_code"),
    )
    assert first_match_findings(collect(app)) == []


def test_a_frozen_tuple_suppresses_exactly_itself():
    from scripts.route_index_frozen import FROZEN

    (pair,) = FROZEN
    key = (
        pair.earlier_path,
        pair.earlier_endpoint,
        pair.later_path,
        pair.later_endpoint,
    )
    assert key == (
        "/users/{userId}",
        "app.api.routes.users.account.get_user",
        "/users/invite-codes",
        "app.api.routes.users.invite_codes.list_invite_codes",
    ), "the frozen tuple cites the concrete registration records"
    wrong = (pair.earlier_path, pair.earlier_endpoint, pair.later_path, "other")
    assert wrong not in {
        (f.earlier_path, f.earlier_endpoint, f.later_path, f.later_endpoint)
        for f in FROZEN
    }, "a near-miss is not exempt"


def test_records_keep_their_methods_for_the_http_intersection():
    record = RouteRecord(
        index=0,
        protocol="http",
        path="/users/{userId}",
        methods=frozenset({"GET"}),
        endpoint="app.api.routes.users.account.get_user",
        name="get_user",
    )
    assert not record.all_literal
    assert record.segments == ("users", "{userId}")
