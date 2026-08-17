from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query

from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.errors import BadRequestError
from app.db.session import get_db
from app.domain.materials.repositories import (
    MaterialBundleRepository,
    MaterialRepository,
)
from app.domain.materials.services import MaterialBundleService

router = APIRouter(prefix="/material-bundles", tags=["MaterialBundles"])


async def get_bundle_service(db=Depends(get_db)) -> MaterialBundleService:
    repo = MaterialBundleRepository(session=db)
    material_repo = MaterialRepository(session=db)
    return MaterialBundleService(repo=repo, material_repo=material_repo)


@router.get(
    "",
    summary="List Material Bundles",
)
async def list_material_bundles(
    q: str | None = Query(default=None),
    page_start: int | None = Query(default=None, alias="page_start"),
    page_size: int = Query(default=20, ge=1, le=100, alias="page_size"),
    sort: str | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: MaterialBundleService = Depends(get_bundle_service),
) -> dict:
    if q and len(q) > 100:
        raise BadRequestError("Keyword too long")
    bundles, page = await service.list_bundles(
        keyword=q,
        page_start=page_start,
        page_size=page_size,
        sort=sort,
    )
    return {"code": 200, "message": "OK", "data": {"materials": bundles, "page": page}}


@router.get(
    "/{bundle_id}",
    summary="Get Material Bundle Detail",
)
async def get_material_bundle(
    bundle_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: MaterialBundleService = Depends(get_bundle_service),
) -> dict:
    bundle = await service.get_bundle_detail(bundle_id)
    return {"code": 200, "message": "OK", "data": {"materialBundle": bundle}}


@router.post(
    "",
    summary="Create Material Bundle",
    status_code=201,
)
async def create_material_bundle(
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: MaterialBundleService = Depends(get_bundle_service),
) -> dict:
    title = payload.get("title")
    content = payload.get("content", "")
    material_ids = payload.get("materialIds") or payload.get("materials", [])

    if not isinstance(title, str) or not title.strip():
        raise BadRequestError("title is required")

    result = await service.create_bundle(
        title=title,
        content=content,
        creator_id=auth_user.user_id,
        material_ids=material_ids,
    )
    return {"code": 201, "message": "Created", "data": result}


@router.patch(
    "/{bundle_id}",
    summary="Update Material Bundle",
)
async def update_material_bundle(
    bundle_id: Annotated[int, Path(ge=0)],
    payload: dict = Body(...),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: MaterialBundleService = Depends(get_bundle_service),
) -> dict:
    title = payload.get("title")
    content = payload.get("content")
    material_ids = payload.get("materialIds") or payload.get("materials")

    bundle = await service.update_bundle(
        bundle_id=bundle_id,
        user_id=auth_user.user_id,
        title=title,
        content=content,
        material_ids=material_ids,
    )
    return {"code": 200, "message": "OK", "data": {"bundle": bundle}}


@router.delete(
    "/{bundle_id}",
    summary="Delete Material Bundle",
    status_code=204,
)
async def delete_material_bundle(
    bundle_id: Annotated[int, Path(ge=0)],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: MaterialBundleService = Depends(get_bundle_service),
) -> None:
    await service.delete_bundle(bundle_id=bundle_id, user_id=auth_user.user_id)
