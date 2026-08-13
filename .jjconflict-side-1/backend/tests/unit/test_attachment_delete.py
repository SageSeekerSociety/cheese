"""Deleting an attachment must not report success the object survived.

The row's `storageKey` is the only pointer to the stored object. Dropping the
row after a failed storage delete orphans the file permanently: it keeps costing
storage, stays fetchable by anyone who knows the key, and nothing is left to
retry from — while the caller got 204.
"""

from types import SimpleNamespace

import pytest

from app.core.errors import InternalServerError
from app.domain.attachment.services import AttachmentService

UPLOADER = 7


class _Repo:
    def __init__(self):
        self.rows = {
            1: SimpleNamespace(id=1, meta={"uploaderId": UPLOADER, "storageKey": "k/1"})
        }
        self.deleted: list[int] = []

    async def get_by_id(self, attachment_id):
        return self.rows.get(attachment_id)

    async def delete(self, attachment_id):
        self.deleted.append(attachment_id)
        self.rows.pop(attachment_id, None)
        return True


class _Storage:
    """`delete` returning False is ambiguous by design across backends: S3 uses
    it for a failed call, the local one for a file that was already absent."""

    def __init__(self, *, delete_ok: bool, still_there: bool):
        self._delete_ok = delete_ok
        self._still_there = still_there
        self.exists_calls = 0

    async def delete(self, key):
        return self._delete_ok

    async def exists(self, key):
        self.exists_calls += 1
        return self._still_there


@pytest.mark.anyio
async def test_a_failed_storage_delete_keeps_the_record():
    repo, storage = _Repo(), _Storage(delete_ok=False, still_there=True)
    service = AttachmentService(repo, storage)  # type: ignore[arg-type]

    with pytest.raises(InternalServerError):
        await service.delete(1, UPLOADER)

    assert repo.deleted == [], "the only pointer to a surviving object was dropped"
    assert 1 in repo.rows


@pytest.mark.anyio
async def test_an_object_that_was_already_gone_is_not_an_error():
    """The end state is what was asked for. Refusing here would turn a working
    delete into an outage — the local backend reports exactly this for a file
    that is not there."""
    repo, storage = _Repo(), _Storage(delete_ok=False, still_there=False)
    service = AttachmentService(repo, storage)  # type: ignore[arg-type]

    await service.delete(1, UPLOADER)

    assert repo.deleted == [1]
    assert storage.exists_calls == 1


@pytest.mark.anyio
async def test_the_ordinary_delete_costs_no_extra_round_trip():
    repo, storage = _Repo(), _Storage(delete_ok=True, still_there=True)
    service = AttachmentService(repo, storage)  # type: ignore[arg-type]

    await service.delete(1, UPLOADER)

    assert repo.deleted == [1]
    assert storage.exists_calls == 0, "a successful delete must not re-query storage"
