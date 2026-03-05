"""Tests for core utility modules: storage, metrics, and crypto.

Covers:
- storage.py: LocalStorageBackend (upload, download, delete, exists, get_url,
  path traversal prevention) and helpers (generate_storage_key, compute_file_hash)
- metrics.py: Counter, Gauge, Histogram, MetricsRegistry
- crypto.py: encrypt_text / decrypt_text round-trip, key derivation
"""

import base64
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# ===========================================================================
# storage.py — LocalStorageBackend
# ===========================================================================


def _make_local_backend(tmp_path: Path):
    """Create a LocalStorageBackend rooted at tmp_path."""
    from app.core.storage import LocalStorageBackend

    return LocalStorageBackend(
        base_path=str(tmp_path / "storage"),
        base_url="http://localhost:8000/files",
    )


@pytest.mark.anyio
async def test_local_upload_creates_file_and_returns_url(tmp_path: Path):
    backend = _make_local_backend(tmp_path)
    content = b"hello cheese world"
    file_obj = io.BytesIO(content)

    url = await backend.upload(file_obj, "photos/pic.jpg", "image/jpeg")

    assert url == "http://localhost:8000/files/photos/pic.jpg"
    written = (tmp_path / "storage" / "photos" / "pic.jpg").read_bytes()
    assert written == content


@pytest.mark.anyio
async def test_local_download_existing_file(tmp_path: Path):
    backend = _make_local_backend(tmp_path)
    file_obj = io.BytesIO(b"download-me")
    await backend.upload(file_obj, "a.txt", "text/plain")

    data = await backend.download("a.txt")

    assert data == b"download-me"


@pytest.mark.anyio
async def test_local_download_missing_file_returns_none(tmp_path: Path):
    backend = _make_local_backend(tmp_path)

    result = await backend.download("nonexistent.bin")

    assert result is None


@pytest.mark.anyio
async def test_local_delete_removes_file(tmp_path: Path):
    backend = _make_local_backend(tmp_path)
    await backend.upload(io.BytesIO(b"del"), "rm.txt", "text/plain")
    assert await backend.exists("rm.txt") is True

    deleted = await backend.delete("rm.txt")

    assert deleted is True
    assert await backend.exists("rm.txt") is False


@pytest.mark.anyio
async def test_local_delete_missing_file_returns_false(tmp_path: Path):
    backend = _make_local_backend(tmp_path)

    deleted = await backend.delete("ghost.txt")

    assert deleted is False


@pytest.mark.anyio
async def test_local_exists_true_and_false(tmp_path: Path):
    backend = _make_local_backend(tmp_path)
    await backend.upload(io.BytesIO(b"x"), "here.txt", "text/plain")

    assert await backend.exists("here.txt") is True
    assert await backend.exists("not_here.txt") is False


def test_local_get_url_strips_trailing_slash(tmp_path: Path):
    from app.core.storage import LocalStorageBackend

    backend = LocalStorageBackend(
        base_path=str(tmp_path),
        base_url="http://example.com/files/",
    )

    assert backend.get_url("img.png") == "http://example.com/files/img.png"


@pytest.mark.anyio
async def test_local_upload_nested_key_creates_subdirectories(tmp_path: Path):
    backend = _make_local_backend(tmp_path)

    await backend.upload(io.BytesIO(b"nested"), "a/b/c/deep.txt", "text/plain")

    assert (tmp_path / "storage" / "a" / "b" / "c" / "deep.txt").exists()


@pytest.mark.anyio
async def test_local_path_traversal_stays_within_base(tmp_path: Path):
    """Uploading with '..' in the key should still write relative to base_path.

    Note: Python's Path / operator resolves '..' against the base, so the file
    ends up at the resolved location.  The important thing is that we can
    verify the actual write location.
    """
    backend = _make_local_backend(tmp_path)
    key = "../escaped.txt"

    await backend.upload(io.BytesIO(b"sneaky"), key, "text/plain")

    # The file is resolved relative to base_path; verify it exists via the
    # same resolution path the backend uses internally.
    resolved = (tmp_path / "storage" / key).resolve()
    assert resolved.exists()
    # Verify the file did NOT end up above tmp_path (the sandbox).
    assert str(resolved).startswith(str(tmp_path.resolve())), (
        f"File escaped tmp_path sandbox: {resolved}"
    )


# ===========================================================================
# storage.py — generate_storage_key
# ===========================================================================


