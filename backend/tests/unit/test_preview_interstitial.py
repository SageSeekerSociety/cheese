"""The page and headers a preview answers with while its app is unreachable.

The rules under test are the ones a person could state before the code existed:
a navigation gets something to look at, an asset gets a transient error, a
Chinese client reads Chinese, a replaced instance is not retried forever, and
the page stays small and self-contained.
"""

import pytest
from starlette.datastructures import Headers

from app.api import preview_interstitial as page


def _headers(pairs: dict[str, str]) -> Headers:
    return Headers(pairs)


@pytest.mark.parametrize(
    "method,headers,expected",
    [
        ("GET", {"sec-fetch-dest": "document"}, True),
        ("GET", {"sec-fetch-dest": "iframe"}, True),
        ("GET", {"accept": "text/html,*/*", "sec-fetch-mode": "navigate"}, True),
        ("GET", {}, False),
        ("GET", {"sec-fetch-dest": "script"}, False),
        ("GET", {"sec-fetch-dest": "empty"}, False),
        ("GET", {"accept": "text/html", "sec-fetch-mode": "cors"}, False),
        ("GET", {"accept": "application/json"}, False),
        ("POST", {"sec-fetch-dest": "document"}, False),
        ("HEAD", {"sec-fetch-dest": "document"}, False),
    ],
)
def test_only_a_document_navigation_is_a_navigation(method, headers, expected):
    assert page.is_document_navigation(method, _headers(headers)) is expected


def _waiting(**kwargs):
    return page.preview_unavailable_response(
        state="app_unavailable", navigation=True, accept_language="zh-CN", **kwargs
    )


def test_a_navigation_gets_a_page_that_retries():
    response = _waiting()
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["Retry-After"] == "2"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Cheese-Preview-State"] == "app_unavailable"
    body = response.body.decode()
    assert "应用正在启动或重新连接" in body
    assert "应用还没有响应，芝士可能需要重启它" in body
    assert f"sec>={page.SLOW_AFTER_S}" in body
    assert "location.reload" in body


def test_an_asset_gets_a_transient_error_not_a_page():
    response = page.preview_unavailable_response(
        state="app_unavailable",
        navigation=False,
        accept_language="zh-CN",
    )
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["Retry-After"] == "2"
    assert response.headers["X-Cheese-Preview-State"] == "app_unavailable"
    assert response.body.decode() == "preview unavailable"
    assert "<html" not in response.body.decode()


@pytest.mark.parametrize(
    "accept_language,expected",
    [
        ("zh-CN,zh;q=0.9,en;q=0.8", "应用正在启动"),
        ("zh", "应用正在启动"),
        ("en-US,en;q=0.9", "The app is starting"),
        ("en", "The app is starting"),
        (None, "The app is starting"),
        ("", "The app is starting"),
        ("fr-FR,fr;q=0.9", "The app is starting"),
    ],
)
def test_the_language_follows_accept_language(accept_language, expected):
    response = page.preview_unavailable_response(
        state="app_unavailable", navigation=True, accept_language=accept_language
    )
    assert expected in response.body.decode()


def test_a_replaced_instance_is_not_retried_on_the_bound_url():
    response = page.preview_unavailable_response(
        state="instance_gone", navigation=True, accept_language="zh-CN"
    )
    assert response.status_code == 409
    assert "Retry-After" not in response.headers
    assert response.headers["X-Cheese-Preview-State"] == "instance_gone"
    assert "应用已重启，请刷新预览" in response.body.decode()
    # Reloading the bound URL would ask for the instance that just went away.
    assert "location.reload" not in response.body.decode()


def test_a_replaced_instance_asset_keeps_the_typed_status():
    response = page.preview_unavailable_response(
        state="instance_gone", navigation=False, accept_language=None
    )
    assert response.status_code == 409
    assert "Retry-After" not in response.headers
    assert response.body.decode() == "preview unavailable"


def test_the_page_stays_small_and_self_contained():
    waiting = _waiting().body.decode()
    restarted = page.preview_unavailable_response(
        state="instance_gone", navigation=True, accept_language="zh-CN"
    ).body.decode()
    for body in (waiting, restarted):
        assert len(body.encode()) < page.MAX_PAGE_BYTES
        # No external asset and no internal address.
        assert "://" not in body
        assert "http" not in body
