"""起始代码 (#4767 第二节)：出题人给的一个 .zip，领题建项目时作为仓库的第一个提交。

压缩包在出题人上传时查一遍（`check`），领题后做成提交（`build`）、推上项目的仓库
（`push`）。两次用同一套规矩，所以上传时收下的一定提交得上，出题人当场就知道这份
能不能用，不必等到有人领题：

- 拒绝绝对路径、盘符、`..`；
- 只收普通文件和目录：符号链接、设备、管道一律拒绝（zip 格式没有硬链接）；
- 文件数、解开后的总大小有上限，解开后比压缩包本身大得离谱的（压缩炸弹）拒绝；
- `__MACOSX/`、`.DS_Store` 和任何一层叫 `.git`（大小写不论）的路径不要：前两样是
  压缩工具顺手塞进来的，`.git` 是压缩整个仓库文件夹时带进来的 git 自己的东西；
- 每一层名字不超过 255 字节、整条路径不超过 4096 字节，不含控制字符；其余 git 不收的
  路径（`GIT~1` 这类 `.git` 的别名）问 git 自己，拒绝；
- 只收 `zipfile` 解得开的压缩方式；
- 整个压缩包只有一个顶层文件夹时，去掉这一层：多数人是右键压缩整个文件夹的。

提交不经过磁盘上的工作目录：每一项从压缩包直接流进 `git fast-import`，所以路径不因为
在哪个目录干活而变长，压缩包里的东西也不会落到 git 自己的对象以外。压缩包自带的
`.gitignore`（顶层和各个文件夹里的）由 git 自己判（`git check-ignore`）：起始代码就是出题人提交这个
文件夹时会得到的那一份，`.env`、`node_modules/`、构建产物不会进每个学生的仓库历史。
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import IO, BinaryIO

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
#: git 不收一条路径时，在 stderr 里说的那一行的开头（`update-index`）。
_GIT_REFUSED = "Ignoring path "
#: 压缩包里自带的忽略规则，由 git 自己判（`_not_ignored`）。
_GITIGNORE = ".gitignore"
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
    files = _not_ignored(archive, files)
    if not files:
        raise _refuse("starterAllIgnored")
    _refuse_unless_git_accepts([str(entry.path) for entry in files])
    return files


def _not_ignored(archive: zipfile.ZipFile, files: list[_Entry]) -> list[_Entry]:
    """The entries `git add -A` would commit from the unpacked tree, as far as
    the archive's own `.gitignore` files (at the top and in any folder) decide:
    a working folder zipped as it is carries its `.env`, `node_modules/` and
    build output, and the starter is what committing that folder would give.

    git decides, not a copy of its rules: only the `.gitignore` files are put
    in a scratch repository, at their places, and `git check-ignore --no-index`
    judges every path against them — an excluded folder whose files cannot be
    re-included, a byte-order mark, a file of any size, as git itself does. A
    `.gitignore` that cannot be put there is refused with its name, so the files
    it lists are never committed by default."""
    rules = [entry for entry in files if entry.path.name == _GITIGNORE]
    if not rules:
        return files
    env = _git_env()
    with tempfile.TemporaryDirectory(prefix="starter-ignore-") as temporary:
        root = Path(temporary)
        _git(root, "init", "-q", env=env)
        for entry in rules:
            stream = _read(archive.open, entry.info)
            with stream:
                content = _read(stream.read)
            try:
                target = root / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            except OSError as exc:
                raise _refuse("starterIgnoreUnreadable", path=str(entry.path)) from exc
        result = subprocess.run(
            ["git", "-C", temporary, "check-ignore", "--no-index", "--stdin", "-z"],
            input=b"".join(str(entry.path).encode() + b"\0" for entry in files),
            capture_output=True,
            env=env,
            timeout=600,
            check=False,
        )
    # 0: some are ignored; 1: none are; anything else is git failing.
    if result.returncode not in (0, 1):
        log.warning("starter ignore check failed: %s", result.stderr.decode().strip())
        raise GatewayUnavailableError(say("forgejoStarterPushFailed"))
    ignored = {path for path in result.stdout.decode().split("\0") if path}
    return [entry for entry in files if str(entry.path) not in ignored]


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
        if line.startswith(_GIT_REFUSED):
            refused = line.removeprefix(_GIT_REFUSED)
            raise _refuse("starterNameInvalid", path=refused)
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
    except _Unreadable as exc:
        raise _refuse("starterNotZip") from exc.__cause__
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError) as exc:
        raise _refuse("starterNotZip") from exc


def _quoted(path: str) -> str:
    """A path as `git fast-import` reads one: C-style quoted. Control characters
    were refused already, so only the quote and the backslash need escaping."""
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


class _Unreadable(Exception):
    """The archive's own bytes could not be read: a wrong CRC, a corrupt deflate,
    bzip2 or lzma stream, a broken local header. Whatever exception the
    decompressor raised, it is the archive's content, so no retry helps."""


def _read(read, *args):
    """Call a read of the archive; anything it raises is the archive's fault."""
    try:
        return read(*args)
    except Exception as exc:  # the decoders raise zlib.error, OSError, EOFError …
        raise _Unreadable from exc


