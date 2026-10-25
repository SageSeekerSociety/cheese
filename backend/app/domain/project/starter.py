"""起始代码 (#4767 第二节)：出题人给的一个 .zip，领题建项目时作为仓库的第一个提交。

压缩包在出题人上传时查一遍（`check`），领题时解开、提交、推上项目的仓库
（`push`）。两次用同一套规矩，所以出题人当场就知道这份能不能用，不必等到有人领题：

- 每一项的路径都要落在解包目录里面：拒绝绝对路径、盘符、`..`；
- 只收普通文件和目录：符号链接、设备、管道一律拒绝（zip 格式没有硬链接）；
- 文件数、解开后的总大小有上限，解开后比压缩包本身大得离谱的（压缩炸弹）拒绝；
- `__MACOSX/`、`.DS_Store` 和任何一层叫 `.git`（大小写不论）的路径不要：前两样是
  压缩工具顺手塞进来的，`.git` 是压缩整个仓库文件夹时带进来的 git 自己的东西；
- 每一层名字不超过 255 字节、整条路径不超过 4096 字节，不含控制字符；其余 git 不收的
  路径（`GIT~1` 这类 `.git` 的别名）问 git 自己，拒绝；
- 只收 `zipfile` 解得开的压缩方式；
- 整个压缩包只有一个顶层文件夹时，去掉这一层：多数人是右键压缩整个文件夹的。

解开只写在一个临时目录里，用完就删。仓库里的 `.gitignore` 照常生效：起始代码就是出题人
自己提交这些文件时会得到的那一份。
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.core.config import settings
from app.core.errors import GatewayUnavailableError, UnprocessableEntityError
from app.core.sentences import say

log = logging.getLogger(__name__)

#: 压缩工具顺手放进来、不属于代码的东西。
_SKIPPED_DIRS = frozenset({"__MACOSX"})
_SKIPPED_NAMES = frozenset({".DS_Store"})
#: 解得开的压缩方式：`zipfile` 只认这几种，别的（如 Deflate64）读的时候才报错。
_METHODS = frozenset(
    {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
)
#: 一层名字、一整条路径最多多少字节（UTF-8）：常见文件系统的上限。
_NAME_BYTES = 255
_PATH_BYTES = 4096
#: git 自己存东西的那一层。压缩整个仓库文件夹时会带进来，跳过；大小写不论，
#: 不区分大小写的盘上 `.GIT` 就是 `.git`。
_GIT_DIR = ".git"
#: 空文件的 blob：只为让 git 判一遍路径，不写任何内容。
_EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
#: 解开后不到这么大时不看压缩比：几个高度重复的小文件压得很小是正常的。
_RATIO_FLOOR = 64 * 1024 * 1024
#: zip 的 general purpose flag：第 0 位是加密，第 11 位是文件名按 UTF-8 存。
_ENCRYPTED = 0x1
_UTF8_NAME = 0x800
#: `create_system` 是 3 的条目在 `external_attr` 高 16 位存着 unix 的文件类型。
_UNIX = 3

#: 第一个提交的作者：平台自己，不是哪一个人。
AUTHOR_NAME = "Cheese"
AUTHOR_EMAIL = "starter@users.invalid"


@dataclass(frozen=True)
class Starter:
    """领题这一刻的起始代码：压缩包在哪、来自题目第几版、第一个提交怎么写。"""

    archive: Path
    revision: int
    message: str


@dataclass(frozen=True)
class _Entry:
    info: zipfile.ZipInfo
    path: PurePosixPath
    executable: bool


def _refuse(key: str, **params) -> UnprocessableEntityError:
    return UnprocessableEntityError(say(key, **params))


def _decoded_name(info: zipfile.ZipInfo) -> str:
    """条目的名字。没标 UTF-8 的，`zipfile` 按 cp437 解；中文系统打的包多半是 GBK，
    按 cp437 解出来是乱码，所以还原成原始字节再按 UTF-8、GBK 试一遍。"""
    if info.flag_bits & _UTF8_NAME:
        return info.orig_filename
    raw = info.orig_filename.encode("cp437", errors="replace")
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return info.orig_filename


def _entries(archive: zipfile.ZipFile, size: int) -> list[_Entry]:
    """压缩包里要放进仓库的每一份文件（去掉顶层文件夹之后的路径）。不合规矩的整份
    拒绝，说明是哪一项。"""
    files: list[_Entry] = []
    total = 0
    for info in archive.infolist():
        name = _decoded_name(info).replace("\\", "/")
        if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
            raise _refuse("starterUnsafeEntry", path=name)
        parts = [part for part in name.split("/") if part not in ("", ".")]
        if ".." in parts:
            raise _refuse("starterUnsafeEntry", path=name)
        if any(part.casefold() == _GIT_DIR for part in parts):
            continue
        if (
            any(len(part.encode()) > _NAME_BYTES for part in parts)
            or len(name.encode()) > _PATH_BYTES
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
        ):
            raise _refuse("starterNameInvalid", path=name)
        if info.create_system == _UNIX:
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise _refuse("starterUnsafeEntry", path=name)
        if not parts or info.is_dir():
            continue
        if _SKIPPED_DIRS.intersection(parts) or parts[-1] in _SKIPPED_NAMES:
            continue
        if info.flag_bits & _ENCRYPTED:
            raise _refuse("starterEncrypted")
        if info.compress_type not in _METHODS:
            raise _refuse("starterCompressionUnsupported", path=name)
        files.append(
            _Entry(
                info=info,
                path=PurePosixPath(*parts),
                executable=bool((info.external_attr >> 16) & 0o111),
            )
        )
        if len(files) > settings.starter_max_files:
            raise _refuse("starterTooManyFiles", max=settings.starter_max_files)
        total += info.file_size
        if total > settings.starter_max_bytes:
            raise _refuse(
                "starterTooLarge", mb=settings.starter_max_bytes // (1024 * 1024)
            )
    if not files:
        raise _refuse("starterEmpty")
    if total > _RATIO_FLOOR and total > settings.starter_max_ratio * max(size, 1):
        raise _refuse("starterCompressionRatio")
    tops = {entry.path.parts[0] for entry in files}
    if len(tops) == 1 and all(len(entry.path.parts) > 1 for entry in files):
        files = [
            _Entry(e.info, PurePosixPath(*e.path.parts[1:]), e.executable)
            for e in files
        ]
    seen: set[PurePosixPath] = set()
    for entry in files:
        # 同一个路径出现两次，或者既是文件又是另一份文件的目录，解不成一棵树。
        if entry.path in seen:
            raise _refuse("starterPathClash", path=str(entry.path))
        seen.add(entry.path)
    for entry in files:
        if any(parent in seen for parent in entry.path.parents):
            raise _refuse("starterPathClash", path=str(entry.path))
    _refuse_unless_git_accepts([str(entry.path) for entry in files])
    return files


def _refuse_unless_git_accepts(paths: list[str]) -> None:
    """每一条路径 git 都收得下：问 git 自己（`update-index` 判路径的那一套，连同
    NTFS、HFS 上 `.git` 的别名，如 `GIT~1`、`.git.`），不在这里抄一份规矩。"""
    with tempfile.TemporaryDirectory(prefix="starter-paths-") as temporary:
        env = _git_env()
        _git(Path(temporary), "init", "-q", env=env)
        result = subprocess.run(
            [
                "git",
                "-C",
                temporary,
                "-c",
                "core.protectNTFS=true",
                "-c",
                "core.protectHFS=true",
                "update-index",
                "--add",
                "--index-info",
            ],
            input="".join(f"100644 {_EMPTY_BLOB}\t{path}\n" for path in paths),
            capture_output=True,
            text=True,
            env=env,
            timeout=600,
            check=False,
        )
    for line in result.stderr.splitlines():
        if line.startswith("Ignoring path "):
            raise _refuse(
                "starterNameInvalid", path=line.removeprefix("Ignoring path ")
            )
    if result.returncode:
        log.warning("starter path check failed: %s", result.stderr.strip())
        raise GatewayUnavailableError(say("forgejoStarterPushFailed"))


def _size(source: BinaryIO) -> int:
    source.seek(0, os.SEEK_END)
    size = source.tell()
    source.seek(0)
    return size


def check(source: BinaryIO) -> int:
    """出题人上传时查一遍：能不能作为起始代码，不能就说为什么。返回里面有几份文件。

    只读目录、不解压：条目声明的大小在解开时由 `zipfile` 按声明截住，所以按声明算总量
    就够了；内容损坏（CRC 对不上）到领题时才会发现。"""
    try:
        with zipfile.ZipFile(source) as archive:
            return len(_entries(archive, _size(source)))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError) as exc:
        raise _refuse("starterNotZip") from exc


def unpack(source: BinaryIO, destination: Path) -> int:
    """把压缩包解到 `destination`（一个空目录）里。返回解出了几份文件。"""
    root = destination.resolve()
    try:
        with zipfile.ZipFile(source) as archive:
            entries = _entries(archive, _size(source))
            for entry in entries:
                target = (root / entry.path).resolve()
                if not target.is_relative_to(root) or target == root:
                    raise _refuse("starterUnsafeEntry", path=str(entry.path))
                target.parent.mkdir(parents=True, exist_ok=True)
                # 'xb'：已经在的不覆盖。上面查过没有重复的路径，撞上就是出了别的错。
                with archive.open(entry.info) as stream, target.open("xb") as out:
                    shutil.copyfileobj(stream, out, 1024 * 1024)
                target.chmod(0o755 if entry.executable else 0o644)
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        EOFError,
        ValueError,
        NotImplementedError,
    ) as exc:
        raise _refuse("starterNotZip") from exc
    return len(entries)


def _git(work: Path, *args: str, env: dict) -> str:
    result = subprocess.run(
        ["git", "-C", str(work), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )
    if result.returncode:
        # stderr 可能带着仓库地址；只进日志，不进回给人的错误。
        log.warning("starter git %s failed: %s", args[0], result.stderr.strip())
        raise GatewayUnavailableError(say("forgejoStarterPushFailed"))
    return result.stdout


def _git_env() -> dict:
    return {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
    }


def push(starter: Starter, url: str, username: str, password: str) -> None:
    """解开起始代码，作为 `main` 上唯一的一个提交推到 `url`（一个空仓库）。

    凭据放在请求头里（和 `forge_migration.push` 一样），不进地址、不进 git 配置文件。"""
    env = {
        **_git_env(),
        "GIT_AUTHOR_NAME": AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": AUTHOR_NAME,
        "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": "Authorization: Basic "
        + base64.b64encode(f"{username}:{password}".encode()).decode(),
    }
    with tempfile.TemporaryDirectory(prefix="starter-") as temporary:
        work = Path(temporary) / "work"
        work.mkdir()
        # The repository itself says whether `main` is there already (an
        # earlier attempt pushed and lost its answer); the forge's API learns
        # of a push only some time after it.
        if _git(work, "ls-remote", url, "refs/heads/main", env=env).strip():
            return
        with starter.archive.open("rb") as source:
            unpack(source, work)
        _git(work, "init", "-q", "--initial-branch=main", env=env)
        _git(work, "add", "-A", env=env)
        # 压缩包里的文件全被它自己的 .gitignore 挡掉时，仍然要有一个提交：仓库得有 main。
        _git(work, "commit", "-q", "--allow-empty", "-m", starter.message, env=env)
        _git(work, "push", "-q", url, "HEAD:refs/heads/main", env=env)
