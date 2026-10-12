"""管理后台「机器」：MicroCloud 上的宿主机、它们上面的沙箱、温机池，和过去一天沙箱起得
怎么样（app.domain.machine.admin）。"""

from fastapi import APIRouter

from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.routes.admin_common import PlatformAdminDep
from app.domain.agent.device_hub import device_hub
from app.domain.machine import admin

router = APIRouter(prefix="/admin/machines", tags=["admin"])


@router.get("")
async def machine_overview(db: DbSession, handle: PlatformAdminDep) -> dict:
    del handle
    return ok(await admin.overview(db, device_hub))
