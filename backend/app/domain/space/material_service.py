"""一块题目板的共用资料库 (#944)：板子上的文件、两档可见性、三条判据。

三条判据，各自一句话：

- **谁能传、谁能删、谁能改档**：这块板的所有者 / 管理员（``is_space_admin``）。
  资料库是板子级的公共设施，往里面放什么、放给谁看，是管理动作。
- **谁能看到清单**：这块板的**成员**都能看 —— 但只看到「所有成员」那一档；
  「仅管理员」那一档的行对成员**根本不出现**（不是出现了点不开）。看不见板子的
  人拿 404，不是 403：一块你不在的板子不该被确认存在，与
  ``routes/spaces.py:_ensure_space_visible`` 同一个口径。
- **谁能拿到字节**：看得见那一档的人。管理员两档都能下；成员只能下「所有成员」
  档。字节一律从 ``GET /spaces/{id}/materials/{mid}/download`` 过，**响应里从不
  给出素材的 ``url``** —— 那是 ``/uploads/...`` 下一条公开可猜的路径（见
  ``routes/uploads.py`` 顶部），一旦交出去，「仅管理员」这一档就只剩一个标签。

**为什么挂 ``material`` 而不是 ``attachment``**：教学配置里的 ``material_ids``
（``app.domain.task.protocol.Teaching``）references 的正是 ``material`` 的 id，
题目上勾选参考资料时把这里的 ``material_id`` 直接写进去，中间不必做 id 翻译；
而 ``attachment`` 那边另有一套归属判据，两张表混用会让两套判据在同一个 where 里
互相污染。

**为什么还要给通用读门加一道闩**：``GET /materials/{id}`` 今天只要求登录，
返回体里带着 ``url``。把某份素材放进「仅管理员」档之后，那道门仍然会把它的公开
路径发出去 —— 档位就是假的。所以 ``may_read_outside_space`` 挂在通用读门上：
只对**真的进了「仅管理员」档**的素材说不，其余素材一个字不变（调用处见
``routes/materials.py``）。
"""

import io
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.space_access import is_space_admin
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.core.storage import StorageBackend, generate_storage_key
from app.domain.materials.models import Material
from app.domain.materials.services import MaterialService
from app.domain.space.models import SpaceMaterial, SpaceMaterialVisibility
from app.domain.space.repositories import SpaceRepository

_MIME_FAMILIES = (
    ("image/", "image"),
    ("video/", "video"),
    ("audio/", "audio"),
)


def _type_for(mime: str) -> str:
    """从 MIME 认文件大类 —— 和 ``POST /materials`` 的入参同一个集合。

    这里**不让用户选**：一个老师在「上传资料」对话框里没法预判「图片 / 视频 / 音频 /
    文件」这个分类跟他要传的东西有什么关系，选错了只是多一个 422。MIME 本来就是
    文件自己带的答案。
    """
    for prefix, kind in _MIME_FAMILIES:
        if mime.startswith(prefix):
            return kind
    return "file"


def _parse_visibility(value: str | None) -> str:
    allowed = {v.value for v in SpaceMaterialVisibility}
    if value is None:
        return SpaceMaterialVisibility.MEMBERS.value
    if value not in allowed:
        raise BadRequestError(
            f"Unknown visibility: {value}", data={"visibility": value}
        )
    return value


