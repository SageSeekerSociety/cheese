"""The dependency httpcore imports on every connection is declared here.

`httpcore._synchronization.current_async_library` does `import sniffio` on
every pool operation and catches ImportError to answer "asyncio". CPython does
not cache a failed import, so when the package is absent each HTTP request
re-runs its whole path search — measurably 16 `stat` calls per request, inside
connection setup, on the event loop.

It reached the image through another package's dependency tree until #2377
removed that package, and nothing declared it since: the lock file has no
`sniffio` entry at all, which is the state this test exists to keep from
coming back.
"""

import tomllib
from pathlib import Path

import sniffio


def test_sniffio_is_installed():
    assert sniffio.current_async_library  # the attribute httpcore calls


def test_the_backend_declares_it_rather_than_inheriting_it():
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    declared = tomllib.loads(pyproject.read_text())["project"]["dependencies"]
    assert any(requirement.startswith("sniffio") for requirement in declared), declared


def test_the_lock_file_carries_it_into_the_image():
    """The image installs from the lock, not from pyproject alone; a
    declaration the lock never saw reaches nothing."""
    lock = Path(__file__).resolve().parents[2] / "uv.lock"
    assert 'name = "sniffio"' in lock.read_text()
