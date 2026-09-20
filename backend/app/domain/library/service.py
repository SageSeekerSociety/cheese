"""资料库与引用型产物 —— 项目里不在任何 git 树上的那一半文件。

拆出来的理由是结论 49 / 不变量 I21b：被托管的仓库只装 agent 替用户做的活。用户
给项目的资料、贴进房间的截图、发布出来的预览产物都不是那个活的源，所以它们既不
进项目的 git 树，也不落在检出目录里——它们住在平台侧，`settings.workspace_root`
下自己的两个目录（`.library/`、`.room-files/`），和项目那唯一一个 git 源
（:mod:`app.domain.repository.service`）互不相干。

两个根，两种寻址：

- 资料库（`.library/<project>/`）项目级、按原名寻址、只读。「上周那份预算表」这
  句话里名字就是身份，所以这里不放随机串。
- 房间文件（`.room-files/<project>/<room>/`）只属于一个房间：贴进来的截图没有名
  字，`image.png` 是浏览器替它编的，它只属于那条消息。发布出来的预览产物同理。

一份资料在消息里、在给 agent 的地址里、在字节端点的 query 上都是同一个地址
`library/<名字>`——**不拷贝**。
"""

import hashlib
import uuid
from pathlib import Path, PurePosixPath

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
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


def write_room_file(
    project_id: uuid.UUID, room_id: uuid.UUID, path: str, data: bytes
) -> None:
    if path.split("/")[0] == LIBRARY_PREFIX:
        # `library/…` 是资料库那一份的地址（见 `read_attachment`）。房间里再写一个
        # 同名的东西，读的人就会拿到房间那份、以为看的是资料库里的原件。
        raise ValidationError(f"{LIBRARY_PREFIX}/ 留给资料库，房间文件不能写在这里")
    target = _safe_path(room_files_root(project_id, room_id), path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def read_room_file(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> bytes:
    target = _safe_path(room_files_root(project_id, room_id), path)
    if not target.is_file():
        raise ValidationError("file not found")
    return target.read_bytes()


def read_room_text_file(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> dict:
    return text_payload(_safe_path(room_files_root(project_id, room_id), path), path)


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


def read_attachment(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> bytes:
    """一个附件的字节:资料库里那一份,或者只属于这个房间的那一份。"""
    name = library_name(path)
    if name is not None:
        return read_library_file(project_id, name)
    return read_room_file(project_id, room_id, path)


def read_attachment_text(project_id: uuid.UUID, room_id: uuid.UUID, path: str) -> dict:
    """同一个地址，读成文本(二进制的那一份照旧只回元数据和版本)。"""
    name = library_name(path)
    if name is not None:
        target = _safe_path(library_root(project_id), name)
        if not target.is_file():
            # 一条旧消息里的引用，而那份资料已经被扔掉了。说清是哪一种打不开：这个
            # 地址没错，是东西不在了。
            raise ValidationError("这份资料已经不在资料库里")
        return text_payload(target, path)
    return read_room_text_file(project_id, room_id, path)


def library_root(project_id: uuid.UUID) -> Path:
    root = Path(settings.workspace_root) / ".library" / str(project_id)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _next_name(name: str, attempt: int) -> str:
    if attempt == 1:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return f"{name}({attempt})"
    return f"{stem}({attempt}).{ext}"


def write_library_file(project_id: uuid.UUID, name: str, data: bytes) -> str:
    """Keep the name the user gave it; a taken name takes the next `(n)`.

    Allocating the name IS the write (`open(..., "xb")`): two uploads of the
    same name in flight is the case this exists for, and check-then-write loses
    one of them. Returns the name it ended up with."""
    root = library_root(project_id)
    for attempt in range(1, 1000):
        candidate = _next_name(name, attempt)
        target = _safe_path(root, candidate)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as sink:
                sink.write(data)
        except FileExistsError:
            continue
        return candidate
    raise ValidationError(f"同名文件太多：{name}")


def read_library_file(project_id: uuid.UUID, path: str) -> bytes:
    target = _safe_path(library_root(project_id), path)
    if not target.is_file():
        raise NotFoundError("资料库里没有这份文件")
    return target.read_bytes()


def delete_library_file(project_id: uuid.UUID, name: str) -> None:
    """扔掉一份资料。

    旧消息里引用它的那枚 chip 随之打不开了，这是对的：那条引用指的就是这一份，而
    这一份没有了——在它的位置上摆一份别的东西，才是把读者读到的内容换掉。"""
    target = _safe_path(library_root(project_id), name)
    if not target.is_file():
        raise NotFoundError("资料库里没有这份文件")
    target.unlink()


def list_library_files(project_id: uuid.UUID) -> list[dict]:
    """Newest first: the file someone just gave the project is the one they are
    about to reference."""
    root = library_root(project_id)
    files = []
    for entry in root.rglob("*"):
        if not entry.is_file():
            continue
        stat = entry.stat()
        files.append(
            {
                "path": str(entry.relative_to(root)),
                "bytes": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    files.sort(key=lambda f: f["modified"], reverse=True)
    return files


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
