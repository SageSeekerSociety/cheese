from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.user.models import (
    User,
    UserFollowingRelationship,
    UserProfile,
    UserRealNameIdentity,
    UserRealNameAccessLog,
)
from app.domain.team.models import TeamUserRelation
from app.domain.task.models import TaskMembership, TaskSubmission
from app.domain.knowledge.models import Knowledge
from app.domain.questions.models import Question
from app.domain.answers.models import Answer


class UserRepository:
    """Persistence operations for the core user table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> User | None:
        stmt: Select[tuple[User]] = select(User).where(
            User.username == username, User.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        stmt: Select[tuple[User]] = select(User).where(
            User.id == user_id, User.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def is_username_taken(self, username: str) -> bool:
        stmt = select(User.id).where(User.username == username, User.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def is_email_taken(self, email: str) -> bool:
        stmt = select(User.id).where(User.email == email, User.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_by_email(self, email: str) -> User | None:
        stmt: Select[tuple[User]] = select(User).where(
            User.email == email, User.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        hashed_password: str,
    ) -> User:
        now = datetime.now(timezone.utc)
        user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            created_at=now,
            updated_at=now,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def update_password(self, user_id: int, hashed_password: str) -> None:
        stmt: Select[tuple[User]] = select(User).where(User.id == user_id)
        result = await self._session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.hashed_password = hashed_password
            await self._session.flush()

    async def get_by_ids(self, user_ids: Sequence[int]) -> dict[int, User]:
        if not user_ids:
            return {}
        stmt: Select[tuple[User]] = select(User).where(
            User.id.in_(list(user_ids)), User.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        users = list(result.scalars().all())
        return {u.id: u for u in users}


class UserProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_profiles_by_user_ids(self, user_ids: Sequence[int]) -> dict[int, UserProfile]:
        if not user_ids:
            return {}
        stmt: Select[tuple[UserProfile]] = select(UserProfile).where(
            and_(
                UserProfile.user_id.in_(list(user_ids)),
                UserProfile.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        return {row.user_id: row for row in rows}

    async def get_profile_by_user_id(self, user_id: int) -> UserProfile | None:
        stmt: Select[tuple[UserProfile]] = select(UserProfile).where(
            and_(UserProfile.user_id == user_id, UserProfile.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_profile(
        self,
        *,
        user_id: int,
        nickname: str,
        intro: str,
        avatar_id: int,
    ) -> UserProfile:
        now = datetime.now(timezone.utc)
        profile = UserProfile(
            user_id=user_id,
            nickname=nickname,
            intro=intro,
            avatar_id=avatar_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(profile)
        await self._session.flush()
        return profile

    async def list_profiles(self, *, limit: int, offset: int) -> list[UserProfile]:
        stmt: Select[tuple[UserProfile]] = (
            select(UserProfile)
            .where(UserProfile.deleted_at.is_(None))
            .order_by(UserProfile.user_id.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_profile(
        self,
        profile: UserProfile,
        *,
        nickname: str | None = None,
        intro: str | None = None,
        avatar_id: int | None = None,
    ) -> UserProfile:
        if nickname is not None:
            profile.nickname = nickname
        if intro is not None:
            profile.intro = intro
        if avatar_id is not None:
            profile.avatar_id = avatar_id
        await self._session.flush()
        return profile


class UserFollowingRepository:
    """Persistence operations for user follow relationships."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_followers(self, user_id: int) -> int:
        stmt = select(func.count(UserFollowingRelationship.id)).where(
            UserFollowingRelationship.followee_id == user_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_following(self, user_id: int) -> int:
        stmt = select(func.count(UserFollowingRelationship.id)).where(
            UserFollowingRelationship.follower_id == user_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def is_following(self, follower_id: int, followee_id: int) -> bool:
        stmt = select(UserFollowingRelationship.id).where(
            UserFollowingRelationship.follower_id == follower_id,
            UserFollowingRelationship.followee_id == followee_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def add_follow(self, follower_id: int, followee_id: int) -> None:
        rel = UserFollowingRelationship(
            follower_id=follower_id,
            followee_id=followee_id,
            created_at=datetime.now(timezone.utc),
        )
        self._session.add(rel)
        await self._session.flush()

    async def soft_delete_follow(self, follower_id: int, followee_id: int) -> bool:
        stmt: Select[tuple[UserFollowingRelationship]] = select(UserFollowingRelationship).where(
            UserFollowingRelationship.follower_id == follower_id,
            UserFollowingRelationship.followee_id == followee_id,
            UserFollowingRelationship.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        rel = result.scalar_one_or_none()
        if rel is None:
            return False
        rel.deleted_at = datetime.now(timezone.utc)
        await self._session.flush()
        return True


class UserRealNameRepository:
    """Minimal repository for real-name identities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_identity(self, user_id: int) -> bool:
        stmt = select(UserRealNameIdentity.id).where(
            UserRealNameIdentity.user_id == user_id,
            UserRealNameIdentity.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_identity(self, user_id: int) -> UserRealNameIdentity | None:
        stmt: Select[tuple[UserRealNameIdentity]] = select(UserRealNameIdentity).where(
            UserRealNameIdentity.user_id == user_id,
            UserRealNameIdentity.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_identity(
        self,
        user_id: int,
        *,
        real_name: str,
        student_id: str,
        grade: str,
        major: str,
        class_name: str,
        encrypted: bool = False,
    ) -> UserRealNameIdentity:
        from datetime import datetime as _dt

        now = _dt.utcnow()
        existing = await self.get_identity(user_id)
        if existing is None:
            identity = UserRealNameIdentity(
                user_id=user_id,
                encrypted=encrypted,
                real_name=real_name,
                student_id=student_id,
                grade=grade,
                major=major,
                class_name=class_name,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            self._session.add(identity)
            await self._session.flush()
            return identity

        existing.real_name = real_name
        existing.student_id = student_id
        existing.grade = grade
        existing.major = major
        existing.class_name = class_name
        existing.encrypted = encrypted
        existing.updated_at = now
        await self._session.flush()
        return existing

    async def create_access_log(
        self,
        *,
        accessor_id: int,
        target_id: int,
        access_reason: str,
        ip_address: str,
        access_type: str,
        module_type: str | None = None,
        module_entity_id: int | None = None,
    ) -> UserRealNameAccessLog:
        from datetime import datetime as _dt

        now = _dt.utcnow()
        log = UserRealNameAccessLog(
            accessor_id=accessor_id,
            target_id=target_id,
            module_type=module_type,
            module_entity_id=module_entity_id,
            access_reason=access_reason,
            ip_address=ip_address,
            access_type=access_type,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(log)
        await self._session.flush()
        return log

    async def list_access_logs(
        self,
        *,
        target_id: int,
        limit: int,
        offset: int = 0,
    ) -> tuple[list[UserRealNameAccessLog], int]:
        stmt: Select[tuple[UserRealNameAccessLog]] = (
            select(UserRealNameAccessLog)
            .where(
                UserRealNameAccessLog.target_id == target_id,
                UserRealNameAccessLog.deleted_at.is_(None),
            )
            .order_by(UserRealNameAccessLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())

        count_stmt = select(func.count(UserRealNameAccessLog.id)).where(
            UserRealNameAccessLog.target_id == target_id,
            UserRealNameAccessLog.deleted_at.is_(None),
        )
        count_result = await self._session.execute(count_stmt)
        total = int(count_result.scalar_one() or 0)
        return rows, total


class UserStatisticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count_teams(self, user_id: int) -> int:
        stmt = select(func.count(TeamUserRelation.id)).where(
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_task_memberships(self, user_id: int) -> int:
        stmt = select(func.count(TaskMembership.id)).where(
            TaskMembership.member_id == user_id,
            TaskMembership.is_team.is_(False),
            TaskMembership.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_knowledge_entries(self, user_id: int) -> int:
        stmt = select(func.count(Knowledge.id)).where(
            Knowledge.created_by == user_id,
            Knowledge.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_submissions(self, user_id: int) -> int:
        stmt = select(func.count(TaskSubmission.id)).where(
            TaskSubmission.submitter_id == user_id,
            TaskSubmission.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_questions(self, user_id: int) -> int:
        stmt = select(func.count(Question.id)).where(
            Question.created_by_id == user_id,
            Question.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_answers(self, user_id: int) -> int:
        stmt = select(func.count(Answer.id)).where(
            Answer.created_by_id == user_id,
            Answer.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def aggregate(self, user_id: int) -> dict[str, int]:
        teams = await self.count_teams(user_id)
        tasks = await self.count_task_memberships(user_id)
        knowledge = await self.count_knowledge_entries(user_id)
        submissions = await self.count_submissions(user_id)
        questions = await self.count_questions(user_id)
        answers = await self.count_answers(user_id)
        return {
            "teamCount": teams,
            "taskParticipationCount": tasks,
            "knowledgeCount": knowledge,
            "submissionCount": submissions,
            "questionCount": questions,
            "answerCount": answers,
        }
