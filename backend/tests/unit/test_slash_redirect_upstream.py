"""Why `redirect_slashes=False` cannot be replaced by configuring `root_path`.

`app.main` builds the app with slash redirects off, because behind a stripping
gateway they cannot be made correct — Starlette's `Location` is origin-absolute
and built from the stripped path, so a browser following it lands on a URL with
no `/api`, which the gateway does not route to the backend.

Setting `root_path` is the obvious repair and it does not work: Starlette
stopped putting root_path back on slash redirects in 0.35.0, and the discussion
asking for it back (starlette#2514) is still open. Rather than trust a
changelog, this measures the version we actually run — and it goes red if
upstream ever fixes it, which is the moment someone should revisit the setting
instead of finding this note years later.

Lives in tests/unit/ because it stands up its own two-route app and needs no
database; the rule it justifies is pinned next door in
tests/contract/test_api_addressing_contract.py.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app(*, redirect_slashes: bool) -> FastAPI:
    app = FastAPI(root_path="/api", redirect_slashes=redirect_slashes)

    @app.get("/topics/{tid}")
    def _topic(tid: int) -> dict:
        return {"tid": tid}

    return app


def test_upstream_still_drops_root_path_from_slash_redirects() -> None:
    client = TestClient(_app(redirect_slashes=True), root_path="/api")

    response = client.get("/topics/42/", follow_redirects=False)

    assert response.status_code == 307
    location = response.headers["location"]
    assert "/api" not in location, (
        "upstream now preserves root_path on slash redirects — re-read "
        "docs/api-conventions.md, this repo turned redirect_slashes off "
        f"precisely because it did not (got {location!r})"
    )


def test_the_setting_we_ship_makes_it_a_plain_404() -> None:
    """The other half: with the redirect off, a stray slash is an honest error
    rather than a silent wrong answer."""
    client = TestClient(_app(redirect_slashes=False), root_path="/api")

    assert client.get("/topics/42", follow_redirects=False).status_code == 200
    assert client.get("/topics/42/", follow_redirects=False).status_code == 404
