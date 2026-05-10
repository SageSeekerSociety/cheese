from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.materials.models import Material, MaterialBundle, MaterialBundleRelation


class MaterialRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, material_id: int) -> Material | None:
        stmt: Select[tuple[Material]] = select(Material).where(Material.id == material_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        type: str,
        url: str,
        name: str,
        uploader_id: int,
        expires: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Material:
        now = datetime.now(UTC)
        material = Material(
            type=type,
            url=url,
            name=name,
            uploader_id=uploader_id,
            created_at=now,
            expires=expires,
            download_count=0,
            meta=meta or {},
        )
        self._session.add(material)
        await self._session.flush()
        return material

    async def delete(self, material_id: int) -> bool:
        material = await self.get_by_id(material_id)
        if material is None:
            return False
        await self._session.delete(material)
        await self._session.flush()
        return True

    async def increment_download_count(self, material_id: int) -> None:
        material = await self.get_by_id(material_id)
        if material:
            material.download_count += 1
            await self._session.flush()


class MaterialBundleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all_bundle_ids(
        self,
        *,
        keyword: str | None = None,
        id_gte: int | None = None,
        descending: bool = False,
    ) -> list[int]:
        stmt = select(MaterialBundle.id)
        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(MaterialBundle.title.ilike(like))
        if id_gte is not None:
            stmt = stmt.where(MaterialBundle.id >= id_gte)
        if descending:
            stmt = stmt.order_by(MaterialBundle.id.desc())
        else:
            stmt = stmt.order_by(MaterialBundle.id.asc())
        result = await self._session.execute(stmt)
        return [r[0] for r in result.all()]

    async def list_bundles(
        self,
        *,
        keyword: str | None = None,
        id_gte: int | None = None,
        limit: int,
        cursor_id: int | None = None,
        descending: bool = False,
    ) -> list[MaterialBundle]:
        stmt: Select[tuple[MaterialBundle]] = select(MaterialBundle)

        if keyword:
            like = f"%{keyword.strip()}%"
            stmt = stmt.where(MaterialBundle.title.ilike(like))

        if id_gte is not None:
            stmt = stmt.where(MaterialBundle.id >= id_gte)

        if cursor_id is not None:
            if descending:
                stmt = stmt.where(MaterialBundle.id <= cursor_id)
            else:
                stmt = stmt.where(MaterialBundle.id >= cursor_id)

        if descending:
            stmt = stmt.order_by(MaterialBundle.id.desc()).limit(limit)
        else:
            stmt = stmt.order_by(MaterialBundle.id.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, bundle_id: int) -> MaterialBundle | None:
        stmt: Select[tuple[MaterialBundle]] = select(MaterialBundle).where(
            MaterialBundle.id == bundle_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, *, title: str, content: str, creator_id: int) -> MaterialBundle:
        now = datetime.now(UTC)
        bundle = MaterialBundle(
            title=title,
            content=content,
            creator_id=creator_id,
            created_at=now,
            updated_at=now,
            rating=0,
            rating_count=0,
            my_rating=None,
            comments_count=0,
        )
        self._session.add(bundle)
        await self._session.flush()
        return bundle

    async def update(
        self,
        bundle: MaterialBundle,
        *,
        title: str | None = None,
        content: str | None = None,
    ) -> MaterialBundle:
        if title is not None:
            bundle.title = title
        if content is not None:
            bundle.content = content
        bundle.updated_at = datetime.now(UTC)
        await self._session.flush()
        return bundle

    async def delete(self, bundle_id: int) -> bool:
        bundle = await self.get_by_id(bundle_id)
        if bundle is None:
            return False
        await self._session.delete(bundle)
        await self._session.flush()
        return True

    async def get_materials_for_bundle(self, bundle_id: int) -> list[int]:
        stmt = select(MaterialBundleRelation.material_id).where(
            MaterialBundleRelation.bundle_id == bundle_id
        )
        result = await self._session.execute(stmt)
        return [row for row in result.scalars().all()]

    async def add_material_to_bundle(self, *, bundle_id: int, material_id: int) -> None:
        stmt = select(MaterialBundleRelation).where(
            MaterialBundleRelation.bundle_id == bundle_id,
            MaterialBundleRelation.material_id == material_id,
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            return
        rel = MaterialBundleRelation(bundle_id=bundle_id, material_id=material_id)
        self._session.add(rel)
        await self._session.flush()

    async def remove_material_from_bundle(self, *, bundle_id: int, material_id: int) -> bool:
        stmt = delete(MaterialBundleRelation).where(
            MaterialBundleRelation.bundle_id == bundle_id,
            MaterialBundleRelation.material_id == material_id,
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount > 0
