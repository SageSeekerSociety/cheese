"""从 GitHub 取一件工件：超限要在**读的中途**断，重定向之后不再带我们的 token。

这一批守的是「上限」这个词的含义。缓冲式下载（`client.get` 然后 `len(resp.content)`）
也会在超限时报错，但它是在**整段正文已经躺在内存里**之后才报 —— 上限管的是平台愿意
持有多久，而不是它同意收多少。区别只有一种办法能证：让响应体自己记账，看它被读到第
几块时读者就停了。

第二条守的是重定向：302 指向的签名 URL 在存储主机上，我们的 App token 不是它认的
凭据，跟过去时不能带着走。
"""

import httpx
import pytest

from app.domain.ratchet import artifacts as ratchet_artifacts
from app.domain.ratchet.artifacts import GitHubArtifacts, RatchetGitHubError


class _CountingBody(httpx.AsyncByteStream):
    """按块产出的响应体，并记住一共交出去多少 —— 「被读了多久」的账本。"""

    def __init__(self, *, chunks: int, size: int) -> None:
        self._chunks = chunks
        self._size = size
        self.sent = 0

    async def __aiter__(self):
        for _ in range(self._chunks):
            self.sent += self._size
            yield b"x" * self._size

    async def aclose(self) -> None:
        return None


def _reader(handler) -> GitHubArtifacts:
    return GitHubArtifacts(
        api_base="https://api.invalid", transport=httpx.MockTransport(handler)
    )


async def test_a_download_that_grows_past_the_cap_is_stopped_mid_body(monkeypatch):
    # 受控小输入：一共只产 100 KB，上限调成 2 KB —— 上限本身是模块常量，改它不影响
    # 别的测试，也不用真去拉一个大工件。
    monkeypatch.setattr(ratchet_artifacts, "_MAX_ARTIFACT_BYTES", 2048)
    body = _CountingBody(chunks=100, size=1000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    with pytest.raises(RatchetGitHubError) as raised:
        await _reader(handler).download(owner="o", repo="r", artifact_id=1, token="t")

    assert "超过" in str(raised.value)
    # 2 KB 上限吃到第三块（3000 字节）就断了：产出的账本停在 3 KB，而整段是 100 KB。
    # 缓冲式下载在这里会读到 100_000 —— 这条断言就是那处差别。
    assert body.sent == 3000


async def test_a_download_under_the_cap_comes_back_whole():
    body = _CountingBody(chunks=3, size=10)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    blob = await _reader(handler).download(
        owner="o", repo="r", artifact_id=1, token="t"
    )

    assert blob == b"x" * 30


async def test_the_token_is_not_resent_to_the_storage_host():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                302, headers={"location": "https://objects.invalid/signed?sig=abc"}
            )
        return httpx.Response(200, content=b"zip")

    blob = await _reader(handler).download(
        owner="o", repo="r", artifact_id=1, token="t"
    )

    assert blob == b"zip"
    assert seen[0].headers["authorization"] == "Bearer t"
    assert "authorization" not in seen[1].headers
    assert str(seen[1].url) == "https://objects.invalid/signed?sig=abc"


async def test_a_refused_download_says_which_status_it_was():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "no"})

    with pytest.raises(RatchetGitHubError) as raised:
        await _reader(handler).download(owner="o", repo="r", artifact_id=9, token="t")

    assert "403" in str(raised.value)
    assert "9" in str(raised.value)
