"""资料库与引用型产物 —— 项目里不在任何 git 树上的那一半文件。

拆出来的理由是结论 49 / 不变量 I21b：被托管的仓库只装 agent 替用户做的活。用户
给项目的资料、贴进房间的截图、发布出来的预览产物都不是那个活的源，所以它们既不
进项目的 git 树，也不落在检出目录里——它们住在平台侧，和项目那唯一一个 git 源
（:mod:`app.domain.repository.service`）互不相干。

两处，两种寻址：

- 资料库项目级、按原名寻址、只读。「上周那份预算表」这句话里名字就是身份，所以
  这里不放随机串；名字里的 `/` 是文件夹。清单和名字在记录表里，字节按每一行自己
  的键存放（:mod:`app.domain.library.records`、:mod:`app.core.blob_store`），
  这里只管名字长什么样。
- 房间文件（`.room-files/<project>/<room>/`）只属于一个房间：贴进来的截图没有名
  字，`image.png` 是浏览器替它编的，它只属于那条消息。发布出来的预览产物同理。

一份资料在消息里、在字节端点的 query 上都是同一个地址 `library/<名字>`——**不拷贝**。
送上机器的那一份是另一回事：它落在会话 home 的 `attachments/` 下
（:mod:`app.domain.agent.place`），不落在检出目录里，agent 拿到的是那台机器报回来
的绝对路径。
"""

import hashlib
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from stat import S_ISREG
from typing import BinaryIO

from app.core import video_files
from app.core.config import settings
from app.core.errors import (
    NotFoundError,
    UnprocessableEntityError,
    ValidationError,
)
from app.core.sentences import say, size_text
from app.domain.textfile import text_payload


def _safe_path(root: Path, rel: str) -> Path:
    """`rel` 解出来在 `root` 里面。

    和仓库那一侧的同名检查不是同一条规则：那边还要挡 `.git`，因为那边的根是一棵
    git 树。这两个根里没有 git，所以这里只有包含关系这一条。
    """
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        raise ValidationError("path escapes the project workspace")
    return target


