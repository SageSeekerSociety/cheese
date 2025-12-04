from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.checker import get_auth_user
from app.auth.core import AuthUserInfo
from app.domain.llm.repositories import AIUserQuotaRepository
from app.domain.llm.services import AiAdviceService
from app.db.session import get_db


router = APIRouter(prefix="/ai", tags=["AI"])


async def get_ai_service(db=Depends(get_db)) -> AiAdviceService:
    repo = AIUserQuotaRepository(session=db)
    return AiAdviceService(repo=repo)


@router.get("/quota", summary="Get Current User's AI Quota")
async def get_ai_quota(
    service: AiAdviceService = Depends(get_ai_service),
    auth_user: AuthUserInfo = Depends(get_auth_user),
) -> dict:
    quota = await service.get_quota(user_id=auth_user.user_id)
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "remaining": quota.remaining,
            "total": quota.total,
            "reset_time": quota.reset_time.isoformat(),
        },
    }
