"""一个文件多大为止，判在**字节落地的那一层**，不在上传那一层的门口。

上限只有一个数（``service.MAX_FILE_BYTES``，默认 10MB），落地只有三处：房间文件走
``service.write_room_file``，资料库走 ``records._put``（``add`` 和 ``replace`` 都从
这里过），房间文件的每一版历史走 ``service.write_revision_blob`` —— 历史读得回来，
所以它和房间文件本身是同一道上限。所以调用方漏判也好、绕开网关直连后端端口也好
（``main.py`` 的 OpenAPI ``servers`` 把「直连后端端口」列为合法入口，
``client_max_body_size`` 在那条路上不存在），字节一样写不进去。

入口那一侧量两件事：**先限读、再拒**（读回来的长度只到上限多一个字节，不是整份
body），以及**拒在碰服务之前** —— 两个服务桩是那个「走过去了」的哨兵，真的走到服务
就会炸。拟稿 PDF 那条还多一条：发题那道门在读 PDF 之前，所以板外的人被拒时一个字节
都还没读（它自己的注释就写着「门放在读 PDF 之前」）。

这一份量的是不需要数据库的那一半。入口那几条路由的集成测试在 ``tests/integration/``
（``test_materials.py``、``test_space_materials.py``、``test_avatars.py``、
``test_pdf_preview_publish_authz.py``、``test_attachment_upload_limits.py``）。
"""

import io
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import UploadFile

from app.api.routes import avatars, materials, spaces_materials
from app.api.routes.tasks import publish_pdf
from app.core.config import settings
from app.core.errors import BadRequestError, ForbiddenError, UnprocessableEntityError
from app.domain.library import blobs, records, service
from app.domain.library.models import LibraryFileRecord

# 比默认值小得多，但整 MB：这里量的是那条界线在哪，不是那个数是多少，而报出去的那个
# 数要能在断言里认出来。超限与「正好等于上限」都真跑一遍，所以上限压到几 MB 才发得动。
CEILING = 4 * 1024 * 1024


def _upload(content: bytes, name: str = "a.png") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=name)


def _stored(project: uuid.UUID) -> list[str]:
    """资料库存储里现在有哪些键（叶子名），按名字排。"""
    root = Path(settings.workspace_root) / ".library-blobs" / str(project)
    if not root.is_dir():
        return []
    return sorted(entry.name for entry in root.rglob("*") if entry.is_file())


class _Session:
    """只答「现在这一份」那一次查询的会话；别的语句收下，不真跑。

    形状照 ``tests/unit/test_library_replace.py`` 里那个 ``_Recorder``：``replace``
    从取锁、读现在这一份一路走到 ``_put``，中间用不上数据库的真状态。
    """

    def __init__(self, current: LibraryFileRecord) -> None:
        self.current = current
        self.added: list[LibraryFileRecord] = []

    async def execute(self, statement, params=None):
        return None

    async def scalar(self, statement):
        return self.current

    def add(self, row) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None


class _Unreachable:
    """一个不该被叫到的服务：叫到就说明这一趟没在门口被拒。"""

    async def add(self, **kwargs):
        raise AssertionError("服务被叫到了：超限应该在这之前就拒掉")

    async def create_avatar(self, **kwargs):
        raise AssertionError("服务被叫到了：这一份应该已经过了门口")


class _AvatarService:
    """头像落库那一句的替身：返回一个号，好让路由把字节写到 tmp_path 里。"""

    async def create_avatar(self, **kwargs):
        return {"avatarId": 7}


def _current_row(project: uuid.UUID, name: str) -> LibraryFileRecord:
    return LibraryFileRecord(
        id=uuid.uuid4(),
        project_id=project,
        name=name,
        bytes=2,
        sha256="0" * 64,
        location=blobs.LOCAL,
        blob_key=f".library/{project}/{name}",
    )


# --- 落地那一层 -------------------------------------------------------


