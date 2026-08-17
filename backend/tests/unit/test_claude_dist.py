"""Serving Claude Code from the platform: what must never go wrong.

The point of this cache is that a machine with no route to the vendor still
gets the pinned binary. So the failures that matter are the ones that would
hand a machine something wrong while looking successful.
"""

import asyncio
import hashlib
import json

import httpx
import pytest

from app.domain.machine import claude_dist

BINARY = b"#!/bin/sh\necho claude\n" * 100
DIGEST = hashlib.sha256(BINARY).hexdigest()
VERSION = "2.1.224"
PLATFORM = "linux-x64"


def _manifest(digest: str = DIGEST, platform: str = PLATFORM) -> str:
    return json.dumps({"platforms": {platform: {"checksum": digest}}})


def _transport(
    *, manifest: str | None = None, body: bytes = BINARY, binary_status: int = 200
) -> httpx.MockTransport:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("manifest.json"):
            if manifest is None:
                return httpx.Response(404, text="nope")
            return httpx.Response(200, text=manifest)
        return httpx.Response(binary_status, content=body)

    t = httpx.MockTransport(handler)
    t.calls = calls  # type: ignore[attr-defined]
    return t


@pytest.fixture
def upstream(monkeypatch):
    """Install a fake upstream and hand the test its transport."""

    holder: dict[str, httpx.MockTransport] = {}

    def use(t: httpx.MockTransport) -> httpx.MockTransport:
        holder["t"] = t
        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = t
            return real(*args, **kwargs)

        monkeypatch.setattr(claude_dist.httpx, "AsyncClient", factory)
        return t

    return use


@pytest.mark.anyio
async def test_fetches_verifies_and_caches(tmp_path, upstream):
    t = upstream(_transport(manifest=_manifest()))

    path = await claude_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert path.read_bytes() == BINARY
    # Executable: the machine runs this file directly.
    assert path.stat().st_mode & 0o111

    before = len(t.calls)
    again = await claude_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert again == path
    assert len(t.calls) == before, "a cached binary must not be re-fetched"


@pytest.mark.anyio
async def test_a_corrupted_download_is_refused(tmp_path, upstream):
    """The whole reason to carry the vendor's checksum.

    A truncated transfer, a captive-portal HTML page, or a tampered mirror all
    look like a successful 200. Serving one to a machine would install a broken
    agent that fails much later and much more confusingly.
    """
    upstream(_transport(manifest=_manifest(), body=b"not the binary"))

    with pytest.raises(claude_dist.ClaudeDistError) as exc:
        await claude_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert "checksum" in str(exc.value)
    # And nothing is left behind that a later request would serve as valid.
    assert not claude_dist.cached_path(tmp_path, VERSION, PLATFORM).exists()
    assert not list(claude_dist.cache_dir(tmp_path).rglob("*.part"))


@pytest.mark.anyio
async def test_missing_manifest_is_an_error_not_an_empty_file(tmp_path, upstream):
    upstream(_transport(manifest=None))

    with pytest.raises(claude_dist.ClaudeDistError):
        await claude_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert not claude_dist.cached_path(tmp_path, VERSION, PLATFORM).exists()


@pytest.mark.anyio
async def test_platform_absent_from_manifest_is_named(tmp_path, upstream):
    upstream(_transport(manifest=_manifest(platform="darwin-arm64")))

    with pytest.raises(claude_dist.ClaudeDistError) as exc:
        await claude_dist.ensure_cached(tmp_path, VERSION, "linux-arm64-musl")
    assert "linux-arm64-musl" in str(exc.value)


@pytest.mark.anyio
async def test_upstream_error_leaves_no_partial(tmp_path, upstream):
    upstream(_transport(manifest=_manifest(), binary_status=503))

    with pytest.raises(claude_dist.ClaudeDistError):
        await claude_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert not claude_dist.cached_path(tmp_path, VERSION, PLATFORM).exists()


# Enrolling a fleet asks for the same 40MB binary at the same instant. Without
# the per-(version, platform) lock each request downloads its own copy and they
# race onto the same path — the reader can then see a half-written file.
@pytest.mark.anyio
async def test_concurrent_requests_fetch_once(tmp_path, upstream):
    t = upstream(_transport(manifest=_manifest()))

    results = await asyncio.gather(
        *[claude_dist.ensure_cached(tmp_path, VERSION, PLATFORM) for _ in range(8)]
    )
    assert len({str(r) for r in results}) == 1
    binary_fetches = [c for c in t.calls if c.endswith("/claude")]
    assert len(binary_fetches) == 1, f"downloaded {len(binary_fetches)} times"


def test_version_and_platform_patterns_reject_traversal():
    """These strings land in a filesystem path, so the guard is not cosmetic."""
    for bad in ["../../etc", "2.1", "latest", "2.1.224/../..", ""]:
        assert not claude_dist.VERSION_RE.match(bad), bad
    for good in ["2.1.224", "2.1.224-beta.1"]:
        assert claude_dist.VERSION_RE.match(good), good

    for bad in ["linux", "linux-amd64", "../x", "windows-x64", "linux-x64-gnu"]:
        assert not claude_dist.PLATFORM_RE.match(bad), bad
    # The vendor's vocabulary, musl included — our own <os>-<arch> target names
    # cannot express musl, which is why this proxies rather than translates.
    for good in ["linux-x64", "linux-arm64", "linux-x64-musl", "darwin-arm64"]:
        assert claude_dist.PLATFORM_RE.match(good), good
