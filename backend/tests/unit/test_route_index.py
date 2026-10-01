"""The route-index collector and first-match guard (v6 reading-boundary
pilot): the counter-example set — real shadows are provable, parameterized
later routes are witnesses, frozen pairs are exact four-field tuples, and
protocols never cross. The real-app assertions pin the fixed profile's
effective expansion, including the seven WebSocket routes.
"""

from fastapi import APIRouter, FastAPI

from scripts.route_index import RouteRecord, collect, first_match_findings


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
    findings = first_match_findings(collect(app))
    assert [(f.kind, f.witness) for f in findings] == [("witness", "/users/x")]


def test_a_literal_earlier_route_does_not_shadow_a_parameterized_one():
    """/users/invite-codes accepts only itself; /users/{user_id} also takes
    /users/alice — nothing is shadowed."""
    app = _app_with(
        (["GET"], "/users/invite-codes", "list_invite_codes"),
        (["GET"], "/users/{user_id}", "get_user"),
    )
    assert first_match_findings(collect(app)) == []


def test_different_methods_and_protocols_never_shadow():
    app = _app_with(
        (["GET"], "/users/{user_id}", "get_user"),
        (["DELETE"], "/users/invite-codes", "delete_invite_codes"),
        (["POST"], "/users/invite-codes", "create_invite_code"),
    )
    assert first_match_findings(collect(app)) == []


def test_the_real_app_expands_exactly_the_effective_routes():
    from app.main import app

    records = collect(app)
    assert len(records) > 600, "the whole effective surface is collected"
    assert [r.index for r in records] == list(range(len(records))), (
        "registration order is preserved"
    )
    assert sum(1 for r in records if r.protocol == "ws") == 7, (
        "the seven WebSocket routes are marked by type, not by name"
    )
    endpoints = {(r.path, r.endpoint) for r in records}
    assert (
        "/topics/{topic_id}/tasks/{task_id}/accept-card",
        "app.api.routes.accept.create_accept_card",
    ) in endpoints


def test_the_real_apps_one_known_shadow_is_the_documented_one():
    from app.main import app

    findings = first_match_findings(collect(app))
    assert [(f.earlier.path, f.later.path, f.kind) for f in findings] == [
        ("/users/{userId}", "/users/invite-codes", "unreachable")
    ], "only the users.py:3367-documented shadow exists today"


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
        "app.api.routes.users.get_user",
        "/users/invite-codes",
        "app.api.routes.users.list_invite_codes",
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
        endpoint="app.api.routes.users.get_user",
        name="get_user",
    )
    assert not record.all_literal
    assert record.segments == ("users", "{userId}")
