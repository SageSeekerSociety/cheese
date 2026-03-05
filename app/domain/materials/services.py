from typing import Any

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.materials.models import Material, MaterialBundle
from app.domain.materials.repositories import (
    MaterialRepository,
    MaterialBundleRepository,
)


def _material_to_dto(material: Material) -> dict:
    created_at_ms = int(material.created_at.timestamp() * 1000) if material.created_at else 0
    return {
        "id": material.id,
        "type": material.type,
        "url": material.url,
        "name": material.name,
        "uploaderId": material.uploader_id,
        "createdAt": created_at_ms,
        "expires": material.expires,
        "downloadCount": material.download_count,
        "meta": material.meta,
    }


def _bundle_to_dto(bundle: MaterialBundle) -> dict:
    created_at_ms = int(bundle.created_at.timestamp() * 1000) if bundle.created_at else 0
    updated_at_ms = int(bundle.updated_at.timestamp() * 1000) if bundle.updated_at else 0
    return {
        "id": bundle.id,
        "title": bundle.title,
        "content": bundle.content,
        "creatorId": bundle.creator_id,
        "createdAt": created_at_ms,
        "updatedAt": updated_at_ms,
        "rating": bundle.rating,
        "ratingCount": bundle.rating_count,
        "commentsCount": bundle.comments_count,
    }


class MaterialService:
    def __init__(self, repo: MaterialRepository) -> None:
        self._repo = repo

    async def get_material(self, material_id: int) -> dict:
        material = await self._repo.get_by_id(material_id)
        if material is None:
            raise NotFoundError("Material not found", data={"id": material_id})
        return _material_to_dto(material)

    async def create_material(
        self,
        *,
        type: str,
        url: str,
        name: str,
        uploader_id: int,
        expires: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict:
        material = await self._repo.create(
            type=type,
            url=url,
            name=name,
            uploader_id=uploader_id,
            expires=expires,
            meta=meta,
        )
        return {"id": material.id}

    async def delete_material(self, *, material_id: int, user_id: int) -> None:
        material = await self._repo.get_by_id(material_id)
        if material is None:
            raise NotFoundError("Material not found", data={"id": material_id})
        if material.uploader_id != user_id:
            raise ForbiddenError("Only the uploader can delete the material")
        await self._repo.delete(material_id)


class MaterialBundleService:
    def __init__(
        self,
        repo: MaterialBundleRepository,
        material_repo: MaterialRepository,
    ) -> None:
        self._repo = repo
        self._material_repo = material_repo

    def _parse_search_query(self, query: str | None) -> tuple[str | None, int | None]:
        if not query:
            return None, None

        title_keyword = None
        id_gte = None

        parts = query.split()
        remaining = []
        for part in parts:
            if part.startswith("title:"):
                title_keyword = part[6:]
            elif part.startswith("id:>="):
                try:
                    id_gte = int(part[5:])
                except ValueError:
                    pass
            else:
                remaining.append(part)

        if remaining and not title_keyword:
            title_keyword = " ".join(remaining)

        return title_keyword, id_gte

    async def list_bundles(
        self,
        *,
        keyword: str | None = None,
        page_start: int | None,
        page_size: int,
        sort: str | None = None,
    ) -> tuple[list[dict], dict]:
        title_keyword, id_gte = self._parse_search_query(keyword)

        descending = sort == "newest"

        all_ids = await self._repo.list_all_bundle_ids(
            keyword=title_keyword, id_gte=id_gte, descending=descending
        )

        if page_start is not None:
            try:
                start_idx = all_ids.index(page_start)
            except ValueError:
                start_idx = 0
        else:
            start_idx = 0

        end_idx = start_idx + page_size
        page_ids = all_ids[start_idx:end_idx]

        bundles = await self._repo.list_bundles(
            keyword=title_keyword,
            id_gte=id_gte,
            limit=page_size,
            cursor_id=page_start,
            descending=descending,
        )
        items = [_bundle_to_dto(b) for b in bundles]

        returned = len(items)
        has_prev = start_idx > 0
        prev_start = all_ids[start_idx - 1] if has_prev and start_idx > 0 else 0
        has_more = end_idx < len(all_ids)
        next_start = all_ids[end_idx] if has_more else 0

        first_id = page_ids[0] if page_ids else 0
        page = {
            "page_start": first_id,
            "page_size": returned,
            "has_prev": has_prev,
            "prev_start": prev_start,
            "has_more": has_more,
            "next_start": next_start,
        }
        return items, page

    async def get_bundle(self, bundle_id: int) -> dict:
        bundle = await self._repo.get_by_id(bundle_id)
        if bundle is None:
            raise NotFoundError("Material bundle not found", data={"id": bundle_id})
        material_ids = await self._repo.get_materials_for_bundle(bundle_id)
        dto = _bundle_to_dto(bundle)
        dto["materialIds"] = material_ids
        return dto

    async def get_bundle_detail(self, bundle_id: int) -> dict:
        bundle = await self._repo.get_by_id(bundle_id)
        if bundle is None:
            raise NotFoundError("Material bundle not found", data={"id": bundle_id})
        material_ids = await self._repo.get_materials_for_bundle(bundle_id)
        materials = []
        for mid in material_ids:
            material = await self._material_repo.get_by_id(mid)
            if material:
                materials.append(_material_to_dto(material))
        dto = _bundle_to_dto(bundle)
        dto["materials"] = materials
        dto["creator"] = {"id": bundle.creator_id}
        return dto

    async def create_bundle(
        self, *, title: str, content: str, creator_id: int, material_ids: list[int] | None = None
    ) -> dict:
        if material_ids:
            for mid in material_ids:
                material = await self._material_repo.get_by_id(mid)
                if material is None:
                    raise NotFoundError("Material not found", data={"id": mid})

        bundle = await self._repo.create(
            title=title,
            content=content,
            creator_id=creator_id,
        )
        if material_ids:
            for mid in material_ids:
                await self._repo.add_material_to_bundle(bundle_id=bundle.id, material_id=mid)
        return {"id": bundle.id}

    async def update_bundle(
        self,
        *,
        bundle_id: int,
        user_id: int,
        title: str | None = None,
        content: str | None = None,
        material_ids: list[int] | None = None,
    ) -> dict:
        bundle = await self._repo.get_by_id(bundle_id)
        if bundle is None:
            raise NotFoundError("Material bundle not found", data={"id": bundle_id})
        if bundle.creator_id != user_id:
            raise ForbiddenError("Only the creator can update the bundle")
        await self._repo.update(bundle, title=title, content=content)

        if material_ids is not None:
            current_mids = await self._repo.get_materials_for_bundle(bundle_id)
            for mid in current_mids:
                if mid not in material_ids:
                    await self._repo.remove_material_from_bundle(
                        bundle_id=bundle_id, material_id=mid
                    )
            for mid in material_ids:
                if mid not in current_mids:
                    await self._repo.add_material_to_bundle(bundle_id=bundle_id, material_id=mid)

        final_material_ids = await self._repo.get_materials_for_bundle(bundle_id)
        dto = _bundle_to_dto(bundle)
        dto["materialIds"] = final_material_ids
        return dto

    async def delete_bundle(self, *, bundle_id: int, user_id: int) -> None:
        bundle = await self._repo.get_by_id(bundle_id)
        if bundle is None:
            raise NotFoundError("Material bundle not found", data={"id": bundle_id})
        if bundle.creator_id != user_id:
            raise ForbiddenError("Only the creator can delete the bundle")
        await self._repo.delete(bundle_id)
