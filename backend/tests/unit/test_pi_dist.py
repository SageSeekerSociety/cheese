"""Serving pi from the platform: what must never go wrong.

The point of this cache is that a machine with no route to the vendor still
gets the pinned agent. So the failures that matter are the ones that would hand
a machine something wrong while looking successful.
"""

import asyncio
import hashlib

import httpx
import pytest

from app.domain.machine import pi_dist

ARCHIVE = b"\x1f\x8b" + b"pretend this is a tarball" * 100
DIGEST = hashlib.sha256(ARCHIVE).hexdigest()
VERSION = "0.85.1"
PLATFORM = "linux-x64"


def _sums(digest: str = DIGEST, platform: str = PLATFORM) -> str:
    """The vendor's SHA256SUMS: every asset of the release, one per line."""
    return (
        f"{'0' * 64}  pi-{VERSION}-source.tar.gz\n"
        f"{digest}  pi-{platform}.tar.gz\n"
        f"{'1' * 64}  pi-windows-x64.zip\n"
    )


def _transport(
    *, sums: str | None = None, body: bytes = ARCHIVE, archive_status: int = 200
) -> httpx.MockTransport:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("SHA256SUMS"):
            if sums is None:
                return httpx.Response(404, text="nope")
            return httpx.Response(200, text=sums)
        return httpx.Response(archive_status, content=body)

    t = httpx.MockTransport(handler)
    t.calls = calls  # type: ignore[attr-defined]
    return t


@pytest.fixture
def upstream(monkeypatch):
    """Install a fake upstream and hand the test its transport."""

    def use(t: httpx.MockTransport) -> httpx.MockTransport:
        real = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = t
            return real(*args, **kwargs)

        monkeypatch.setattr(pi_dist.httpx, "AsyncClient", factory)
        return t

    return use


@pytest.mark.anyio
async def test_fetches_verifies_and_caches(tmp_path, upstream):
    t = upstream(_transport(sums=_sums()))

    path = await pi_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert path.read_bytes() == ARCHIVE

    before = len(t.calls)
    again = await pi_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert again == path
    assert len(t.calls) == before, "a cached archive must not be re-fetched"


@pytest.mark.anyio
async def test_a_corrupted_download_is_refused(tmp_path, upstream):
    """The whole reason to carry the vendor's checksum.

    A truncated transfer, a captive-portal HTML page, or a tampered mirror all
    look like a successful 200. Serving one to a machine would unpack a broken
    agent that fails much later and much more confusingly.
    """
    upstream(_transport(sums=_sums(), body=b"not the archive"))

    with pytest.raises(pi_dist.PiDistError) as exc:
        await pi_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert "checksum" in str(exc.value)
    # And nothing is left behind that a later request would serve as valid.
    assert not pi_dist.cached_path(tmp_path, VERSION, PLATFORM).exists()
    assert not list(pi_dist.cache_dir(tmp_path).rglob("*.part"))


@pytest.mark.anyio
async def test_missing_checksums_is_an_error_not_an_empty_file(tmp_path, upstream):
    upstream(_transport(sums=None))

    with pytest.raises(pi_dist.PiDistError):
        await pi_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert not pi_dist.cached_path(tmp_path, VERSION, PLATFORM).exists()


@pytest.mark.anyio
async def test_a_platform_the_release_does_not_carry_is_named(tmp_path, upstream):
    """One SHA256SUMS covers every asset, so the wrong line is a real risk.

    Matching loosely — on the digest column, or on a prefix — would hand a
    darwin machine the linux build with a checksum that verifies.
    """
    upstream(_transport(sums=_sums(platform="linux-arm64")))

    with pytest.raises(pi_dist.PiDistError) as exc:
        await pi_dist.ensure_cached(tmp_path, VERSION, "darwin-arm64")
    assert "darwin-arm64" in str(exc.value)


@pytest.mark.anyio
async def test_upstream_error_leaves_no_partial(tmp_path, upstream):
    upstream(_transport(sums=_sums(), archive_status=503))

    with pytest.raises(pi_dist.PiDistError):
        await pi_dist.ensure_cached(tmp_path, VERSION, PLATFORM)
    assert not pi_dist.cached_path(tmp_path, VERSION, PLATFORM).exists()


# Enrolling a fleet asks for the same 40MB archive at the same instant. Without
# the per-(version, platform) lock each request downloads its own copy and they
# race onto the same path — the reader can then see a half-written file.
@pytest.mark.anyio
async def test_concurrent_requests_fetch_once(tmp_path, upstream):
    t = upstream(_transport(sums=_sums()))

    results = await asyncio.gather(
        *[pi_dist.ensure_cached(tmp_path, VERSION, PLATFORM) for _ in range(8)]
    )
    assert len({str(r) for r in results}) == 1
    fetches = [c for c in t.calls if c.endswith(".tar.gz")]
    assert len(fetches) == 1, f"downloaded {len(fetches)} times"


def test_version_and_platform_patterns_reject_traversal():
    """These strings land in a filesystem path, so the guard is not cosmetic."""
    for bad in ["../../etc", "0.85", "latest", "0.85.1/../..", ""]:
        assert not pi_dist.VERSION_RE.match(bad), bad
    assert pi_dist.VERSION_RE.match("0.85.1")

    for bad in ["linux", "linux-amd64", "../x", "windows-x64"]:
        assert not pi_dist.PLATFORM_RE.match(bad), bad
    # No musl: the vendor's Linux builds link glibc, and serving one to an
    # Alpine machine would install a binary its loader refuses.
    assert not pi_dist.PLATFORM_RE.match("linux-x64-musl")
    for good in ["linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64"]:
        assert pi_dist.PLATFORM_RE.match(good), good