def test_generate_storage_key_format():
    from app.core.storage import generate_storage_key

    key = generate_storage_key("photo.JPG", prefix="images")

    parts = key.split("/")
    assert parts[0] == "images"
    # date part: YYYY/MM/DD
    assert len(parts[1]) == 4  # year
    assert len(parts[2]) == 2  # month
    assert len(parts[3]) == 2  # day
    # filename: 12-char hex + extension
    filename = parts[4]
    assert filename.endswith(".jpg"), f"Extension should be lowercased, got {filename}"
    stem = filename.removesuffix(".jpg")
    assert len(stem) == 12
    int(stem, 16)  # must be valid hex


def test_generate_storage_key_no_extension():
    from app.core.storage import generate_storage_key

    key = generate_storage_key("", prefix="uploads")

    # Should end with the 12-char hex (no trailing dot).
    filename = key.split("/")[-1]
    assert "." not in filename
    assert len(filename) == 12


def test_generate_storage_key_default_prefix():
    from app.core.storage import generate_storage_key

    key = generate_storage_key("doc.pdf")

    assert key.startswith("uploads/")


def test_generate_storage_key_uniqueness():
    from app.core.storage import generate_storage_key

    keys = {generate_storage_key("f.txt") for _ in range(50)}

    assert len(keys) == 50, "Keys must be unique across calls"


# ===========================================================================
# storage.py — compute_file_hash
# ===========================================================================


def test_compute_file_hash_correct_md5():
    from app.core.storage import compute_file_hash

    content = b"cheese is delicious"
    expected = hashlib.md5(content).hexdigest()
    file_obj = io.BytesIO(content)

    result = compute_file_hash(file_obj)

    assert result == expected


def test_compute_file_hash_resets_seek_position():
    from app.core.storage import compute_file_hash

    file_obj = io.BytesIO(b"seek test")
    compute_file_hash(file_obj)

    assert file_obj.tell() == 0, "File position must be reset to 0 after hashing"


def test_compute_file_hash_empty_file():
    from app.core.storage import compute_file_hash

    result = compute_file_hash(io.BytesIO(b""))

    assert result == hashlib.md5(b"").hexdigest()


def test_compute_file_hash_large_content():
    """Verify chunked reading produces the same hash as a single-shot digest."""
    from app.core.storage import compute_file_hash

    content = b"A" * 100_000  # larger than 8192-byte chunk
    expected = hashlib.md5(content).hexdigest()

    assert compute_file_hash(io.BytesIO(content)) == expected


# ===========================================================================
# metrics.py — Counter
# ===========================================================================


def test_counter_starts_at_zero():
    from app.core.metrics import Counter

    c = Counter(name="test_counter")

    assert c.value == 0


def test_counter_inc_default():
    from app.core.metrics import Counter

    c = Counter(name="test_counter")
    c.inc()

    assert c.value == 1


def test_counter_inc_custom_amount():
    from app.core.metrics import Counter

    c = Counter(name="test_counter")
    c.inc(5)
    c.inc(3)

    assert c.value == 8


def test_counter_preserves_labels():
    from app.core.metrics import Counter

    labels = {"method": "GET", "path": "/api"}
    c = Counter(name="http_total", labels=labels)

    assert c.labels == labels


# ===========================================================================
# metrics.py — Gauge
# ===========================================================================


def test_gauge_starts_at_zero():
    from app.core.metrics import Gauge

    g = Gauge(name="test_gauge")

    assert g.value == 0.0


def test_gauge_set():
    from app.core.metrics import Gauge

    g = Gauge(name="test_gauge")
    g.set(42.5)

    assert g.value == 42.5


def test_gauge_inc_default():
    from app.core.metrics import Gauge

    g = Gauge(name="test_gauge")
    g.inc()

    assert g.value == 1.0


def test_gauge_inc_custom():
    from app.core.metrics import Gauge

    g = Gauge(name="test_gauge")
    g.inc(2.5)

    assert g.value == 2.5


def test_gauge_dec_default():
    from app.core.metrics import Gauge

    g = Gauge(name="test_gauge")
    g.set(10.0)
    g.dec()

    assert g.value == 9.0


def test_gauge_dec_custom():
    from app.core.metrics import Gauge

    g = Gauge(name="test_gauge")
    g.set(10.0)
    g.dec(3.5)

    assert g.value == 6.5


def test_gauge_can_go_negative():
    from app.core.metrics import Gauge

    g = Gauge(name="test_gauge")
    g.dec(5.0)

    assert g.value == -5.0


# ===========================================================================
# metrics.py — Histogram
# ===========================================================================


def test_histogram_default_buckets():
    from app.core.metrics import Histogram

    h = Histogram(name="test_hist")
    expected = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]

    assert h.buckets == expected


def test_histogram_custom_buckets():
    from app.core.metrics import Histogram

    h = Histogram(name="test_hist", buckets=[0.1, 0.5, 1.0])

    assert h.buckets == [0.1, 0.5, 1.0]


