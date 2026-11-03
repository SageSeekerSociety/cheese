"""The object store SDK's models, loaded at startup after the import-time
freeze, are moved out of the collector's scan set once (`core/storage`).

botocore's S3 model is a large tree of OrderedDicts that lives as long as the
client; left tracked, every full collection walks it with the event loop
held. Production's heap census found most of its 25,000 OrderedDicts there."""

import asyncio
import collections
import gc

from app.core import storage
from app.core.config import settings


def _tracked_ordered_dicts() -> int:
    gc.collect()
    return sum(type(o) is collections.OrderedDict for o in gc.get_objects())


def test_warming_the_object_store_freezes_what_the_sdk_loaded(monkeypatch):
    monkeypatch.setattr(settings, "storage_type", "s3")
    monkeypatch.setattr(settings, "s3_endpoint_url", "http://127.0.0.1:9")
    monkeypatch.setattr(settings, "s3_access_key", "key")
    monkeypatch.setattr(settings, "s3_secret_key", "secret")
    monkeypatch.setattr(storage, "_models_frozen", False)

    async def warm() -> int:
        async with storage.reuse_s3_connections():
            return _tracked_ordered_dicts()

    before = _tracked_ordered_dicts()
    frozen_before = gc.get_freeze_count()
    during = asyncio.run(warm())

    assert gc.get_freeze_count() > frozen_before
    # The model's OrderedDicts are frozen, not left for every collection.
    assert during <= before + 50, (before, during)


def test_a_second_warmup_freezes_nothing_more(monkeypatch):
    monkeypatch.setattr(storage, "_models_frozen", True)
    frozen = gc.get_freeze_count()
    storage._freeze_loaded_models()
    assert gc.get_freeze_count() == frozen
