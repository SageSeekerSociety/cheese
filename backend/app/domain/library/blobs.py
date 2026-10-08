"""资料库的字节存在哪里 (#2114 第二节)。

记录表的每一行说清它的字节在哪：`location` 是哪一种存储，`blob_key` 是那里面的
键。上层只拿这两样读写，不拼路径——一份资料改名、挪进文件夹，改的是它的名字，
字节一动不动；以后把新文件写进对象存储，也只是多一种 `location`，老文件照旧原地
读，不用双写、不用切换期。

今天只有本地磁盘这一种：键是 `settings.workspace_root` 下的相对路径。
"""

import uuid
from pathlib import Path

from app.core.config import settings
from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.sentences import say

LOCAL = "local"


def new_key(project_id: uuid.UUID, record_id: uuid.UUID) -> str:
    """一份新字节的键：跟着记录那一行，不跟名字。"""
    return f".library-blobs/{project_id}/{record_id}"


class LocalBlobs:
    """`workspace_root` 下按键存放的字节。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        root = self.root.resolve()
        target = (root / key).resolve()
        if root not in target.parents:
            raise UnprocessableEntityError("blob key escapes the workspace")
        return target

    def put(self, key: str, data: bytes) -> None:
        """写下一份新字节。先写到旁边再换过去，读的人不会读到半份。"""
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}")
        staging.write_bytes(data)
        staging.replace(target)

    def get(self, key: str) -> bytes:
        target = self._path(key)
        if not target.is_file():
            raise NotFoundError(say("libraryFileNotFound"))
        return target.read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


def store(location: str) -> LocalBlobs:
    if location != LOCAL:
        raise UnprocessableEntityError(f"unknown blob location: {location}")
    return LocalBlobs(Path(settings.workspace_root))