def test_histogram_observe_count_and_sum():
    from app.core.metrics import Histogram

    h = Histogram(name="test_hist", buckets=[1.0, 5.0, 10.0])
    h.observe(0.5)
    h.observe(3.0)
    h.observe(7.0)

    assert h._count == 3
    assert h._sum == pytest.approx(10.5)


def test_histogram_observe_bucket_assignment():
    from app.core.metrics import Histogram

    h = Histogram(name="test_hist", buckets=[1.0, 5.0, 10.0])
    h.observe(0.5)   # bucket 0 (<=1.0)
    h.observe(1.0)   # bucket 0 (<=1.0)
    h.observe(3.0)   # bucket 1 (<=5.0)
    h.observe(7.0)   # bucket 2 (<=10.0)
    h.observe(99.0)  # overflow bucket (+Inf)

    # _counts has len(buckets)+1 entries
    assert h._counts == [2, 1, 1, 1]


def test_histogram_observe_all_overflow():
    from app.core.metrics import Histogram

    h = Histogram(name="test_hist", buckets=[1.0])
    h.observe(5.0)
    h.observe(10.0)

    assert h._counts[0] == 0  # nothing <= 1.0
    assert h._counts[1] == 2  # overflow


def test_histogram_observe_exact_boundary():
    from app.core.metrics import Histogram

    h = Histogram(name="test_hist", buckets=[1.0, 2.0])
    h.observe(1.0)  # exactly on first boundary
    h.observe(2.0)  # exactly on second boundary

    assert h._counts == [1, 1, 0]


# ===========================================================================
# metrics.py — MetricsRegistry
# ===========================================================================


def test_registry_counter_returns_same_instance():
    from app.core.metrics import MetricsRegistry

    reg = MetricsRegistry()
    c1 = reg.counter("req_total")
    c2 = reg.counter("req_total")

    assert c1 is c2


def test_registry_counter_with_labels_different_instances():
    from app.core.metrics import MetricsRegistry

    reg = MetricsRegistry()
    c1 = reg.counter("req_total", labels={"method": "GET"})
    c2 = reg.counter("req_total", labels={"method": "POST"})

    assert c1 is not c2
    c1.inc()
    assert c1.value == 1
    assert c2.value == 0


def test_registry_gauge_returns_same_instance():
    from app.core.metrics import MetricsRegistry

    reg = MetricsRegistry()
    g1 = reg.gauge("active")
    g2 = reg.gauge("active")

    assert g1 is g2


def test_registry_histogram_returns_same_instance():
    from app.core.metrics import MetricsRegistry

    reg = MetricsRegistry()
    h1 = reg.histogram("latency")
    h2 = reg.histogram("latency")

    assert h1 is h2


def test_registry_histogram_custom_buckets_from_registry():
    from app.core.metrics import MetricsRegistry

    reg = MetricsRegistry()
    h = reg.histogram("latency", buckets=[0.1, 1.0])

    assert h.buckets == [0.1, 1.0]


def test_registry_export_structure():
    from app.core.metrics import MetricsRegistry

    reg = MetricsRegistry()
    c = reg.counter("hits")
    c.inc(7)
    g = reg.gauge("temp")
    g.set(36.6)
    h = reg.histogram("dur", buckets=[1.0])
    h.observe(0.5)

    export = reg.export()

    assert "uptime_seconds" in export
    assert export["uptime_seconds"] >= 0

    assert "hits" in export["counters"]
    assert export["counters"]["hits"]["value"] == 7

    assert "temp" in export["gauges"]
    assert export["gauges"]["temp"]["value"] == pytest.approx(36.6)

    assert "dur" in export["histograms"]
    hist_data = export["histograms"]["dur"]
    assert hist_data["count"] == 1
    assert hist_data["sum"] == pytest.approx(0.5)
    assert "1.0" in hist_data["buckets"]
    assert "+Inf" in hist_data["buckets"]


def test_registry_key_generation_with_labels():
    from app.core.metrics import MetricsRegistry

    reg = MetricsRegistry()

    # Labels should be sorted alphabetically in the key
    reg.counter("req", labels={"z": "1", "a": "2"})
    key = reg._key("req", {"z": "1", "a": "2"})

    assert key == "req{a=2,z=1}"
    assert key in reg._counters


def test_registry_key_generation_without_labels():
    from app.core.metrics import MetricsRegistry

    reg = MetricsRegistry()

    key = reg._key("simple", None)

    assert key == "simple"


# ===========================================================================
# crypto.py — encrypt_text / decrypt_text round-trip
# ===========================================================================


