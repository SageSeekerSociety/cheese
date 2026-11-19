"""测试脚本与命令判分的那几份文件（方案 §测试脚本、§命令判分）。

``SCRIPT``（测试脚本）和 ``COMMAND``（命令判分）两种形态都带着出题人上传的文件：

* ``SCRIPT`` 的测试文件（``role = "test"``），评测时拷进学生代码旁边的 ``tests/``；
* ``COMMAND`` 的判分文件（``role = "grader"``，只有出题人有的判分程序、数据）和保护文件
  （``role = "protected"``，起始代码里出题人点名的那几份）—— 两样都按仓库根的相对路径
  放回去，每次评测前用出题人的原版覆盖学生的版本。保护文件的字节由出题表单从起始代码里
  读出来一起送上来，平台不再去翻起始代码包：这样存下的就是出题人点保存那一刻看到的那一版。

字节和隐藏用例的期望输出一样**只进私有存储**（``app.core.blob_store``，键
``.judge-blobs/<题目>/<uuid>/file``），``TaskTestSuite.test_files`` 只记
``[{path, role, location, key, bytes, sha256}]``。学生读题时只拿到路径（保护文件的路径，
好知道哪几份会被覆盖），从来拿不到字节；出题人读题时拿回全文，出题表单原样送回来就是
「没改」（按 ``sha256`` 比，不重写字节）。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.core import blob_store
from app.core.errors import BadRequestError
from app.core.sentences import say

#: 文件的三种身份，见模块开头。
TEST, GRADER, PROTECTED = "test", "grader", "protected"
ROLES = (TEST, GRADER, PROTECTED)
#: 一套测试最多几份文件、合计多少字节（和试跑上传的参考代码同一个上限）。
MAX_FILES = 100
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_PATH_CHARS = 255


@dataclass(frozen=True)
class FileLine:
    """出题人送来的一份文件：相对路径、身份、字节。"""

    path: str
    role: str
    data: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def clean_path(raw: object) -> str:
    """一份文件的相对路径，规整过；绝对路径、``..``、空路径、以 ``/`` 结尾的都拒。

    评测主机那一侧放文件时还会再挡一遍（``runner._place``）——这里先拒，是为了出题人
    当场就知道哪一份写错了。
    """
    text = str(raw or "").replace("\\", "/").strip()
    path = PurePosixPath(text)
    if (
        not text
        or len(text) > MAX_PATH_CHARS
        or path.is_absolute()
        or ".." in path.parts
        or text.endswith("/")
        or str(path) in ("", ".")
    ):
        raise BadRequestError(say("testSuiteFileBadPath", path=text[:80]))
    return str(path)


def _bytes_of(entry: dict) -> bytes:
    """一份文件的字节：``content``（文本）或 ``base64``（二进制）二选一。"""
    if entry.get("base64") is not None:
        try:
            return base64.b64decode(str(entry["base64"]), validate=True)
        except (binascii.Error, ValueError):
            raise BadRequestError(
                say("testSuiteFileBadPath", path=str(entry.get("path") or "")[:80])
            ) from None
    return str(entry.get("content") or "").encode()


def file_lines(raw: object, role: str) -> list[FileLine]:
    """表单里的一组文件（``[{path, content}]`` 或 ``[{path, base64}]``）读成
    :class:`FileLine`。同一身份里路径重复的拒（后一份会悄悄盖掉前一份）。"""
    out: list[FileLine] = []
    seen: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        path = clean_path(entry.get("path"))
        if path in seen:
            raise BadRequestError(say("testSuiteFileDuplicate", path=path[:80]))
        seen.add(path)
        out.append(FileLine(path=path, role=role, data=_bytes_of(entry)))
    return out


def check_total(files: Sequence[FileLine]) -> None:
    """一套测试的文件合起来不超过 :data:`MAX_FILES` 份、:data:`MAX_TOTAL_BYTES` 字节。"""
    if len(files) > MAX_FILES:
        raise BadRequestError(say("testSuiteTooManyFiles", max=MAX_FILES))
    if sum(len(f.data) for f in files) > MAX_TOTAL_BYTES:
        raise BadRequestError(
            say("testSuiteFilesTooLarge", mb=MAX_TOTAL_BYTES // (1024 * 1024))
        )


def same_files(stored: list | None, files: Sequence[FileLine]) -> bool:
    """存着的那一组（``TaskTestSuite.test_files``）和送来的这一组是不是同一组：按
    次序比路径、身份和 ``sha256``，不读字节。"""
    old = [
        (str(e.get("path")), str(e.get("role")), str(e.get("sha256")))
        for e in stored or []
        if isinstance(e, dict)
    ]
    return old == [(f.path, f.role, f.sha256) for f in files]


async def store_files(base: str, files: Sequence[FileLine]) -> list[dict]:
    """把字节写进私有存储，返回 ``test_files`` 那一列要记的样子。``base`` 是键的前缀
    （``.judge-blobs/<题目>`` 或试跑的 ``.judge-blobs/trials/<id>``），每份文件一个新键：
    已出的评测、旧版测试套指着的字节不会被这一次覆盖。"""
    store = blob_store.for_new()
    out: list[dict] = []
    for line in files:
        key = f"{base}/{uuid.uuid4()}/file"
        await store.put(key, line.data)
        out.append(
            {
                "path": line.path,
                "role": line.role,
                "location": store.location,
                "key": key,
                "bytes": len(line.data),
                "sha256": line.sha256,
            }
        )
    return out


async def _read(entry: dict) -> bytes:
    try:
        return await blob_store.store(str(entry.get("location") or "")).get(
            str(entry.get("key") or "")
        )
    except LookupError:
        return b""


async def read_files(stored: list | None) -> list[FileLine]:
    """存着的那一组文件连同字节读回来（一起读，不一份一份等）。"""
    entries = [e for e in stored or [] if isinstance(e, dict)]
    datas = await asyncio.gather(*(_read(e) for e in entries))
    return [
        FileLine(path=str(e.get("path")), role=str(e.get("role")), data=data)
        for e, data in zip(entries, datas, strict=True)
    ]


def file_to_api(line: FileLine) -> dict:
    """出题人读到的一份文件：能按 UTF-8 读的给 ``content``，读不了的给 ``base64``。
    出题表单原样送回就是没改。"""
    out: dict = {"path": line.path, "bytes": len(line.data)}
    try:
        out["content"] = line.data.decode()
    except UnicodeDecodeError:
        out["base64"] = base64.b64encode(line.data).decode()
    return out