def _stream(zipped: zipfile.ZipFile, entry: _Entry, feed: IO[bytes]) -> None:
    """One entry's bytes into ``feed``. Reading and writing fail apart: a read
    error is the archive's, a write error is the importer's."""
    stream = _read(zipped.open, entry.info)
    with stream:
        while chunk := _read(stream.read, 1024 * 1024):
            feed.write(chunk)


def build(archive: Path, message: str, when: datetime, repository: Path) -> str:
    """The starter commit, built in a new bare repository at ``repository``, with
    no file ever written: each entry streams from the archive into `git
    fast-import` as a blob, so no path is bounded by where the job runs, and
    nothing the archive holds lands on a disk outside git's own objects. The
    same archive, message and time give the same commit. Returns its id.

    An archive whose bytes cannot be read to their end (a corrupt entry, in any
    of the ways a decompressor reports one) raises the refusal that says so:
    nothing about it changes on a retry. Stopping the importer afterwards
    cannot replace that error."""
    env = _git_env()
    _git(repository.parent, "init", "-q", "--bare", str(repository), env=env)
    stamp = f"{int(when.timestamp())} +0000"
    who = f"{AUTHOR_NAME} <{AUTHOR_EMAIL}> {stamp}"
    with tempfile.TemporaryFile() as errors:
        importer = subprocess.Popen(
            ["git", "-C", str(repository), "fast-import", "--quiet"],
            stdin=subprocess.PIPE,
            stderr=errors,
            env=env,
        )
        assert importer.stdin is not None
        feed = importer.stdin
        unreadable: BaseException | None = None
        try:
            with archive.open("rb") as source, zipfile.ZipFile(source) as zipped:
                entries = _entries(zipped, _size(source))
                for mark, entry in enumerate(entries, start=1):
                    feed.write(
                        f"blob\nmark :{mark}\ndata {entry.info.file_size}\n".encode()
                    )
                    _stream(zipped, entry, feed)
                    feed.write(b"\n")
                text = message.encode()
                feed.write(
                    f"commit refs/heads/main\nauthor {who}\ncommitter {who}\n"
                    f"data {len(text)}\n".encode()
                    + text
                    + b"\n"
                )
                for mark, entry in enumerate(entries, start=1):
                    mode = "100755" if entry.executable else "100644"
                    feed.write(
                        f"M {mode} :{mark} {_quoted(str(entry.path))}\n".encode()
                    )
                feed.write(b"done\n")
        except (_Unreadable, zipfile.BadZipFile) as exc:
            # What the decoder raised, not our wrapper around it.
            unreadable = exc.__cause__ if isinstance(exc, _Unreadable) else exc
            importer.kill()
        except BaseException:
            importer.kill()
            importer.wait()
            raise
        finally:
            # A killed importer's pipe refuses the last buffered bytes; that is
            # the stop we asked for, not the error to report.
            with contextlib.suppress(OSError):
                feed.close()
        if unreadable is not None:
            importer.wait()
            raise _refuse("starterNotZip") from unreadable
        if importer.wait(timeout=600):
            errors.seek(0)
            log.warning("starter fast-import failed: %s", errors.read().decode())
            raise GatewayUnavailableError(say("forgejoStarterPushFailed"))
    return _git(repository, "rev-parse", "refs/heads/main", env=env).strip()


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


@dataclass(frozen=True)
class Remote:
    """The project's repository as git reaches it, with the account's
    credential sent as a request header (as `forge_migration.push` does), never
    in the address or a config file."""

    url: str
    username: str
    password: str

    def env(self) -> dict:
        return {
            **_git_env(),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": "Authorization: Basic "
            + base64.b64encode(f"{self.username}:{self.password}".encode()).decode(),
        }


def remote_main(remote: Remote) -> str | None:
    """The commit the repository's `main` is at, asked of the repository itself
    (the forge's API learns of a push only some time after it); None while
    there is no `main`."""
    with tempfile.TemporaryDirectory(prefix="starter-") as temporary:
        found = _git(
            Path(temporary),
            "ls-remote",
            remote.url,
            "refs/heads/main",
            env=remote.env(),
        )
    return found.split()[0] if found.strip() else None


def holds(remote: Remote, main: str, commit: str) -> bool:
    """Whether ``commit`` is on the repository's `main` (now at ``main``): fetch
    `main`'s history without file contents and ask git."""
    if main == commit:
        return True
    env = remote.env()
    with tempfile.TemporaryDirectory(prefix="starter-") as temporary:
        work = Path(temporary) / "history.git"
        _git(Path(temporary), "init", "-q", "--bare", str(work), env=env)
        _git(work, "fetch", "-q", "--filter=blob:none", remote.url, main, env=env)
        result = subprocess.run(
            ["git", "-C", str(work), "merge-base", "--is-ancestor", commit, main],
            capture_output=True,
            env=env,
            timeout=600,
            check=False,
        )
    return result.returncode == 0


def push(remote: Remote, repository: Path, commit: str) -> None:
    """Push ``commit`` as `main`. Not forced: a `main` that appeared meanwhile
    refuses it, and the next attempt finds that `main` and says so."""
    _git(
        repository,
        "push",
        "-q",
        remote.url,
        f"{commit}:refs/heads/main",
        env=remote.env(),
    )