def test_a_room_file_over_the_ceiling_is_refused_and_leaves_no_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(service, "MAX_FILE_BYTES", CEILING)
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    project_id, room_id = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(UnprocessableEntityError) as refused:
        service.write_room_file(project_id, room_id, "报告.bin", b"x" * (CEILING + 1))

    assert refused.value.status_code == 422
    # 报的是「超限」，不是权限；同一句话里带着上限是多大。
    assert refused.value.message.key == "fileTooLarge"
    assert refused.value.message.params == {"mb": 4}
    root = Path(tmp_path) / ".room-files" / str(project_id) / str(room_id)
    assert list(root.rglob("*")) == []


def test_a_room_file_at_the_ceiling_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """界线是「超过」，不是「到」：正好等于上限的那一份正常落地。"""
    monkeypatch.setattr(service, "MAX_FILE_BYTES", CEILING)
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    project_id, room_id = uuid.uuid4(), uuid.uuid4()

    service.write_room_file(project_id, room_id, "报告.bin", b"x" * CEILING)

    target = (
        Path(tmp_path) / ".room-files" / str(project_id) / str(room_id) / "报告.bin"
    )
    assert target.read_bytes() == b"x" * CEILING


def test_a_revision_blob_over_the_ceiling_is_refused_and_leaves_no_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """一版历史也是文件：读得回来的字节一样过这道上限。"""
    monkeypatch.setattr(service, "MAX_FILE_BYTES", CEILING)
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    project_id, room_id = uuid.uuid4(), uuid.uuid4()

    with pytest.raises(UnprocessableEntityError) as refused:
        service.write_revision_blob(project_id, room_id, b"x" * (CEILING + 1))

    assert refused.value.status_code == 422
    assert refused.value.message.key == "fileTooLarge"
    root = Path(tmp_path) / ".room-file-history" / str(project_id) / str(room_id)
    assert list(root.rglob("*")) == []


def test_a_revision_blob_at_the_ceiling_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """界线也是「超过」：正好等于上限的那一版正常留下。"""
    monkeypatch.setattr(service, "MAX_FILE_BYTES", CEILING)
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    project_id, room_id = uuid.uuid4(), uuid.uuid4()

    digest = service.write_revision_blob(project_id, room_id, b"x" * CEILING)

    target = (
        Path(tmp_path) / ".room-file-history" / str(project_id) / str(room_id) / digest
    )
    assert target.read_bytes() == b"x" * CEILING


async def test_a_library_replace_over_the_ceiling_is_refused_before_bytes_are_stored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """换一份超限的资料进去：报超限（不是权限），存储里一个字节都不留。

    存量里已经有一份超过上限的旧文件、要把它改掉时，这一条也管住：新的字节一样过不
    了这道闸。
    """
    monkeypatch.setattr(service, "MAX_FILE_BYTES", CEILING)
    monkeypatch.setattr(settings, "workspace_root", str(tmp_path))
    project = uuid.uuid4()
    session = _Session(_current_row(project, "预算.xlsx"))

    with pytest.raises(UnprocessableEntityError) as refused:
        await records.replace(
            session,
            project_id=project,
            name="预算.xlsx",
            data=b"x" * (CEILING + 1),
            by="user-1",
        )

    assert refused.value.status_code == 422
    assert refused.value.message.key == "fileTooLarge"
    assert _stored(project) == []


# --- 上传入口那一层 ---------------------------------------------------


