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
    monkeypatch.setenv("CHEESE_SERVING", "1")

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
    monkeypatch.setenv("CHEESE_SERVING", "1")
    seen = {}
    thread = threading.Thread(
        target=lambda: seen.update(froze=startup_freeze.freeze_after_startup())
    )
    thread.start()
    thread.join()
    assert seen == {"froze": False}
    assert startup_freeze._done is False


def test_a_process_the_server_did_not_start_freezes_nothing(monkeypatch):
    """Tests enter the lifespan on the main thread too
    (`test_backend_handover_signal.py`): only the serving entry point's
    processes freeze."""
    startup_freeze = _freeze()
    monkeypatch.setattr(startup_freeze, "_done", False)
    monkeypatch.delenv("CHEESE_SERVING", raising=False)
    frozen = gc.get_freeze_count()
    assert startup_freeze.freeze_after_startup() is False
    assert gc.get_freeze_count() == frozen


def test_the_serving_entry_point_marks_its_processes(monkeypatch):
    import pytest

    from app import serve

    class Execed(Exception):
        pass

    def execvp(*_args):
        raise Execed  # exec replaces the process; nothing after it runs

    monkeypatch.delenv("CHEESE_SERVING", raising=False)
    monkeypatch.setattr(serve.os, "execvp", execvp)
    monkeypatch.setenv("API_POOL_WORKERS", "0")
    with pytest.raises(Execed):
        serve.main()
    assert serve.os.environ.get("CHEESE_SERVING") == "1"
    assert serve.SERVING == _freeze().SERVING
