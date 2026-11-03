"""A response's Server-Timing names the database's share of it: how long its
statements ran and how many there were (`core/query_watch.py`). A request that
reads rows says so; the count is not a constant the header happens to carry."""

import re

from app.core.query_watch import watch_engine
from tests.integration.conftest import post_project, session_auth_headers


def _db(response) -> tuple[float, int]:
    for entry in response.headers.get_list("server-timing"):
        found = re.fullmatch(r'db;dur=(\d+(?:\.\d+)?);desc="(\d+) sql"', entry)
        if found:
            return float(found.group(1)), int(found.group(2))
    raise AssertionError(response.headers.get_list("server-timing"))


def test_a_request_that_reads_rows_says_how_many_and_how_long(client):
    # The harness serves requests on an engine of its own; the app watches
    # `core.db`'s, which is the one that serves them outside tests.
    watch_engine(client.test_app_engine)
    post_project(client, json={"name": "Timed"}, owner="alice")
    listed = client.get("/projects", headers=session_auth_headers("alice"))
    assert listed.status_code == 200, listed.text
    duration, statements = _db(listed)
    assert statements > 0
    assert duration > 0
    assert any(
        re.fullmatch(r"app;dur=\d+(\.\d+)?", entry)
        for entry in listed.headers.get_list("server-timing")
    )


def test_the_slowest_statements_are_named_by_where_they_were_sent(client):
    """Beside the total, each of the few slowest statements is named by the
    file:line that sent it, so a slow page says which read to look at. No SQL
    text goes out."""
    watch_engine(client.test_app_engine)
    post_project(client, json={"name": "Timed"}, owner="alice")
    listed = client.get("/projects", headers=session_auth_headers("alice"))
    slowest = [
        re.fullmatch(r'sql;dur=(\d+(?:\.\d+)?);desc="([^"]+)"', entry)
        for entry in listed.headers.get_list("server-timing")
        if entry.startswith("sql;")
    ]
    assert 1 <= len(slowest) <= 3, listed.headers.get_list("server-timing")
    assert all(found is not None for found in slowest)
    durations = [float(found.group(1)) for found in slowest]
    assert durations == sorted(durations, reverse=True)
    assert all(re.search(r"\.py:\d+", found.group(2)) for found in slowest)
    assert not any("select" in found.group(2).lower() for found in slowest)
