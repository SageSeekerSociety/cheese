from __future__ import annotations

from collections.abc import Sequence

import bcrypt

from app.domain.user.models import User, UserProfile
from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRepository,
    UserStatisticsRepository,
)


class UserService:
    """Read-only operations for user profiles (used by other domains)."""

    def __init__(self, repo: UserProfileRepository) -> None:
        self._repo = repo

    async def get_users_by_ids(self, ids: Sequence[int]) -> dict[int, UserProfile]:
        return await self._repo.get_profiles_by_user_ids(ids)


class UserProfileService:
    """Profile update operations."""

    def __init__(self, profile_repo: UserProfileRepository) -> None:
        self._profile_repo = profile_repo

    async def update_profile(
        self,
        *,
        user_id: int,
        nickname: str | None = None,
        intro: str | None = None,
        avatar_id: int | None = None,
    ) -> None:
        profile = await self._profile_repo.get_profile_by_user_id(user_id)
        if profile is None:
            from app.core.errors import NotFoundError
            raise NotFoundError("User profile not found")
        await self._profile_repo.update_profile(
            profile, nickname=nickname, intro=intro, avatar_id=avatar_id
        )


class UserAuthService:
    """Authentication-related user operations (login/refresh helpers)."""

    def __init__(
        self,
        user_repo: UserRepository,
        profile_repo: UserProfileRepository,
        follow_repo: UserFollowingRepository,
        stats_repo: UserStatisticsRepository,
    ) -> None:
        self._user_repo = user_repo
        self._profile_repo = profile_repo
        self._follow_repo = follow_repo
        self._stats_repo = stats_repo

    async def authenticate(
        self,
        username: str,
        password: str,
    ) -> tuple[User, UserProfile] | None:
        """Validate username/password using the bcrypt hash stored in DB.

        Returns (user, profile) when successful; otherwise None.
        """
        user = await self._user_repo.get_by_username(username)
        if user is None or not user.hashed_password:
            return None

        if not bcrypt.checkpw(
            password.encode("utf-8"), user.hashed_password.encode("utf-8")
        ):
            return None

        profile = await self._profile_repo.get_profile_by_user_id(user.id)
        if profile is None:
            # In the Kotlin / NestJS world every active user should have a profile.
            # If it is missing, treat as authentication failure for now.
            return None

        return user, profile

    async def get_user_with_profile(self, user_id: int) -> tuple[User, UserProfile]:
        user = await self._user_repo.get_by_id(user_id)
        if user is None:
            raise ValueError("User not found")
        profile = await self._profile_repo.get_profile_by_user_id(user_id)
        if profile is None:
            raise ValueError("User profile not found")
        return user, profile

    async def get_user_by_email(self, email: str) -> User | None:
        return await self._user_repo.get_by_email(email)

    async def update_password(self, user_id: int, new_password: str) -> None:
        hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        await self._user_repo.update_password(user_id, hashed)

    async def is_username_taken(self, username: str) -> bool:
        return await self._user_repo.is_username_taken(username)

    async def is_email_taken(self, email: str) -> bool:
        return await self._user_repo.is_email_taken(email)

    async def register_with_password(
        self,
        *,
        username: str,
        nickname: str,
        email: str,
        password: str,
        default_avatar_id: int = 1,
    ) -> tuple[User, UserProfile]:
        """Create a new user using legacy password-based auth.

        NOTE: This is a simplified Python-side registration:
        - 不发送真实邮件，也不校验 emailCode。
        - 仅覆盖最常见的用户名/邮箱 + 密码注册路径。
        """
        if await self._user_repo.is_username_taken(username):
            raise ValueError("USERNAME_TAKEN")
        if await self._user_repo.is_email_taken(email):
            raise ValueError("EMAIL_TAKEN")

        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        user = await self._user_repo.create_user(
            username=username,
            email=email,
            hashed_password=hashed,
        )
        profile = await self._profile_repo.create_profile(
            user_id=user.id,
            nickname=nickname,
            intro="",
            avatar_id=default_avatar_id,
        )
        return user, profile

    async def register_with_srp(
        self,
        *,
        username: str,
        nickname: str,
        email: str,
        srp_salt: str,
        srp_verifier: str,
        default_avatar_id: int = 1,
    ) -> tuple[User, UserProfile]:
        """Create a new user using SRP-based auth.

        SRP salt and verifier are stored as the hashed_password field for now.
        In a full SRP implementation, separate columns would be used.
        """
        if await self._user_repo.is_username_taken(username):
            raise ValueError("USERNAME_TAKEN")
        if await self._user_repo.is_email_taken(email):
            raise ValueError("EMAIL_TAKEN")

        srp_data = f"SRP:{srp_salt}:{srp_verifier}"
        user = await self._user_repo.create_user(
            username=username,
            email=email,
            hashed_password=srp_data,
        )
        profile = await self._profile_repo.create_profile(
            user_id=user.id,
            nickname=nickname,
            intro="",
            avatar_id=default_avatar_id,
        )
        return user, profile

    @staticmethod
    def _base_user_dto(user: User, profile: UserProfile) -> dict:
        return {
            "id": user.id,
            "username": user.username,
            "nickname": profile.nickname,
            "avatarId": profile.avatar_id,
            "intro": profile.intro,
        }

    async def build_user_dto(
        self,
        user: User,
        profile: UserProfile,
        viewer_id: int | None = None,
    ) -> dict:
        """Map User + UserProfile into a UserDto-compatible dict with counts & follow flag."""
        base = self._base_user_dto(user, profile)
        followers = await self._follow_repo.count_followers(user.id)
        following = await self._follow_repo.count_following(user.id)
        is_follow = False
        if viewer_id is not None and viewer_id != user.id:
            is_follow = await self._follow_repo.is_following(
                follower_id=viewer_id,
                followee_id=user.id,
            )
        stats = await self._stats_repo.aggregate(user.id)

        base.update(
            {
                "follow_count": following,
                "fans_count": followers,
                "question_count": stats["questionCount"],
                "answer_count": stats["answerCount"],
                "team_count": stats["teamCount"],
                "task_participation_count": stats["taskParticipationCount"],
                "knowledge_count": stats["knowledgeCount"],
                "submission_count": stats["submissionCount"],
                "is_follow": is_follow,
            }
        )
        return base
