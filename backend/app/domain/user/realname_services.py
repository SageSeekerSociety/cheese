from collections.abc import Sequence
from datetime import UTC

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import Purpose, decrypt, encrypt
from app.core.errors import BadRequestError, NotFoundError
from app.domain.user.models import User, UserRealNameAccessLog, UserRealNameIdentity
from app.domain.user.repositories import (
    UserProfileRepository,
    UserRealNameRepository,
    UserRepository,
)


def seal_realname_field(user_id: int, field: str, value: str) -> str:
    """Encrypt one real-name column, bound to its owner and to the column."""
    return encrypt(Purpose.REALNAME, value, bound_to=f"user:{user_id}:{field}")


def open_realname_field(identity: UserRealNameIdentity, field: str) -> str:
    """One real-name column as text, decrypting it when the row is encrypted."""
    value: str = getattr(identity, field)
    if not identity.encrypted or not value:
        return value
    return decrypt(Purpose.REALNAME, value, bound_to=f"user:{identity.user_id}:{field}")


def realname_dict(identity: UserRealNameIdentity) -> dict:
    return {
        "realName": open_realname_field(identity, "real_name"),
        "studentId": open_realname_field(identity, "student_id"),
        "grade": open_realname_field(identity, "grade"),
        "major": open_realname_field(identity, "major"),
        "className": open_realname_field(identity, "class_name"),
    }


class UserRealNameService:
    def __init__(
        self,
        session: AsyncSession,
        user_repo: UserRepository,
        profile_repo: UserProfileRepository,
        realname_repo: UserRealNameRepository,
    ) -> None:
        self._session = session
        self._user_repo = user_repo
        self._profile_repo = profile_repo
        self._realname_repo = realname_repo

    async def _ensure_user_exists(self, user_id: int) -> User:
        user = await self._user_repo.get_by_id(user_id)
        if user is None:
            raise NotFoundError(
                "Resource user not found", data={"type": "user", "id": user_id}
            )
        return user

    def _mask_name(self, real_name: str) -> str:
        if not real_name:
            return real_name
        return real_name[0] + "*" * max(0, len(real_name) - 1)

    def _mask_student_id(self, student_id: str) -> str:
        if len(student_id) <= 2:
            return student_id
        return student_id[0] + "*" * (len(student_id) - 2) + student_id[-1]

    async def get_user_identity(self, user_id: int) -> dict:
        await self._ensure_user_exists(user_id)
        identity = await self._realname_repo.get_identity(user_id)
        if identity is None:
            raise NotFoundError(
                "user real name identity not found",
                data={"type": "user_real_name_identity", "id": user_id},
            )
        return realname_dict(identity)

    async def get_fuzzy_user_identity(self, user_id: int) -> dict:
        precise = await self.get_user_identity(user_id)
        precise["realName"] = self._mask_name(precise["realName"])
        precise["studentId"] = self._mask_student_id(precise["studentId"])
        return precise

    async def create_or_update_user_identity(
        self,
        *,
        user_id: int,
        real_name: str,
        student_id: str,
        grade: str,
        major: str,
        class_name: str,
    ) -> dict:
        await self._ensure_user_exists(user_id)
        real_name, student_id = real_name.strip(), student_id.strip()
        if not real_name or not student_id:
            raise BadRequestError("A real name and a student ID are required.")
        grade, major, class_name = grade.strip(), major.strip(), class_name.strip()
        identity = await self._realname_repo.upsert_identity(
            user_id=user_id,
            real_name=seal_realname_field(user_id, "real_name", real_name),
            student_id=seal_realname_field(user_id, "student_id", student_id),
            grade=seal_realname_field(user_id, "grade", grade),
            major=seal_realname_field(user_id, "major", major),
            class_name=seal_realname_field(user_id, "class_name", class_name),
            encrypted=True,
        )
        return realname_dict(identity)

    async def delete_user_identity(self, user_id: int) -> None:
        await self._ensure_user_exists(user_id)
        if not await self._realname_repo.delete_identity(user_id):
            raise NotFoundError(
                "user real name identity not found",
                data={"type": "user_real_name_identity", "id": user_id},
            )

    async def log_access(
        self,
        *,
        accessor_id: int,
        target_id: int,
        access_reason: str,
        access_type: str,
        ip_address: str,
        module_type: str | None = None,
        module_entity_id: int | None = None,
    ) -> UserRealNameAccessLog:
        await self._ensure_user_exists(accessor_id)
        await self._ensure_user_exists(target_id)
        return await self._realname_repo.create_access_log(
            accessor_id=accessor_id,
            target_id=target_id,
            access_reason=access_reason,
            ip_address=ip_address,
            access_type=access_type,
            module_type=module_type,
            module_entity_id=module_entity_id,
        )

    async def get_access_logs(
        self,
        *,
        target_user_id: int,
        page_size: int,
        page_start: int | None,
    ) -> tuple[list[dict], dict]:
        await self._ensure_user_exists(target_user_id)
        limit = max(1, min(page_size, 100))
        offset = page_start or 0
        rows, total = await self._realname_repo.list_access_logs(
            target_id=target_user_id,
            limit=limit,
            offset=offset,
        )
        accessor_ids: Sequence[int] = [row.accessor_id for row in rows]
        profiles = await self._profile_repo.get_profiles_by_user_ids(accessor_ids)

        accessor_users: dict[int, User] = {}
        for uid in accessor_ids:
            if uid in accessor_users:
                continue
            user = await self._user_repo.get_by_id(uid)
            if user is not None:
                accessor_users[uid] = user

        logs: list[dict] = []
        for log in rows:
            profile = profiles.get(log.accessor_id)
            user = accessor_users.get(log.accessor_id)
            if user is None or profile is None:
                continue
            accessor_dto = {
                "id": user.id,
                "username": user.username,
                "nickname": profile.nickname,
                "avatarId": profile.avatar_id,
                "intro": profile.intro,
            }
            aware = (
                log.created_at
                if log.created_at.tzinfo is not None
                else log.created_at.replace(tzinfo=UTC)
            )
            access_time_ms = int(aware.timestamp() * 1000)
            logs.append(
                {
                    "accessor": accessor_dto,
                    "accessModuleType": log.module_type,
                    "accessEntityId": log.module_entity_id,
                    "accessEntityName": None,
                    "accessTime": access_time_ms,
                    "accessType": log.access_type,
                    "ipAddress": log.ip_address,
                    "accessReason": log.access_reason,
                }
            )

        returned = len(logs)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return logs, page