def room_files_root(project_id: uuid.UUID, room_id: uuid.UUID) -> Path:
    """Room attachments and published previews have no code branch."""
    root = (
        Path(settings.workspace_root) / ".room-files" / str(project_id) / str(room_id)
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


#: 一条消息的附件（贴进来的图、干活机器上摆出来的图）用的地址头。它们没有自己的名字，
#: 房间里给它们的是一串随机目录名加原来的文件名。
MESSAGE_ATTACHMENT_PREFIX = "uploads"


def is_message_attachment(path: str) -> bool:
    """这份东西是不是一条消息的附件 —— 认它的地址头，不看字节在哪。

    附件和房间文件住在同一棵树里（那条消息读它，按地址读，读得到就行），但它不是
    这个房间的文件：清单列的是房间里有哪些东西，而它属于那条消息。
    """
    return path.split("/", 1)[0] == MESSAGE_ATTACHMENT_PREFIX


#: 一份文件落地时的字节上限。房间文件（:func:`write_room_file`）与资料库
#: （:mod:`app.domain.library.records`）落的是同一批字节，用同一条规则
#: （:func:`max_bytes_for`）；路由那一层的 ``MAX_LIBRARY_UPLOAD_BYTES`` 也指回这里，
#: 别在别处再写一遍。
#:
#: 这道上限不是「上传那一层」的事：``client_max_body_size`` 只在网关后面那一条路
#: 上（``frontend/nginx.conf``），而 ``main.py`` 的 OpenAPI ``servers`` 把「直连后
#: 端端口、前面没有网关」列为合法入口 —— 那条路上没有这道门。
#:
#: ``: int`` 是给类型检查看的：不标的话 pyright 把全大写的常量收成
#: ``Literal[10485760]``，而 ``project.ports.FileStore`` 要一个可变的 ``int``，
#: 那边就收不下这个模块了。
MAX_FILE_BYTES: int = 10 * 1024 * 1024

#: 交出去的一份产物（artifact）多大算太大。和 :data:`MAX_FILE_BYTES` 是同一条线：
#: 两边都是「一个人会在这个房间里打开的东西」—— 一份大到贴不进聊天的报告，也大到
#: 不该被当成交付物摆出来。它们落在同一棵树里（``.room-files/`` 与 ``.artifacts/``），
#: 所以这个数只在这里写一遍，路由读它。
MAX_ARTIFACT_BYTES = MAX_FILE_BYTES

#: 一段视频的上限。手机录屏是拿来报问题的，30–60 秒的 iPhone 录屏常常有几十到一百
#: 多 MB，10MB 装不下一段。这个数和网关上传附件那一条路的 ``client_max_body_size``
#: 是一对（``frontend/nginx.conf``、``deploy/llm-tunnel/``），改一个就改另一个。
MAX_VIDEO_BYTES = 200 * 1024 * 1024

#: 落地的字节：整份在内存里，或者一个读得到、能倒回开头的文件（上传时 multipart
#: 已经落在临时文件里的那一份，不必再整份读进内存）。
Content = bytes | BinaryIO

_CHUNK = 1024 * 1024


def max_bytes_for(name: str | None) -> int:
    """叫这个名字的一份文件最多多大：视频按 :data:`MAX_VIDEO_BYTES`，别的都是
    :data:`MAX_FILE_BYTES`。没有名字的（一版历史）按后者。"""
    suffix = PurePosixPath(name or "").suffix.lower()
    if suffix in video_files.SUFFIX_MEDIA_TYPE:
        return MAX_VIDEO_BYTES
    return MAX_FILE_BYTES


def refuse_oversize(
    size: int, name: str | None = None, limit: int | None = None
) -> None:
    """超过这一类文件上限的字节不落地，报「超限」而不是别的，带着那一类的上限。

    房间文件和资料库的每一个字节都从这里过，所以上限只有一处、也只有一层：调用方
    漏判也好、直连后端绕开网关也好，字节都写不进去。``limit`` 是另有上限的地方
    （团队的资料库）自己的那一条。
    """
    limit = max_bytes_for(name) if limit is None else limit
    if size > limit:
        raise UnprocessableEntityError(say("fileTooLarge", size=size_text(limit)))


def size_of(content: Content) -> int:
    """多少字节；文件看它的结尾在哪，一个字节也不读。"""
    if isinstance(content, bytes):
        return len(content)
    content.seek(0, 2)
    size = content.tell()
    content.seek(0)
    return size


def measure(content: Content) -> tuple[int, str]:
    """多少字节、sha256 是多少。文件读一遍之后倒回开头，留给写的那一步。"""
    if isinstance(content, bytes):
        return len(content), hashlib.sha256(content).hexdigest()
    content.seek(0)
    digest = hashlib.sha256()
    size = 0
    while chunk := content.read(_CHUNK):
        digest.update(chunk)
        size += len(chunk)
    content.seek(0)
    return size, digest.hexdigest()


def write_content(target: Path, content: Content) -> None:
    """把字节写到 `target`；文件按块拷，不整份进内存。"""
    if isinstance(content, bytes):
        target.write_bytes(content)
        return
    content.seek(0)
    with target.open("wb") as out:
        shutil.copyfileobj(content, out, _CHUNK)


def write_room_file(
    project_id: uuid.UUID, room_id: uuid.UUID, path: str, data: Content
) -> None:
    refuse_oversize(size_of(data), path)
    if path.split("/")[0] == LIBRARY_PREFIX:
        # `library/…` 是资料库那一份的地址（见 `read_attachment`）。房间里再写一个
        # 同名的东西，读的人就会拿到房间那份、以为看的是资料库里的原件。
        raise ValidationError(say("libraryPrefixReserved", prefix=LIBRARY_PREFIX))
    target = _safe_path(room_files_root(project_id, room_id), path)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_content(target, data)


def room_file_path(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> Path:
    """这个房间里这一份文件在磁盘上的位置；没有这份文件就拒。"""
    target = _safe_path(room_files_root(project_id, room_id), path)
    if not target.is_file():
        raise ValidationError("file not found")
    return target


def read_room_file(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> bytes:
    return room_file_path(project_id, room_id, path).read_bytes()


def read_room_text_file(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> dict:
    return text_payload(_safe_path(room_files_root(project_id, room_id), path), path)


def room_file_exists(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> bool:
    return _safe_path(room_files_root(project_id, room_id), path).is_file()


#: 一次列出多少份房间文件。频道文件区是几十份的量级（一个人往里放的东西、芝士交付
#: 的东西、定时任务的结果）；这个数只挡一份失控的目录——目录里的路径由干活的那台机器
#: 自己写，没有别的上限。超过的那部分由调用方照 `truncated` 说出来，不闷着。
MAX_LISTED_ROOM_FILES = 500


def list_room_files(
    project_id: uuid.UUID, room_id: uuid.UUID
) -> tuple[list[dict], int]:
    """这个房间自己的文件：路径、大小、最后一次改动的时间，最近改的在最前。

    房间文件此前只有按路径取一份的读法（:func:`read_room_file`）：一个人想知道这个
    房间的文件区里有什么，得先知道那份文件叫什么。这份清单是那个目录本身——定时任务
    放进来的结果、芝士交付的文档、有人放进来的文件都在里面。

    一条消息的附件不在里面（:func:`is_message_attachment`）：它们和房间文件住在同一
    棵树里，但属于那条消息，按地址读得到就够了，页面上不再占一格、也不再挤掉一格。

    交出 ``(清单, 找到的总数)``；总数超过上限时清单是最近改的那些，调用方据此报
    「只列出了一部分」。
    """
    root = room_files_root(project_id, room_id)
    found: list[tuple[float, dict]] = []
    for entry in root.rglob("*"):
        relative = entry.relative_to(root)
        # 点开头的层是编辑器留下的临时文件和别的东西：它们没有消息引得到（
        # :func:`read_preview_file` 那一侧也拒绝这种地址），占一格只会让人多点一次。
        if any(part.startswith(".") for part in relative.parts):
            continue
        if is_message_attachment(relative.as_posix()):
            continue
        try:
            info = entry.stat()
        except OSError:
            continue
        if not S_ISREG(info.st_mode):
            continue
        found.append(
            (
                info.st_mtime,
                {
                    "path": relative.as_posix(),
                    "bytes": info.st_size,
                    "modified_at": datetime.fromtimestamp(
                        info.st_mtime, UTC
                    ).isoformat(),
                },
            )
        )
    found.sort(key=lambda item: (-item[0], item[1]["path"]))
    return [entry for _, entry in found[:MAX_LISTED_ROOM_FILES]], len(found)


def _revision_root(project_id: uuid.UUID, room_id: uuid.UUID) -> Path:
    """Where a room's saved states live, by content hash — beside the room
    files and never inside them, so no room path can name one."""
    root = (
        Path(settings.workspace_root)
        / ".room-file-history"
        / str(project_id)
        / str(room_id)
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def write_revision_blob(project_id: uuid.UUID, room_id: uuid.UUID, data: bytes) -> str:
    """Keep one exact content under its hash — and never past the ceiling.

    A revision is read back as a file, so a byte that could not have gone into
    the room must not land here either: this is where the history's bytes land,
    so the ceiling is checked here too (:func:`refuse_oversize`).
    """
    refuse_oversize(len(data))
    digest = hashlib.sha256(data).hexdigest()
    target = _revision_root(project_id, room_id) / digest
    if not target.is_file():
        temporary = target.with_name(f"{digest}.{uuid.uuid4().hex}")
        temporary.write_bytes(data)
        temporary.replace(target)
    return digest


def read_revision_blob(project_id: uuid.UUID, room_id: uuid.UUID, digest: str) -> bytes:
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
        raise ValidationError(say("libraryNotARevision"))
    target = _revision_root(project_id, room_id) / digest
    if not target.is_file():
        raise NotFoundError(say("revisionContentGone"))
    return target.read_bytes()


# 带着房间走的那种地址（`uploads/<随机串>/<名字>`）只属于贴进来的那一份：它没有
# 名字，也就没有第二个房间会引用它。

LIBRARY_PREFIX = "library"


def library_ref(name: str) -> str:
    """资料库里那一份在消息和工作目录里的地址。"""
    return f"{LIBRARY_PREFIX}/{name}"


def library_name(path: str) -> str | None:
    """这个地址指的是资料库里哪一份,不是的话给 None。"""
    prefix = f"{LIBRARY_PREFIX}/"
    return path[len(prefix) :] if path.startswith(prefix) else None


def next_name(name: str, attempt: int) -> str:
    """撞名时的第 `attempt` 个名字：`预算表(2).xlsx`。编号加在最后一层上，
    文件夹不跟着编号。"""
    if attempt == 1:
        return name
    folder, slash, leaf = name.rpartition("/")
    stem, dot, ext = leaf.rpartition(".")
    numbered = f"{stem}({attempt}).{ext}" if dot else f"{leaf}({attempt})"
    return f"{folder}{slash}{numbered}"


#: 记录表里 `name` 那一列多长。
MAX_LIBRARY_NAME = 512


def clean_library_path(raw: str | None) -> str:
    """人给的一个资料库名字（改名的目标、要放进去的文件夹），每一层都得是个名字。

    和 `clean_upload_name` 不是一回事：那边从一个上传的文件名里只留最后一层，这边
    整条路径都是人有意写下的，所以一层不合规就拒绝，不替人改。"""
    parts = [part.strip() for part in (raw or "").strip().strip("/").split("/")]
    if not parts or any(
        not part
        or part.startswith(".")
        or re.search(r"[\x00-\x1f\x7f\\]", part)
        or len(part.encode("utf-8")) > 180
        for part in parts
    ):
        raise ValidationError(say("libraryBadName"))
    name = "/".join(parts)
    if len(name) > MAX_LIBRARY_NAME:
        raise ValidationError(say("libraryBadName"))
    return name


def clean_upload_name(filename: str | None) -> str:
    """一份上传的文件在资料库里叫什么：它自己的名字，去掉路径和控制字符。"""
    name = (filename or "file").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name).strip().strip(".") or "file"
    return name.encode("utf-8")[:180].decode("utf-8", errors="ignore")


def artifact_snapshot_path(
    project_id: uuid.UUID, card_id: uuid.UUID, name: str
) -> Path:
    """这一版交出去的那一份的位置 (#1085 结论五)。

    一版是一次交付，一次交付就是一张采纳了的卡，所以快照按卡分目录：同一项产物的
    七版互不覆盖，而撤回采纳只改卡的状态、不动字节。

    在资料库旁边（`.library/` / `.artifacts/`），不在 git 里：成品是从源构建出来
    的，进库就是把五十版 20MB 的幻灯片提交进仓库的那条老路。名字只取最后一段——
    交付物的地址是「哪一版的那一份」，它在工作目录里的哪个子目录不是它的身份。
    """
    leaf = Path(name).name
    if not leaf or leaf in {".", ".."}:
        raise ValidationError(say("libraryNotAFileName", name=name))
    root = Path(settings.workspace_root) / ".artifacts" / str(project_id)
    return (root / str(card_id) / leaf).resolve()


def write_artifact_snapshot(
    project_id: uuid.UUID, card_id: uuid.UUID, name: str, data: bytes
) -> None:
    target = artifact_snapshot_path(project_id, card_id, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def artifact_snapshot_size(
    project_id: uuid.UUID, card_id: uuid.UUID, name: str
) -> int | None:
    """这一版留存的那一份有多大；没有留存时给 None。"""
    try:
        return artifact_snapshot_path(project_id, card_id, name).stat().st_size
    except (OSError, ValidationError):
        return None


def read_artifact_snapshot(
    project_id: uuid.UUID, card_id: uuid.UUID, name: str
) -> bytes:
    target = artifact_snapshot_path(project_id, card_id, name)
    if not target.is_file():
        # 交付物落地之前递的那些卡：清单上有这一版，字节从来没有过。说清是哪一
        # 种，别让它读起来像文件丢了。
        raise NotFoundError(say("artifactVersionNoFile"))
    return target.read_bytes()


def read_preview_file(
    project_id: uuid.UUID, topic_id: uuid.UUID, entry: str, relative: str
) -> bytes:
    """Read web assets only inside the explicitly selected artifact's directory."""
    parts = relative.split("/")
    if not relative or any(
        not part or part.startswith(".") or "\\" in part or "\x00" in part
        for part in parts
    ):
        raise ValidationError("preview path unavailable")
    tree = room_files_root(project_id, topic_id)
    directory = _safe_path(tree, str(PurePosixPath(entry).parent))
    target = _safe_path(directory, relative)
    return read_room_file(project_id, topic_id, str(target.relative_to(tree)))


def preview_file_version(
    project_id: uuid.UUID, topic_id: uuid.UUID, entry: str
) -> str | None:
    """Track HTML edits without loading a large artifact into the editor API."""
    target = _safe_path(room_files_root(project_id, topic_id), entry)
    try:
        with target.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()[:16]
    except OSError:
        # The metadata still names a missing/unreadable artifact; the file API
        # supplies its existing detailed error state to the preview panel.
        return None