class SpaceMaterialRepository:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def list_live(self, *, space_id: int) -> list[SpaceMaterial]:
        stmt = (
            select(SpaceMaterial)
            .where(
                SpaceMaterial.space_id == space_id,
                SpaceMaterial.deleted_at.is_(None),
            )
            .order_by(SpaceMaterial.id.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_live(
        self, *, space_id: int, material_id: int
    ) -> SpaceMaterial | None:
        stmt = select(SpaceMaterial).where(
            SpaceMaterial.space_id == space_id,
            SpaceMaterial.material_id == material_id,
            SpaceMaterial.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_live_by_material_ids(
        self, *, material_ids: Sequence[int]
    ) -> list[SpaceMaterial]:
        if not material_ids:
            return []
        stmt = select(SpaceMaterial).where(
            SpaceMaterial.material_id.in_(list(material_ids)),
            SpaceMaterial.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self, *, space_id: int, material_id: int, visibility: str
    ) -> SpaceMaterial:
        now = datetime.now(UTC)
        link = SpaceMaterial(
            space_id=space_id,
            material_id=material_id,
            visibility=visibility,
            download_count=0,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def save(self, link: SpaceMaterial) -> SpaceMaterial:
        link.updated_at = datetime.now(UTC)
        await self._session.flush()
        return link

    async def soft_delete(self, link: SpaceMaterial) -> None:
        now = datetime.now(UTC)
        link.deleted_at = now
        link.updated_at = now
        await self._session.flush()


class SpaceMaterialService:
    def __init__(self, *, session: AsyncSession, storage: StorageBackend) -> None:
        self._session = session
        self._storage = storage
        self._links = SpaceMaterialRepository(session=session)
        self._spaces = SpaceRepository(session)
        # 素材那一域的行一律经它的 service 拿，不自己摸它的 repository ——
        # ``test_domain_import_guard.py`` 拦的就是那一条。
        self._materials = MaterialService.for_lookup(session)

    # ---- 看 ----

    async def list_for_space(self, *, space_id: int, user_id: int) -> dict:
        """看得见这块板就能拿到清单，但「仅管理员」那一档只发给管理员。

        响应里带上 ``canManage`` —— 传、改档、撤下来是同一批人（板子的管理员），
        界面要拿它决定摆不摆那几个入口。由服务端说而不是让界面自己去猜：能管的
        判据就在下面这一行里，抄一份到前端就是第二份会走样的规则。
        """
        is_admin = await self._require_member(space_id=space_id, user_id=user_id)
        links = await self._links.list_live(space_id=space_id)
        if not is_admin:
            links = [
                link
                for link in links
                if link.visibility == SpaceMaterialVisibility.MEMBERS.value
            ]
        return {"materials": await self._decorate(links), "canManage": is_admin}

    async def _decorate(self, links: Sequence[SpaceMaterial]) -> list[dict]:
        found = {
            material.id: material
            for material in await self._materials.get_many(
                [link.material_id for link in links]
            )
        }
        items: list[dict] = []
        for link in links:
            material = found.get(link.material_id)
            # 关联行还在、素材行没了（理论上不该发生）：跳过，不把 None 交出去。
            if material is None:
                continue
            items.append(_link_to_dto(link, material))
        return items

    async def can_download(
        self, *, space_id: int, user_id: int, link: SpaceMaterial
    ) -> bool:
        if link.visibility == SpaceMaterialVisibility.MEMBERS.value:
            return await self._spaces.is_member(space_id=space_id, user_id=user_id)
        return await is_space_admin(self._session, space_id=space_id, user_id=user_id)

    async def may_read_outside_space(self, *, material_id: int, user_id: int) -> bool:
        """这份素材在**资料库这一侧**归谁读 —— 一道挂在通用读门上的闩。

        ``GET /materials/{id}`` 只要求登录，而它返回的 ``url`` 是一条公开路径。
        素材一旦进了某块板的「仅管理员」档，那道门发出去的就等于这份文件本身。
        所以这里判：它在不在「仅管理员」档里；在，就必须是那块板的管理员。

        「所有成员」档**不在这里拦**：那条口径今天本来就是「任何登录用户都拿得到」
        （素材表只有上传者、没有归属，见 ``routes/attachments.py`` 顶部记的同一个
        缺口），本模块不假装把那条修好了。同一份素材挂在两块板上时按最宽的那道
        放行 —— 有一块板对所有人公开，另一块再怎么锁也锁不住它。

        非管理员那一半判据只有一份：``_readable_by_a_member``（见它的说明），这里
        只是把「要么本就人人可读、要么这人正好是某块相关板子的管理员」两句话说
        完，判据本身与 ``member_readable_material_ids`` 共用，不各写一份。
        """
        links = await self._links.list_live_by_material_ids(material_ids=[material_id])
        if _readable_by_a_member([link.visibility for link in links]):
            return True
        for link in links:
            if await is_space_admin(
                self._session, space_id=link.space_id, user_id=user_id
            ):
                return True
        return False

    async def readable_material_ids(
        self, *, material_ids: Sequence[int], user_id: int
    ) -> set[int]:
        """这一批素材里，这个人在资料库这一侧读得到的是哪几个。

        与 ``may_read_outside_space`` 是**同一句判据**，不多抄一份：一个素材包一次
        带十几份，包一层循环只为让调用方少写一个 for。看不见的 id 从结果里消失，
        调用方照它过滤（见 ``routes/materialbundles.py``）。
        """
        readable: set[int] = set()
        for material_id in material_ids:
            if await self.may_read_outside_space(
                material_id=material_id, user_id=user_id
            ):
                readable.add(material_id)
        return readable

    # ---- 写 ----

    async def add(
        self,
        *,
        space_id: int,
        user_id: int,
        content: bytes,
        filename: str,
        content_type: str,
        visibility: str | None,
    ) -> dict:
        await self._require_admin(space_id=space_id, user_id=user_id)
        chosen = _parse_visibility(visibility)
        kind = _type_for(content_type)

        storage_key = generate_storage_key(filename, prefix=f"materials/{kind}")
        url = await self._storage.upload(io.BytesIO(content), storage_key, content_type)

        meta: dict = {
            "size": len(content),
            "mime": content_type,
            "mimeType": content_type,
            "storageKey": storage_key,
        }
        if kind == "file":
            meta["name"] = filename
            meta["expires"] = 0

        created = await self._materials.create_material(
            type=kind,
            url=url,
            name=filename,
            uploader_id=user_id,
            meta=meta,
        )
        material_id = int(created["id"])
        link = await self._links.create(
            space_id=space_id, material_id=material_id, visibility=chosen
        )
        return _link_to_dto(link, await self._materials.material_row(material_id))

    async def set_visibility(
        self, *, space_id: int, user_id: int, material_id: int, visibility: str
    ) -> dict:
        await self._require_admin(space_id=space_id, user_id=user_id)
        link = await self._links.get_live(space_id=space_id, material_id=material_id)
        if link is None:
            raise NotFoundError("Material not found", data={"id": material_id})
        link.visibility = _parse_visibility(visibility)
        await self._links.save(link)
        return _link_to_dto(link, await self._materials.material_row(material_id))

    async def remove(self, *, space_id: int, user_id: int, material_id: int) -> None:
        """把这份素材从这块板上拿下来。

        软删这一行，``material`` 行与存储上的字节都留着：拿下来是「这块板不再列
        它」，不是「把文件毁掉」—— 同一份素材可能还被某道题的参考资料引用着，而
        对象删掉就没有回头路。
        """
        await self._require_admin(space_id=space_id, user_id=user_id)
        link = await self._links.get_live(space_id=space_id, material_id=material_id)
        if link is None:
            raise NotFoundError("Material not found", data={"id": material_id})
        await self._links.soft_delete(link)

    async def download(
        self, *, space_id: int, user_id: int, material_id: int
    ) -> tuple[bytes, str, str]:
        await self._require_member(space_id=space_id, user_id=user_id)
        link = await self._links.get_live(space_id=space_id, material_id=material_id)
        if link is None:
            raise NotFoundError("Material not found", data={"id": material_id})
        if not await self.can_download(space_id=space_id, user_id=user_id, link=link):
            raise ForbiddenError(
                "This material is only visible to the board's managers"
            )

        material = await self._materials.material_row(material_id)
        meta = material.meta or {}
        storage_key = meta.get("storageKey")
        if not storage_key:
            raise NotFoundError("Material storage key not found")
        content = await self._storage.download(storage_key)
        if content is None:
            raise NotFoundError("Material file not found in storage")

        # 计数在取到字节之后才加：文件取不出来时不该记一笔「有人下载过」。
        link.download_count += 1
        await self._links.save(link)

        return content, material.name, meta.get("mime") or "application/octet-stream"

    # ---- 判据 ----

    async def _require_member(self, *, space_id: int, user_id: int) -> bool:
        """看不见这块板就 404（不是 403）；返回的是不是管理员。"""
        if not await self._spaces.is_member(space_id=space_id, user_id=user_id):
            raise NotFoundError(
                "Resource space not found", data={"type": "space", "id": space_id}
            )
        return await is_space_admin(self._session, space_id=space_id, user_id=user_id)

    async def _require_admin(self, *, space_id: int, user_id: int) -> None:
        if not await self._require_member(space_id=space_id, user_id=user_id):
            raise ForbiddenError("Only a board manager can perform this action")


def _readable_by_a_member(visibilities: Sequence[str]) -> bool:
    """一份素材「普通成员读得到吗」—— 这条判据**只在这里写一遍**。

    给它这份素材**当前活着的**板内关联行的档位集合，它回答非管理员那一半：
    一条关联都没有（``material`` 行没人挂过）→ 是，它就是一份普通素材，今天
    任何登录用户都读得到（这条口径本模块不改，见 ``may_read_outside_space``）；
    有至少一条「所有成员」档 → 是，最宽的那道说了算；有行但没有一条是「所有
    成员」档 → 否，全是「仅管理员」档。管理员那一层不归它管，由
    ``may_read_outside_space`` 叠在上面。

    ``may_read_outside_space``（一个人）与 ``member_readable_material_ids``
    （一批 id，「有人的话最宽能到哪」）都走这一个函数，所以两处不可能对同一行
    档位给出不同的答案。
    """
    if not visibilities:
        return True
    return SpaceMaterialVisibility.MEMBERS.value in visibilities


async def member_readable_material_ids(
    session: AsyncSession, *, material_ids: Sequence[int]
) -> set[int]:
    """这批素材里，一块板的**普通成员**能读到的是哪几个 —— 不问具体是谁。

    与 ``may_read_outside_space`` 是同一句判据的另一半：那边问「这个人」，
    这边问「哪怕是普通成员，最宽能读到哪些」。教学配置里的 ``material_ids``
    正是要按它过滤（``app.domain.task.teaching.for_project``）：一份课件只在
    「仅管理员」档里，就不该出现在任何普通成员的 agent 开场上下文里 —— 它带着的
    ``url`` 是一条公开可猜的 ``/uploads/...`` 路径，发出去等于把文件发出去。

    判据只有一份（``_readable_by_a_member``），这里只负责按 ``material_id`` 把
    活着的关联行归堆，然后逐 id 问那一句。返回的是**能读的 id 集合**；调用方照它
    过滤，看不见的 id 从结果里消失。没有任何关联行的 id 也算能读（普通素材）。
    """
    links = await SpaceMaterialRepository(session=session).list_live_by_material_ids(
        material_ids=material_ids
    )
    by_material: dict[int, list[str]] = {}
    for link in links:
        by_material.setdefault(link.material_id, []).append(link.visibility)
    return {
        material_id
        for material_id in material_ids
        if _readable_by_a_member(by_material.get(material_id, []))
    }


def _link_to_dto(link: SpaceMaterial, material: Material) -> dict:
    """**不给 ``url``** —— 见本模块开头「谁能拿到字节」那一条。"""
    meta = material.meta or {}
    size = meta.get("size")
    return {
        "id": material.id,
        "name": material.name,
        "type": material.type,
        "visibility": link.visibility,
        "size": size if isinstance(size, int) else None,
        "mime": meta.get("mime") or meta.get("mimeType"),
        "uploaderId": material.uploader_id,
        "createdAt": int(link.created_at.timestamp() * 1000),
        "downloadCount": link.download_count,
    }
