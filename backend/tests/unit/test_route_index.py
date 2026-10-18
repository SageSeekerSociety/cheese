"""The route-index collector and first-match guard (v6 reading-boundary
pilot): the counter-example set — real shadows are provable, parameterized
later routes are witnesses, and protocols never cross. Real-app inventory
assertions run under the fixed profile in the existing API-addressing contract
suite. The index is never committed, so nothing here reads or writes one.
"""

from fastapi import APIRouter, FastAPI

from scripts.route_index import (
    RouteRecord,
    _full,
    collect,
    first_match_findings,
    print_sections,
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


def test_each_section_of_a_merged_run_carries_its_own_status(capsys):
    """The marker is the contract: the caller reads a section's status off it.

    One child answers all three questions, so a caller that wants one of them
    must be able to see that answer go red without the other two turning red
    with it. Written out literally — the separator is what the addressing
    contract suite parses, so a change to it belongs in this test.
    """
    code = print_sections(
        [
            ("emit", 0, '{"settings_profile": {"name": "p"}}'),
            ("check", 1, "first-match guard: 2 provable shadow(s)"),
        ]
    )

    assert code == 1, "a provable shadow still has to leave the run non-zero"
    assert capsys.readouterr().out == (
        "## route-index section emit status 0\n"
        '{"settings_profile": {"name": "p"}}\n'
        "## route-index section check status 1\n"
        "first-match guard: 2 provable shadow(s)\n"
    )

    assert print_sections([("emit", 0, "{}"), ("check", 0, "")]) == 0
    assert capsys.readouterr().out == (
        "## route-index section emit status 0\n{}\n"
        "## route-index section check status 0\n"
    ), "a section with no output prints its marker and nothing else"
