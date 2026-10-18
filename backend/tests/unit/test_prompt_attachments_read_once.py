"""A message's attachments are asked about in batches, not one at a time.

One pending message can carry several files through the same two stores: the
library table (a row per library file) and the room's own directory. Asking per
attachment cost one query and one thread-pool hop each, on the first-byte path
of every turn.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.prompt import offered_attachments
from app.domain.block.models import BlockKind
from app.domain.library import records as library_records
from app.domain.library import service as library_service
from tests.support.pictures import picture

LIBRARY_BYTES = picture("PNG")
ROOM_BYTES = picture("JPEG")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return iter(self._rows)


def _asked_names(statement) -> set[str]:
    """The names a batched library query asks about (`name IN (...)`)."""
    wanted: set[str] = set()
    pending = [statement.whereclause]
    while pending:
        node = pending.pop()
        if node is None:
            continue
        values = getattr(getattr(node, "right", None), "value", None)
        if isinstance(values, (list, set, tuple)):
            wanted.update(values)
        pending.extend(getattr(node, "clauses", ()))
    return wanted


class _CountingSession:
    """Answers the turn's library queries, and counts them.

    A name-less select asks which files exist; a row select asks for their
    storage keys. The deleted file has a row in neither answer — that is what
    the library does after somebody removes it.
    """

    def __init__(self, files):
        self.files = files  # name -> (location, blob_key)
        self.executes = 0
        self.asked: list[set[str]] = []

    async def scalar(self, statement):
        """One name at a time, for the per-attachment shape this replaces."""
        self.executes += 1
        name = getattr(getattr(statement.whereclause, "right", None), "value", None)
        if name not in self.files:
            return None
        location, blob_key = self.files[name]
        return SimpleNamespace(name=name, location=location, blob_key=blob_key)

    async def execute(self, statement):
        self.executes += 1
        names = _asked_names(statement)
        self.asked.append(names)
        if len(statement.selected_columns) == 1:
            return _Result([name for name in names if name in self.files])
        return _Result(
            [
                SimpleNamespace(
                    name=name,
                    location=self.files[name][0],
                    blob_key=self.files[name][1],
                )
                for name in names
                if name in self.files
            ]
        )


class _Store:
    def __init__(self, blobs):
        self._blobs = blobs
        self.read: list[str] = []

    def get(self, key):
        self.read.append(key)
        return self._blobs[key]


@pytest.fixture
def store(monkeypatch):
    blobs = _Store({"blob-one": LIBRARY_BYTES})
    monkeypatch.setattr(library_records.blobs, "store", lambda _location: blobs)
    return blobs


@pytest.fixture
def room(monkeypatch):
    files = {"uploads/shot.jpg": ROOM_BYTES}
    monkeypatch.setattr(
        library_service,
        "room_file_exists",
        lambda _project_id, _room_id, path: path in files,
    )
    monkeypatch.setattr(
        library_service,
        "read_room_file",
        lambda _project_id, _room_id, path: files[path],
    )
    return files


@pytest.fixture
def thread_calls(monkeypatch):
    """Every thread-pool hop the attachment stores make, in order."""
    calls = []

    async def to_thread(func, /, *args, **kwargs):
        calls.append(func.__name__)
        return func(*args, **kwargs)

    # Only the records module's reference is replaced: `prompt` still decodes
    # bytes on the real pool.
    monkeypatch.setattr(
        library_records, "asyncio", SimpleNamespace(to_thread=to_thread)
    )
    return calls


def _attachment(path: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind=BlockKind.attachment,
        content=path,
        mime_type="image/png",
    )


def _three_attachments() -> list[SimpleNamespace]:
    # Two library files and one of the room's own; the first library file is
    # the one somebody deleted while the message still references it.
    return [
        _attachment("library/one.png"),
        _attachment("library/gone.png"),
        _attachment("uploads/shot.jpg"),
    ]


def _session() -> _CountingSession:
    # `library/gone.png` has no row: the file was deleted after it was sent.
    return _CountingSession({"one.png": ("local", "blob-one")})


@pytest.mark.anyio
async def test_a_message_reads_the_library_once_and_the_room_once(
    store, room, thread_calls
):
    session = _session()

    images, gone, undecodable = await offered_attachments(
        session, _three_attachments(), uuid.uuid4(), uuid.uuid4()
    )

    # Presence and bytes each ask the library table once for the whole message.
    assert session.executes == 2
    # The room's existence check is one hop, and the byte reads are another.
    assert thread_calls == ["_room_files_present", "_read_attachment_bytes"]
    assert store.read == ["blob-one"]
    assert [image.data for image in images] == [LIBRARY_BYTES, ROOM_BYTES]
    assert undecodable == set()


@pytest.mark.anyio
async def test_a_deleted_file_is_gone_and_is_never_read(store, room, thread_calls):
    """The message outlives its file: the turn goes on, the prompt line says the
    file is gone, and nothing tries to read bytes that are not there."""
    blocks = _three_attachments()
    session = _session()

    _images, gone, _undecodable = await offered_attachments(
        session, blocks, uuid.uuid4(), uuid.uuid4()
    )

    assert gone == {blocks[1].id}
    # Presence asks about every path in one query, and only the surviving
    # library file reaches the byte read. Should the deleted name be read, the
    # library would raise and the whole turn would end unfinished.
    assert session.asked[0] == {"one.png", "gone.png"}
    assert session.asked[1] == {"one.png"}
