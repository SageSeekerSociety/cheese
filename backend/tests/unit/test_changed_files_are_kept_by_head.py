"""A task's changed paths are read once per head commit.

The 改动 badge asks for them on every visit to a task's page, and each ask
was a forge comparison (0.4–3 s). A head's changes do not move while the head
stays, so the second ask answers from the shared cache with one head lookup.
"""

import asyncio
import types
import uuid

from app.domain.repository import forge_files
from app.domain.repository.forge_files import ProjectFiles


class _Store:
    def __init__(self):
        self.values: dict[str, bytes] = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex=None):
        self.values[key] = value.encode() if isinstance(value, str) else value


class _Files(ProjectFiles):
    def __init__(self, head: str):
        super().__init__(None, uuid.uuid4(), uuid.uuid4())  # type: ignore[arg-type]
        self.head = head
        self.compared: list[str] = []

    async def task(self):
        return types.SimpleNamespace(
            branch_name="task/x",
            base_branch="main",
            status="open",
            closing_since=None,
            delivered_head=None,
        )

    async def _head(self, *args, **kwargs):
        return self.head

    async def _data(self, project_id, session, path, **kwargs):
        self.compared.append(path)
        return {"files": [{"filename": f"{self.head}.py"}]}


def test_an_unchanged_head_is_not_compared_again(monkeypatch):
    store = _Store()
    monkeypatch.setattr(forge_files, "get_redis_client", lambda: store)
    files = _Files("a1")

    first = asyncio.run(files.changed_files())
    second = asyncio.run(files.changed_files())

    assert first == second == ["a1.py"]
    assert len(files.compared) == 1


def test_a_new_head_is_compared(monkeypatch):
    store = _Store()
    monkeypatch.setattr(forge_files, "get_redis_client", lambda: store)
    files = _Files("a1")
    asyncio.run(files.changed_files())

    files.head = "b2"

    assert asyncio.run(files.changed_files()) == ["b2.py"]
    assert len(files.compared) == 2


def test_without_the_cache_it_still_compares(monkeypatch):
    monkeypatch.setattr(forge_files, "get_redis_client", lambda: None)
    files = _Files("a1")

    assert asyncio.run(files.changed_files()) == ["a1.py"]
    assert asyncio.run(files.changed_files()) == ["a1.py"]
    assert len(files.compared) == 2
