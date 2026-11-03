"""What startup loads after the import-time freeze is frozen once, when the
process is the server (`core/startup_freeze`).

botocore's S3 model, loaded when the object store's client is warmed, is a
large tree of OrderedDicts that lives as long as the client; left tracked,
every full collection walks it with the event loop held. Production's heap
census found most of its 25,000 OrderedDicts there."""

import asyncio
import collections
import gc
import importlib
import threading

from app.core import storage
from app.core.config import settings


def _freeze():
    return importlib.import_module("app.core.startup_freeze")


def _tracked_ordered_dicts() -> int:
    gc.collect()
    return sum(type(o) is collections.OrderedDict for o in gc.get_objects())


def test_the_servers_startup_freezes_what_the_sdk_loaded(monkeypatch):
    monkeypatch.setattr(settings, "storage_type", "s3")
    monkeypatch.setattr(settings, "s3_endpoint_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "s3_access_key", "key")
    monkeypatch.setattr(settings, "s3_secret_key", "secret")
    startup_freeze = _freeze()
    monkeypatch.setattr(startup_freeze, "_done", False)

    async def start() -> tuple[int, bool]:
        async with storage.reuse_s3_connections():
            froze = startup_freeze.freeze_after_startup()
            return _tracked_ordered_dicts(), froze

    before = _tracked_ordered_dicts()
    during, froze = asyncio.run(start())

    assert froze
    # The model's OrderedDicts are frozen, not left for every collection.
    assert during <= before + 50, (before, during)


def test_it_freezes_once():
    startup_freeze = _freeze()
    startup_freeze._done = True
    frozen = gc.get_freeze_count()
    assert startup_freeze.freeze_after_startup() is False
    assert gc.get_freeze_count() == frozen


def test_an_app_run_off_the_main_thread_freezes_nothing(monkeypatch):
    """A test client runs the app on a thread of its own, and enters its
    lifespan once per test: freezing there would pin each test's engines."""
    startup_freeze = _freeze()
    monkeypatch.setattr(startup_freeze, "_done", False)
    seen = {}
    thread = threading.Thread(
        target=lambda: seen.update(froze=startup_freeze.freeze_after_startup())
    )
    thread.start()
    thread.join()
    assert seen == {"froze": False}
    assert startup_freeze._done is False