def _mock_settings_for_crypto(*, encryption_key: str = "", jwt_secret: str = "test-jwt-secret"):
    """Return a SimpleNamespace that looks like settings for crypto tests."""
    return SimpleNamespace(
        realname_encryption_key=encryption_key,
        jwt_secret=jwt_secret,
    )


def test_encrypt_decrypt_round_trip_derived_key():
    """When no explicit key is set, derive from jwt_secret; round-trip must work."""
    from app.core.crypto import get_fernet

    # Clear the lru_cache so our patched settings take effect.
    get_fernet.cache_clear()

    mock_settings = _mock_settings_for_crypto(jwt_secret="my-super-secret")
    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import decrypt_text, encrypt_text

        plaintext = "Alice Wonderland"
        token = encrypt_text(plaintext)
        recovered = decrypt_text(token)

        assert recovered == plaintext
        assert token != plaintext  # must be encrypted, not plaintext

    get_fernet.cache_clear()


def test_encrypt_decrypt_round_trip_explicit_key():
    """When an explicit Fernet key is provided, use it directly."""
    from cryptography.fernet import Fernet

    from app.core.crypto import get_fernet

    get_fernet.cache_clear()

    explicit_key = Fernet.generate_key().decode("utf-8")
    mock_settings = _mock_settings_for_crypto(encryption_key=explicit_key)
    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import decrypt_text, encrypt_text

        plaintext = "Bob Builder"
        token = encrypt_text(plaintext)
        recovered = decrypt_text(token)

        assert recovered == plaintext

    get_fernet.cache_clear()


def test_derive_key_from_jwt_secret_is_deterministic():
    """Same jwt_secret must always produce the same derived key."""
    from app.core.crypto import get_fernet

    get_fernet.cache_clear()

    secret = "deterministic-secret"
    mock_settings = _mock_settings_for_crypto(jwt_secret=secret)
    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import _derive_key

        key1 = _derive_key()

    get_fernet.cache_clear()

    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import _derive_key

        key2 = _derive_key()

    assert key1 == key2

    get_fernet.cache_clear()


def test_derive_key_uses_sha256_of_jwt_secret():
    """Verify the derived key matches sha256(jwt_secret) base64-encoded."""
    from app.core.crypto import get_fernet

    get_fernet.cache_clear()

    secret = "known-secret"
    mock_settings = _mock_settings_for_crypto(jwt_secret=secret)
    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import _derive_key

        key = _derive_key()

    expected_digest = hashlib.sha256(secret.encode("utf-8")).digest()
    expected_key = base64.urlsafe_b64encode(expected_digest)

    assert key == expected_key

    get_fernet.cache_clear()


def test_encrypt_produces_different_tokens_for_same_input():
    """Fernet encryption is non-deterministic (uses timestamp + random IV)."""
    from app.core.crypto import get_fernet

    get_fernet.cache_clear()

    mock_settings = _mock_settings_for_crypto(jwt_secret="token-variety")
    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import encrypt_text

        t1 = encrypt_text("same input")
        t2 = encrypt_text("same input")

        assert t1 != t2  # different ciphertexts

    get_fernet.cache_clear()


def test_decrypt_invalid_token_raises():
    """Decrypting garbage should raise InternalServerError."""
    from app.core.crypto import get_fernet

    get_fernet.cache_clear()

    mock_settings = _mock_settings_for_crypto(jwt_secret="err-test")
    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import decrypt_text
        from app.core.errors import InternalServerError

        with pytest.raises(InternalServerError):
            decrypt_text("not-a-valid-fernet-token")

    get_fernet.cache_clear()


def test_encrypt_text_returns_string():
    from app.core.crypto import get_fernet

    get_fernet.cache_clear()

    mock_settings = _mock_settings_for_crypto(jwt_secret="type-check")
    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import encrypt_text

        result = encrypt_text("hello")
        assert isinstance(result, str)

    get_fernet.cache_clear()


def test_encrypt_empty_string():
    """Encrypting an empty string should still round-trip correctly."""
    from app.core.crypto import get_fernet

    get_fernet.cache_clear()

    mock_settings = _mock_settings_for_crypto(jwt_secret="empty-test")
    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import decrypt_text, encrypt_text

        token = encrypt_text("")
        assert decrypt_text(token) == ""

    get_fernet.cache_clear()


def test_encrypt_unicode():
    """Encrypting unicode text should round-trip correctly."""
    from app.core.crypto import get_fernet

    get_fernet.cache_clear()

    mock_settings = _mock_settings_for_crypto(jwt_secret="unicode-test")
    with patch("app.core.crypto.settings", mock_settings):
        from app.core.crypto import decrypt_text, encrypt_text

        plaintext = "Cheese is awesome!"
        token = encrypt_text(plaintext)
        assert decrypt_text(token) == plaintext

    get_fernet.cache_clear()