async def test_a_material_over_the_ceiling_is_refused_before_the_service(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(settings, "attachment_max_bytes", CEILING)
    file = _upload(b"x" * (CEILING * 2))

    with pytest.raises(UnprocessableEntityError) as refused:
        await materials.upload_material(
            file=file, type="image", auth_user=None, service=None
        )

    assert refused.value.message.key == "fileTooLarge"
    assert refused.value.message.params == {"mb": 4}
    # 限读：读到上限多一个字节就停，整份 body 没有进过内存。
    assert file.file.tell() == CEILING + 1


async def test_a_space_material_over_the_ceiling_is_refused_before_the_service(
    monkeypatch: pytest.MonkeyPatch,
):
    """这道 read 在 ``service.add`` 里那句 ``_require_admin`` 之前，所以它得自己先判：
    否则进不了这块板的人也能让服务器先把整份 body 收进内存。"""
    monkeypatch.setattr(settings, "attachment_max_bytes", CEILING)
    file = _upload(b"x" * (CEILING * 2))

    with pytest.raises(UnprocessableEntityError) as refused:
        await spaces_materials.upload_space_material(
            space_id=1,
            file=file,
            visibility=None,
            auth_user=None,
            service=_Unreachable(),
        )

    assert refused.value.message.key == "fileTooLarge"
    assert file.file.tell() == CEILING + 1


async def test_an_avatar_over_the_ceiling_is_refused(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "attachment_max_bytes", CEILING)
    file = _upload(b"\x89PNG\r\n\x1a\n" + b"x" * (CEILING * 2))

    with pytest.raises(UnprocessableEntityError) as refused:
        await avatars.create_avatar(avatar=file, auth_user=None, service=None)

    assert refused.value.message.key == "fileTooLarge"
    assert file.file.tell() == CEILING + 1


async def test_an_avatar_under_the_ceiling_gets_all_the_way_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """头像存的是原样字节，出站才 sniff：限读不改变这一点。"""
    monkeypatch.setattr(avatars, "AVATAR_STORAGE_DIR", str(tmp_path))
    for content in (
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01",
        b"GIF89a" + b"\x00" * 8,
        b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8,
    ):
        result = await avatars.create_avatar(
            avatar=_upload(content), auth_user=None, service=_AvatarService()
        )

        assert result["code"] == 201
        assert (tmp_path / "7").read_bytes() == content


# --- 拟稿 PDF 那条 -----------------------------------------------------


class _SpaceRepo:
    """板那一次查询的替身：这一段只判「板在不在」，给个不是 None 的就够。"""

    def __init__(self, session) -> None:
        pass

    async def get_by_id(self, space_id: int):
        return object()


async def _preview(file: UploadFile):
    # 门要用到调用者的 id；别的两个依赖走到那一步之前都用不上。
    caller = SimpleNamespace(user_id=1)
    return await publish_pdf.preview_task_from_pdf(
        space_id=1, pdf_file=file, db=None, auth_user=caller, draft_service=None
    )


async def test_a_pdf_from_outside_the_board_costs_nothing_to_refuse(
    monkeypatch: pytest.MonkeyPatch,
):
    """板外的人被拒时，PDF 一个字节都还没读：门在那之前。"""

    async def refuse(**kwargs) -> bool:
        return False

    monkeypatch.setattr(publish_pdf, "SpaceRepository", _SpaceRepo)
    monkeypatch.setattr(publish_pdf, "may_publish_in_space", refuse)
    file = _upload(b"%PDF-1.4\n" + b"x" * 1024, name="卷子.pdf")

    with pytest.raises(ForbiddenError):
        await _preview(file)

    assert file.file.tell() == 0


async def test_a_pdf_over_the_ceiling_is_refused_without_reading_it_all(
    monkeypatch: pytest.MonkeyPatch,
):
    """15MB 这个数不变，但判的是「读回来多了一个字节」，不是「已经分配了多少」。"""

    async def allow(**kwargs) -> bool:
        return True

    monkeypatch.setattr(publish_pdf, "SpaceRepository", _SpaceRepo)
    monkeypatch.setattr(publish_pdf, "may_publish_in_space", allow)
    monkeypatch.setattr(publish_pdf, "MAX_PDF_BYTES", CEILING)
    file = _upload(b"%PDF-1.4\n" + b"x" * (CEILING * 2), name="卷子.pdf")

    with pytest.raises(BadRequestError):
        await _preview(file)

    assert file.file.tell() == CEILING + 1
