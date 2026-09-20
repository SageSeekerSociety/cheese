"""Serving the document toolchain from the platform: what must never go wrong.

The point of this cache is that a machine with no route to GitHub still gets
the pinned tool. So the failures that matter are the ones that would hand a
machine something wrong while looking successful — and here there is no vendor
checksum to fall back on, which makes the pinned digest the only thing standing
between a machine and whatever upstream served that day.
"""

import asyncio
import hashlib

import httpx
import pytest

from app.domain.agent import toolchain
from app.domain.machine import toolchain_dist

BODY = b"\xfd7zXZ\x00fake archive bytes" * 200
DIGEST = hashlib.sha256(BODY).hexdigest()
TOOL = "probe"
PLATFORM = "linux-x64"


@pytest.fixture
def pinned(monkeypatch):
    """A tool pinned to the digest of BODY, so the fetch path can be exercised
    without depending on which upstream release is current."""
    monkeypatch.setitem(
        toolchain.ARTIFACTS,
        (TOOL, PLATFORM),
        toolchain.Artifact(
            "https://upstream.test/probe.tar.xz", DIGEST, len(BODY), ".tar.xz"
        ),
    )


def _transport(*, body: bytes = BODY, status: int = 200) -> httpx.MockTransport:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status, content=body)

    transport = httpx.MockTransport(handler)
    transport.calls = calls  # type: ignore[attr-defined]
    return transport


@pytest.fixture
def upstream(monkeypatch):
    """Install a fake upstream and hand the test its transport."""

    def use(transport: httpx.MockTransport) -> httpx.MockTransport:
        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        monkeypatch.setattr(toolchain_dist.httpx, "AsyncClient", factory)
        return transport

    return use


@pytest.mark.anyio
async def test_fetches_verifies_and_caches(tmp_path, pinned, upstream):
    transport = upstream(_transport())

    path = await toolchain_dist.ensure_cached(tmp_path, TOOL, PLATFORM)
    assert path.read_bytes() == BODY

    before = len(transport.calls)
    again = await toolchain_dist.ensure_cached(tmp_path, TOOL, PLATFORM)
    assert again == path
    assert len(transport.calls) == before, "a cached artifact must not be re-fetched"


@pytest.mark.anyio
async def test_bytes_that_do_not_match_the_pin_are_refused(tmp_path, pinned, upstream):
    """The pin is the whole mechanism, because upstream publishes no checksum.

    A truncated transfer, a captive-portal HTML page, or a release re-cut under
    the same tag all arrive as a successful 200. Serving one to a machine would
    install a broken tool that fails much later and much more confusingly.
    """
    upstream(_transport(body=b"not the archive"))

    with pytest.raises(toolchain_dist.ToolchainDistError) as exc:
        await toolchain_dist.ensure_cached(tmp_path, TOOL, PLATFORM)
    assert "checksum" in str(exc.value)
    assert not toolchain_dist.cached_path(tmp_path, TOOL, PLATFORM).exists()
    assert not list(toolchain_dist.cache_dir(tmp_path).rglob("*.part"))


@pytest.mark.anyio
async def test_an_upstream_error_leaves_no_partial(tmp_path, pinned, upstream):
    upstream(_transport(status=503))

    with pytest.raises(toolchain_dist.ToolchainDistError):
        await toolchain_dist.ensure_cached(tmp_path, TOOL, PLATFORM)
    # Empty directories are harmless; a file is not. Nothing may be left that a
    # later request would hand to a machine as a complete artifact.
    left = [p for p in toolchain_dist.cache_dir(tmp_path).rglob("*") if p.is_file()]
    assert not left


@pytest.mark.anyio
async def test_concurrent_requests_fetch_once(tmp_path, pinned, upstream):
    """A fleet preparing at once asks for the same 35MB file at the same moment."""
    transport = upstream(_transport())

    await asyncio.gather(
        *(toolchain_dist.ensure_cached(tmp_path, TOOL, PLATFORM) for _ in range(5))
    )
    assert len(transport.calls) == 1


def test_a_font_is_the_same_file_on_every_platform():
    """Fonts carry no machine code, and a per-platform copy would be a second
    place for the two to drift — a PDF that renders differently on a Mac."""
    resolved = {
        toolchain.resolve("font-sans", platform)
        for platform in ("linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64")
    }
    assert len(resolved) == 1
    assert resolved.pop() is not None


def test_every_binary_tool_is_pinned_for_every_platform_we_serve():
    """A platform missing from the table 404s on exactly the machines that have
    it and nowhere else, which is the kind of gap that shows up as one user's
    room being broken."""
    platforms = {"linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"}
    tools = {
        tool
        for tool, _ in toolchain.ARTIFACTS
        if tool not in ("font-sans", "font-serif")
    }
    assert tools, "the table must actually carry the binaries"
    for tool in tools:
        for platform in platforms:
            assert toolchain.resolve(tool, platform), f"{tool} has no {platform}"


def test_every_pin_is_a_real_digest_over_https():
    """A blank or short digest would verify nothing while looking like a pin."""
    for (tool, platform), artifact in toolchain.ARTIFACTS.items():
        assert artifact.url.startswith("https://"), f"{tool}/{platform}"
        assert len(artifact.sha256) == 64, f"{tool}/{platform}"
        assert artifact.sha256 == artifact.sha256.lower().strip()
        assert int(artifact.sha256, 16) >= 0, f"{tool}/{platform} is not hex"
        assert artifact.size > 0, f"{tool}/{platform}"


def test_unknown_names_and_traversal_never_reach_upstream():
    assert toolchain.resolve("typst", "../../etc") is None
    assert toolchain.resolve("../claude", "linux-x64") is None
    assert toolchain.resolve("typst", "windows-x64") is None
    assert toolchain.resolve("no-such-tool", "linux-x64") is None
