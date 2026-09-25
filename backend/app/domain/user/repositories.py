from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.answers.models import Answer
from app.domain.avatars.models import Avatar
from app.domain.identity.models import AgentBinding
from app.domain.knowledge.models import Knowledge
from app.domain.platform_stats.windows import utc_day
from app.domain.questions.models import Question
from app.domain.team.models import Team
from app.domain.user.models import (
    User,
    UserProfile,
    UserRealNameAccessLog,
    UserRealNameIdentity,
)


class UserRepository:
    """Persistence operations for the core user table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_handle(self, handle: str) -> User | None:
        """cheesex compat (fusion A1): handle == main's User.username."""
        return await self.get_by_username(handle)

    async def get_by_username(self, username: str) -> User | None:
        stmt: Select[tuple[User]] = select(User).where(
            User.username == username, User.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def search_accounts(self, q: str, limit: int) -> Sequence[tuple[str, str]]:
        """(handle, 昵称) —— 按关键词找账号，给「加管理员」那个选择器用。

        昵称在 `user_profile` 上，所以是 **left** join：注册路径会写一条 profile，
        历史账号不一定有，inner join 会让那些账号在图谱里彻底消失（搜 handle 也搜
        不到）。没有昵称的回 handle 本身，界面至少显示得出一个能认的东西。

        两列都搜：加人的时候有人想得起名字、有人只记得 handle。

        agent 用 `NOT EXISTS` 在**这里**排掉，不是回给调用方再过滤一遍 —— agent
        当不了管理员（`FeedbackService.add_admin` 会拒），把它画在选择器里等于给人
        一个点下去必然失败的选项。判据是同一张 `agent_bindings` 表，只是从一次一个
        的 `IdentityService.is_agent` 变成了一次一条 SQL。
        """
        pattern = f"%{q.strip()}%"
        stmt = (
            select(User.username, UserProfile.nickname)
            .outerjoin(UserProfile, UserProfile.user_id == User.id)
            .where(
                User.deleted_at.is_(None),
                or_(
                    User.username.ilike(pattern),
                    UserProfile.nickname.ilike(pattern),
                ),
                ~exists().where(AgentBinding.user_id == User.id),
            )
            .order_by(User.username)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [(username, nickname or username) for username, nickname in result]

    async def lookup_account(self, q: str) -> tuple[str, str, int | None] | None:
        """(handle, 昵称, avatar_id) of the one person whose username or email is
        exactly ``q``, case aside — or None.

        Exact on purpose: this is how someone outside a team is found to be
        invited, and a partial match would let anyone page through who is
        registered. AI accounts are never returned; they are not invited.
        """
        wanted = q.strip().lower()
        if not wanted:
            return None
        stmt = (
            select(User.username, UserProfile.nickname, UserProfile.avatar_id)
            .outerjoin(UserProfile, UserProfile.user_id == User.id)
            .where(
                User.deleted_at.is_(None),
                or_(
                    func.lower(User.username) == wanted,
                    func.lower(User.email) == wanted,
                ),
                ~exists().where(AgentBinding.user_id == User.id),
            )
            .limit(1)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        username, nickname, avatar_id = row
        return username, nickname or username, avatar_id

    async def get_by_handles(self, handles: Sequence[str]) -> dict[str, User]:
        """Batch handle → user. Roster-wide lookups run on every agent turn, so
        they must not go N+1 over ``get_by_handle``."""
        names = {h for h in handles if h}
        if not names:
            return {}
        stmt: Select[tuple[User]] = select(User).where(
            User.username.in_(names), User.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return {u.username: u for u in result.scalars()}

    async def get_by_id(self, user_id: int) -> User | None:
        stmt: Select[tuple[User]] = select(User).where(
            User.id == user_id, User.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def is_username_taken(self, username: str) -> bool:
        """Case-insensitive, matching ``uq_user_username_lower`` — and a team's
        handle takes the name too: users and teams share one namespace."""
        lowered = username.lower()
        user = select(User.id).where(
            func.lower(User.username) == lowered, User.deleted_at.is_(None)
        )
        team = select(Team.id).where(
            func.lower(Team.handle) == lowered, Team.deleted_at.is_(None)
        )
        return bool(await self._session.scalar(select(exists(user) | exists(team))))

    async def is_email_taken(self, email: str) -> bool:
        """Case-insensitive, matching ``uq_user_email_lower``."""
        stmt = select(User.id).where(
            func.lower(User.email) == email.lower(), User.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive, matching ``uq_user_email_lower``."""
        stmt: Select[tuple[User]] = select(User).where(
            func.lower(User.email) == email.lower(), User.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        hashed_password: str | None = None,
    ) -> User:
        now = datetime.now(UTC)
        email_domain = email.split("@", 1)[1].lower() if "@" in email else None
        user = User(
            username=username,
            email=email,
            email_domain=email_domain,
            hashed_password=hashed_password,
            created_at=now,
            updated_at=now,
        )
        # A savepoint, so a unique violation leaves the session usable for the
        # caller to find out which value was taken. The add goes inside it:
        # begin_nested() flushes pending objects first.
        async with self._session.begin_nested():
            self._session.add(user)
            await self._session.flush()
        return user

    async def update_email(self, user: User, email: str) -> None:
        """Raises ``IntegrityError`` when a live account already holds it
        (``uq_user_email_lower``); the session stays usable."""
        async with self._session.begin_nested():
            user.email = email
            user.email_domain = email.split("@", 1)[1].lower()
            user.updated_at = datetime.now(UTC)
            await self._session.flush()

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

    async def count_accounts(self) -> int:
        """平台上的账号总数。

        **这是全仓少数几个诚实的全表聚合之一**，而且它必须说得出为什么：`user`
        表只有约 1200 行（`api/routes/admin_members.py` 的注释记着这个部署的数），
        整张表比 `resource_usage` 一天的增量还小。`created_at` 上没有索引，计划是
        顺序扫 —— 这个规模下那是正确的计划，加索引反而多一份写放大。

        与「在线人数」无关：那需要每个账号的活动时间，而这个表里没有那样一列，也
        不该为了一个看板数字凭空造一个。
        """
        stmt = select(func.count(User.id)).where(User.deleted_at.is_(None))
        return int((await self._session.execute(stmt)).scalar_one() or 0)

    async def count_accounts_by_kind(self) -> dict[str, int]:
        """存量里有多少真人、多少 agent。

        判据是 `agent_bindings` —— **同一个判据** `IdentityService.is_agent` 和
        管理员选择器用的那一个，只是从一次一个的探针变成一条聚合 SQL。不在这里
        另发明一（handle 前缀、email 域名……）：两套判据的症状是「看板说 12 个
        agent、成员页标出 9 个」。

        agent 有一行 `agent_bindings`，真人没有。分身是一人一行（见
        `identity/services.py`），所以这里的 agent 数是**身份数**，不是「几个
        芝士」。
        """
        agent = exists().where(AgentBinding.user_id == User.id)
        stmt = select(
            func.count(User.id).filter(agent),
            func.count(User.id).filter(~agent),
        ).where(User.deleted_at.is_(None))
        agents, humans = (await self._session.execute(stmt)).one()
        return {"humans": int(humans or 0), "agents": int(agents or 0)}

    async def accounts_series_by_kind(
        self, *, since: datetime, until: datetime
    ) -> dict[str, dict[date, int]]:
        """窗口内按 **UTC 的天**新增的账号数，真人 / agent 各一条；稀疏，补 0 由
        调用方做。和 `count_accounts_by_kind` 同一个判据（`agent_bindings`）。"""
        agent = exists().where(AgentBinding.user_id == User.id)
        day = utc_day(User.created_at)
        stmt = (
            select(day.label("day"), agent.label("is_agent"), func.count(User.id))
            .where(
                User.deleted_at.is_(None),
                User.created_at >= since,
                User.created_at < until,
            )
            .group_by(day, agent)
            .order_by(day)
        )
        rows = (await self._session.execute(stmt)).all()
        out: dict[str, dict[date, int]] = {"humans": {}, "agents": {}}
        for row in rows:
            key = "agents" if row[1] else "humans"
            out[key][row[0].date()] = int(row[2])
        return out

    async def accounts_series(
        self, *, since: datetime, until: datetime
    ) -> dict[date, int]:
        """窗口内按 **UTC 的天**新增的账号数，稀疏；补 0 由调用方做。

        和 `count_accounts` 同一个规模判断：这张表小，扫它不需要索引，`created_at`
        上的范围条件走的是顺序扫。
        """
        day = utc_day(User.created_at)
        stmt = (
            select(day.label("day"), func.count(User.id))
            .where(
                User.deleted_at.is_(None),
                User.created_at >= since,
                User.created_at < until,
            )
            .group_by(day)
            .order_by(day)
        )
        rows = (await self._session.execute(stmt)).all()
        return {row[0].date(): int(row[1]) for row in rows}

    async def count_accounts_between(self, *, since: datetime, until: datetime) -> int:
        """窗口内新增的账号总数 —— `accounts_series` 的合计版（看板环比用）。

        和 `accounts_series` 同一个 WHERE（未删 + 半开窗口），只是不分桶。
        """
        stmt = select(func.count(User.id)).where(
            User.deleted_at.is_(None),
            User.created_at >= since,
            User.created_at < until,
        )
        return int((await self._session.execute(stmt)).scalar_one() or 0)


class UserProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_profiles_by_user_ids(
        self, user_ids: Sequence[int]
    ) -> dict[int, UserProfile]:
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

    async def nickname_and_avatar_by_user_id(
        self, user_ids: Sequence[int]
    ) -> dict[int, tuple[str | None, int | None]]:
        """user_id -> (昵称, 自己挑过的头像 id)，一条查询里两列。

        名单、名册这些「画一行」的地方要的是名字和脸，而它们一个在 `UserProfile`
        上、一个要经 `Avatar` 认出来 —— 分开查就是每个调用点自己 join 一次，或者
        先查 profile 再按 avatar_id 回查一遍（那又成了 N+1）。这里一次 outerjoin
        就够，`AdminService.admins_out` 那类「一屏几十行」的调用点因此不需要第二
        次往返。

        avatar id 只在**真的挑过**时才有值，判据仍是 ``avatar_type != "default"``，
        不是拿 id 去比 1：默认头像是哪一行是种子数据，每个环境不一样。

        outerjoin 而不是 join：没有档案行的人、头像 id 指着一个已经不在 `Avatar`
        里的人，都要留在结果里（值给 None），而不是整行消失 —— 调用方要画的是
        「这个人在名单上，只是没挑过头像」。这一条正是它和 ``chosen_avatar_ids``
        分开的原因：那边「没挑过」要整条不见（界面据此画彩色首字母），这边要给一个
        能占位的行。规则仍只有一处，``chosen_avatar_ids`` 从这条结果里过滤。
        """
        if not user_ids:
            return {}
        stmt = (
            select(
                UserProfile.user_id,
                UserProfile.nickname,
                UserProfile.avatar_id,
                Avatar.avatar_type,
            )
            .outerjoin(Avatar, Avatar.id == UserProfile.avatar_id)
            .where(
                and_(
                    UserProfile.user_id.in_(list(user_ids)),
                    UserProfile.deleted_at.is_(None),
                )
            )
        )
        rows = (await self._session.execute(stmt)).all()
        return {
            user_id: (
                nickname,
                avatar_id if avatar_type not in (None, "default") else None,
            )
            for user_id, nickname, avatar_id, avatar_type in rows
        }

    async def chosen_avatar_ids(self, user_ids: Sequence[int]) -> dict[int, int]:
        """user_id -> the avatar this person actually PICKED, for those who did.

        A user who never picked one is absent from the mapping rather than
        mapped to the global default, because "everyone who never chose shares
        one face" is strictly worse at telling people apart than the per-handle
        hashed initial the UI falls back to — and telling people apart is the
        entire job of an avatar. Every registration path hardcodes
        ``default_avatar_id: int = 1`` (``domain/user/services.py``), so that
        state is the common one, not an edge case.

        The default row is recognised by ``avatar_type``, not by comparing
        against a literal 1: which row holds the default is seed data and
        differs per environment.

        This is now a filter over ``nickname_and_avatar_by_user_id`` rather than
        its own query, so 「哪一行是默认头像」 has one implementation: a caller
        that wants the nickname too (``AdminService.admins_out``) reads the same
        join instead of writing a second one that could drift.
        """
        faces = await self.nickname_and_avatar_by_user_id(user_ids)
        return {
            user_id: avatar_id
            for user_id, (_, avatar_id) in faces.items()
            if avatar_id is not None
        }

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
        now = datetime.now(UTC)
        profile = UserProfile(
            user_id=user_id,
            nickname=nickname,
            intro=intro,
            avatar_id=avatar_id,
            created_at=now,
            updated_at=now,
        )
        # Added inside the savepoint: begin_nested() flushes pending objects
        # first, which would put the INSERT outside it.
        async with self._session.begin_nested():
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
        now = datetime.now(UTC)
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
        now = datetime.now(UTC)
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
        from app.domain.team.models import TeamUserRelation

        stmt = select(func.count(TeamUserRelation.id)).where(
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_task_memberships(self, user_id: int) -> int:
        from app.domain.task.models import TaskMembership

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
        from app.domain.task.models import TaskSubmission

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
