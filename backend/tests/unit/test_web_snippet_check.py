"""What the platform's browser decides about a mini-app before it is kept.

An app that throws or draws nothing is refused with what went wrong. Anything
that says nothing about the app — no browser, a busy one, one too old to
measure the app itself — keeps the version unchecked rather than refusing it:
otherwise 芝士 could write no app at all while the browser is away.
"""

import base64
import json

import httpx
import pytest

from app.core.config import settings
from app.domain.web_snippet import check

PNG = b"\x89PNG fake"


def _view(width: int, **fields) -> dict:
    return {
        "width": width,
        "console_errors": [],
        "element_height": 300,
        "png_b64": base64.b64encode(PNG).decode(),
        **fields,
    }


@pytest.fixture
def renderer(monkeypatch):
    """The renderer answers whatever the test puts in ``answers``, per theme."""
    answers: list[dict] = []
    seen: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=answers.pop(0) if answers else answers_default)

    answers_default = {"ok": True, "views": [_view(400), _view(720)]}
    real = httpx.AsyncClient
    monkeypatch.setattr(
        check.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handle), **kw),
    )
    monkeypatch.setattr(settings, "fetch_browser_endpoint", "http://renderer")

    async def page(shell: int) -> str:
        return "<html><head></head><body><div id='cheese-app'></div></body></html>"

    monkeypatch.setattr(check, "shell_page", page)
    return answers, seen


async def test_an_app_that_runs_is_kept_with_its_heights_and_pictures(renderer):
    ran = await check.run("<p>试算</p>", 1)
    assert ran is not None
    assert ran.heights == {400: 300, 720: 300}
    assert set(ran.shots) == {"400-light", "720-light", "400-dark", "720-dark"}
    _, seen = renderer
    # The code is in the page the browser runs, and only the app is pictured.
    assert all(
        "试算" in body["html"] and body["element"] == "#cheese-app" for body in seen
    )


async def test_an_app_that_throws_is_refused_with_the_error(renderer):
    answers, _ = renderer
    answers.append(
        {"ok": True, "views": [_view(400, console_errors=["ReferenceError: x"])]}
    )
    with pytest.raises(check.Refused, match="ReferenceError: x"):
        await check.run("<script>x()</script>", 1)


async def test_an_app_that_draws_nothing_is_refused(renderer):
    answers, _ = renderer
    answers.append({"ok": True, "views": [_view(400, element_height=0)]})
    with pytest.raises(check.Refused):
        await check.run("<script>let a = 1</script>", 1)


@pytest.mark.parametrize(
    "answer",
    [
        {"ok": False, "error": "busy: every inspection slot is taken"},
        # A renderer from before it could picture one element.
        {
            "ok": True,
            "views": [{k: v for k, v in _view(400).items() if k != "element_height"}],
        },
    ],
)
async def test_a_browser_that_learns_nothing_keeps_the_app_unchecked(renderer, answer):
    answers, _ = renderer
    answers.append(answer)
    assert await check.run("<p>试算</p>", 1) is None


async def test_with_no_browser_configured_the_app_is_kept_unchecked(monkeypatch):
    monkeypatch.setattr(settings, "fetch_browser_endpoint", None)
    assert await check.run("<p>试算</p>", 1) is None


async def test_an_app_that_breaks_once_used_is_refused_with_what_was_done(renderer):
    answers, seen = renderer
    answers.append(
        {
            "ok": True,
            "views": [
                _view(
                    400,
                    # The browser's own complaint arrives first; what the shell
                    # says names the action that brought it out.
                    exercise_errors=[
                        'Error: <circle> attribute cx: Expected length, "NaN".',
                        "读者把「速度」设为 10 后，画面上出现了 NaN：<circle> 的 cx",
                    ],
                ),
                _view(720),
            ],
        }
    )
    with pytest.raises(check.Refused, match="读者把「速度」设为 10 后"):
        await check.run("<p>试算</p>", 1)
    # It is asked to use the app once, not in both themes.
    assert seen[0]["exercise"] is True


async def test_a_renderer_that_cannot_use_the_app_still_keeps_it(renderer):
    """One from before it could exercise a page says nothing of it: the app is
    kept on what it did say."""
    ran = await check.run("<p>试算</p>", 1)
    assert ran is not None
    _, seen = renderer
    assert [body["exercise"] for body in seen] == [True, False]
