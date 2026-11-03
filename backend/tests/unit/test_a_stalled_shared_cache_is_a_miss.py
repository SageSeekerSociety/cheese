"""A shared cache that does not answer is a miss, not a hung page.

The 现场 page asks the shared cache before the bucket. A Redis that accepts
the connection and then says nothing must not hold the read: the body is in
the bucket, and that is what the cache is an optimization of.
"""

import asyncio
import uuid
from collections import OrderedDict

from app.domain.session_record import service


class _Stalled:
    async def get(self, key):
        await asyncio.sleep(30)

    async def set(self, key, value, ex=None):
        await asyncio.sleep(30)


class _Bucket:
    def __init__(self):
        self.downloaded: list[str] = []

    async def download(self, key):
        self.downloaded.append(key)
        return b"sealed"


def test_a_stalled_shared_cache_falls_back_to_the_bucket(monkeypatch):
    monkeypatch.setattr(service, "get_redis_client", lambda: _Stalled())
    monkeypatch.setattr(service, "_sealed_bodies", OrderedDict())
    monkeypatch.setattr(service, "_sealed_bytes", 0)
    bucket = _Bucket()
    key = f"session-records/{uuid.uuid4()}/run.jsonl.gz"

    assert asyncio.run(service.sealed_body(bucket, key)) == b"sealed"
    assert bucket.downloaded == [key]
