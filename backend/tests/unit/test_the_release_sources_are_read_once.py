"""The release's helper sources are read from disk once per process.

`sources()` reads 24 files and 0.8 MiB, `sandbox/cheese` whole among them, and
a screen launch's preparation calls it — so the read lands on whichever turn is
starting a session. Production's stall board has a 131 ms stall whose stack is
`json/encoder` over this dict inside `release.digest`, reached from
`device_launch.holes` during a prewarm.

`remote_execution/launch._file_sources` already caches its own set for the same
reason and says so in a comment; this is that same reading for the release.
"""

import pytest

from app.domain.agent.harness.claude_code.remote_execution import release


@pytest.fixture(autouse=True)
def a_cold_cache():
    release._sources.cache_clear()
    yield
    release._sources.cache_clear()


def test_asking_twice_reads_the_files_once():
    first = release.sources()
    second = release.sources()

    info = release._sources.cache_info()
    assert info.misses == 1, f"read the files {info.misses} times for two asks"
    assert first == second


def test_the_answer_is_a_copy():
    """A caller that edits what it was given (a test adding a line to one
    helper) must not change what the next caller sees."""
    first = release.sources()
    first["client.py"] = "tampered"
    assert release.sources()["client.py"] != "tampered"


def test_the_sources_are_the_real_files():
    sources = release.sources()
    assert "client.py" in sources and "cheese.py" in sources
    assert len(sources["cheese.py"]) > 100_000, len(sources["cheese.py"])
    assert sources["client.py"].lstrip().startswith(('"""', "from", "import")), sources[
        "client.py"
    ][:40]


def test_the_version_is_encoded_once():
    """`digest` serializes 0.8 MiB of JSON; `_resident_release_due` asks it
    every turn, with the same `sources()` dict each time."""
    release._last_sources = None

    sources = release.sources()
    first = release.version_of(sources)
    for _ in range(20):
        assert release.version_of(release.sources()) == first

    assert first == release.digest(sources)


def test_a_substituted_sources_gets_its_own_version():
    """`sources()` is the override point a release and a test both use; the
    answer must describe what they handed over, not the process's own."""
    release._last_sources = None
    own = release.sources()

    changed = {**own, "client.py": own["client.py"] + "\n# next\n"}

    assert release.version_of(changed) != release.version_of(own)
    assert release.version_of(changed) == release.digest(changed)
