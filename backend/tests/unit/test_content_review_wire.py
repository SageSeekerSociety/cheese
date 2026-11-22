"""The installed SDK can actually ask the service and read its answer.

Every other content review test stands in for the SDK client. This one keeps
the real client — its signing, its HTTP stack, its response models — and puts a
local server where the service would be, answering in the service's own JSON.
A dependency set whose client cannot send a request (tea-openapi 0.3.14 beside
credentials 0.3.6 raised AttributeError on every call) refuses every public
write in production while every stand-in test stays green; this one goes red.
"""

from collections.abc import AsyncIterator

import pytest
from aiohttp import web

from app.core import content_review
from app.core.config import settings
from app.core.content_review import TextKind, review_image, review_text
from app.core.errors import UnprocessableEntityError
from tests.support.content_review import Green


@pytest.fixture
async def service(monkeypatch) -> AsyncIterator[None]:
    """A local server answering `TextModerationPlus` as the service does: any
    text containing BANNED is high risk."""

    async def answer(request: web.Request) -> web.Response:
        form = await request.post()
        risk = "high" if "BANNED" in form.get("ServiceParameters", "") else "none"
        return web.json_response(
            {
                "Code": 200,
                "Message": "OK",
                "RequestId": "local",
                "Data": {"RiskLevel": risk, "Result": [{"Label": "nonLabel"}]},
            }
        )

    app = web.Application()
    app.router.add_post("/", answer)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(content_review, "ENDPOINT", f"127.0.0.1:{port}")
    monkeypatch.setattr(content_review, "PROTOCOL", "http")
    monkeypatch.setattr(settings, "aliyun_green_access_key_id", "test-key-id")
    monkeypatch.setattr(settings, "aliyun_green_access_key_secret", "test-secret")
    try:
        yield
    finally:
        await runner.cleanup()


async def test_the_real_client_asks_the_service_and_acts_on_its_answer(
    service: None,
) -> None:
    await review_text(TextKind.NAME, ["小明"])
    with pytest.raises(UnprocessableEntityError):
        await review_text(TextKind.PUBLIC_TEXT, ["招新 BANNED"])


@pytest.fixture
async def upload_bucket(green: Green, monkeypatch) -> AsyncIterator[list[bytes]]:
    """A local server where the upload token says the service's bucket is, as
    the real OSS client reaches it; yields what was stored there."""
    stored: list[bytes] = []

    async def store(request: web.Request) -> web.Response:
        stored.append(await request.read())
        return web.Response(headers={"ETag": '"local"'})

    app = web.Application()
    app.router.add_put("/{name:.*}", store)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    green.oss_endpoint = f"http://127.0.0.1:{port}"
    # The stand-in stores pictures itself; this test keeps the real upload.
    monkeypatch.setattr(content_review, "_put_for_review", real_put_for_review)
    try:
        yield stored
    finally:
        await runner.cleanup()


real_put_for_review = content_review._put_for_review


async def test_a_picture_is_stored_where_the_upload_token_says(
    upload_bucket: list[bytes],
) -> None:
    await review_image(b"a picture")

    assert upload_bucket == [b"a picture"]
