import asyncio
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime

import bcrypt
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableEntityError
from app.domain.identity.handles import is_reserved_username
from app.domain.user.models import User, UserProfile
from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRepository,
    UserStatisticsRepository,
)

NICKNAME_MAX_LENGTH = 50

# A nickname must carry at least one letter, digit or CJK ideograph, so that a
# name made only of punctuation or invisible characters cannot be saved.
_NICKNAME_MEANINGFUL_RE = re.compile(r"[0-9A-Za-z㐀-䶿一-鿿]")


async def user_by_handle(session: AsyncSession, handle: str) -> User | None:
    return await UserRepository(session).get_by_username(handle)


async def handles_by_ids(
    session: AsyncSession, user_ids: Iterable[int]
) -> tuple[str, ...]:
    """用户 id -> handle，按传进来的顺序；查不到的那些不在里面。

    收件人在投递那一侧是 handle（I11），而社交那几处调用点手里是自己算出来的一组
    用户 id —— 申请的管理员名单、邀请人、被 @ 的那几个人。翻译只此一处：与
    ``user_by_handle`` 一样，账号叫什么是 `User` 的事实，而调用点自己拼一遍
    ``select(User.username)`` 就要各自再答一遍「删掉的账号算不算」。

    一次查完，不按人 N+1：一条讨论可以 @ 一屋子人。
    """
    ids = list(dict.fromkeys(int(i) for i in user_ids if int(i) > 0))
    users = await UserRepository(session).get_by_ids(ids)
    return tuple(users[i].username for i in ids if i in users)


async def search_accounts(
    session: AsyncSession, q: str, limit: int
) -> Sequence[tuple[str, str]]:
    """(handle, 昵称) —— 按关键词找账号，给「加管理员」那个选择器用。

    Lives beside the other two rather than in the calling domain, for the reason
    ``chosen_avatars_by_handle`` states: what an account is called is a fact about
    `User`/`UserProfile`, and the two rules that are easy to get wrong — a missing
    profile must not hide the account, and agents are not candidates — are written
    once, in ``UserRepository.search_accounts``.
    """
    return await UserRepository(session).search_accounts(q, limit)


async def chosen_avatars_by_handle(
    session: AsyncSession, handles: Iterable[str]
) -> dict[str, int]:
    """handle -> 这个人**自己选过**的头像，没选过的不在里面。

    Two queries for a whole page (handles -> users, users -> profiles), never one
    per row: a list, its comments and its notes are all drawn at once, so a
    per-row lookup would be twenty round-trips for twenty faces.

    Lives beside ``user_by_handle`` rather than in the calling domain because
    "which avatar did this person pick" is a fact about `UserProfile`. A caller
    that copied this in would also have to copy the one rule that is easy to get
    wrong — a person who never picked one is **absent from the mapping**, not
    mapped to the global default, since every registration path hardcodes that
    default and returning it would hand twenty people the same face. The
    criterion itself is ``UserProfileRepository.chosen_avatar_ids``; it
    recognises the default row by ``avatar_type``, which is seed data and so
    differs per environment.
    """
    wanted = {h for h in handles if h}
    if not wanted:
        return {}
    users = await UserRepository(session).get_by_handles(sorted(wanted))
    chosen = await UserProfileRepository(session).chosen_avatar_ids(
        [u.id for u in users.values()]
    )
    return {
        handle: chosen[user.id] for handle, user in users.items() if user.id in chosen
    }


async def faces_by_handle(
    session: AsyncSession, handles: Iterable[str]
) -> dict[str, tuple[str | None, int | None]]:
    """handle -> (昵称, 自己挑过的头像 id)，平台上没有这个 handle 的不在里面。

    和 ``chosen_avatars_by_handle`` 走同一条实现路径 —— 一次把 handle 翻成 user，
    再一次把（昵称, 头像）一起取回来，**总共两条查询**，名单多长都是两条。分成
    「查昵称」「查头像」两次会让同一个 join 写两遍，而「哪一行是默认头像」这条规则
    也就多了一个漂开的机会（现状：`UserProfileRepository` 里只此一处）。

    和 ``chosen_avatars_by_handle`` 的唯一差别是**缺省怎么写**：那边「没挑过」就
    整条不见，调用方据此画彩色首字母；这边一行的名字和脸要一起画，所以**有账号的
    人一定在映射里**（没昵称、没挑过就是 (None, None)），只有平台上根本没有这个
    handle 时整条缺失 —— 部署配置里写错一个名字是允许的，那一行仍然是名单的一份。

    放在这里而不是调用方：和旁边两个一样，「这个账号叫什么、有没有挑过头像」是
    `User` / `UserProfile` 的事实，写一份才不会两边各答一次「删掉的账号算不算」。
    """
    wanted = {h for h in handles if h}
    if not wanted:
        return {}
    users = await UserRepository(session).get_by_handles(sorted(wanted))
    profiles = await UserProfileRepository(session).nickname_and_avatar_by_user_id(
        [u.id for u in users.values()]
    )
    return {
        handle: profiles.get(user.id, (None, None)) for handle, user in users.items()
    }


def normalize_nickname(raw: str) -> str:
    """Trim a user-supplied nickname and reject the unusable ones.

    Length beyond emptiness is not constrained on purpose: the only rules are
    non-empty, within the storage bound, and containing something readable.
    """
    nickname = raw.strip()
    if not nickname:
        raise UnprocessableEntityError("Nickname must not be empty")
    if len(nickname) > NICKNAME_MAX_LENGTH:
        raise UnprocessableEntityError(
            f"Nickname must be at most {NICKNAME_MAX_LENGTH} characters"
        )
    if not _NICKNAME_MEANINGFUL_RE.search(nickname):
        raise UnprocessableEntityError(
            "Nickname must contain at least one letter, digit or Chinese character"
        )
    return nickname


class UserService:
    """Read-only operations for user profiles (used by other domains)."""

    def __init__(self, repo: UserProfileRepository) -> None:
        self._repo = repo

    async def get_users_by_ids(self, ids: Sequence[int]) -> dict[int, UserProfile]:
        return await self._repo.get_profiles_by_user_ids(ids)


class AccountService:
    """账号表（`User`）上的读 —— 平台看板问「有多少账号、这七天来了几个」。

    **Why this one takes a session and its three neighbours take a repository:**
    它们三个的调用点只有用户自己的路由，而路由手里本来就已经握着那几个仓储（它们
    还要用它做别的事），把仓储传进来省一次构造。这一个开给的是**别的领域**，而那些
    领域不许 import `UserRepository` —— 建仓储这一步留在门里面，越界才不成立。所以
    它拿 session，和 `FeedbackService` / `AdminService` 同一个形状。

    它面对的是账号表而不是 profile：旁边三个类全是「资料」那一侧（昵称、头像、
    关注、实名），而账号的存量与新增问的是 `User` 自己的行。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = UserRepository(session)

    async def count_accounts(self) -> int:
        return await self._repo.count_accounts()

    async def count_accounts_by_kind(self) -> dict[str, int]:
        """真人 / agent 的存量拆分。判据是 `agent_bindings`（与
        `IdentityService.is_agent` 同一份），见
        `UserRepository.count_accounts_by_kind`。"""
        return await self._repo.count_accounts_by_kind()

    async def accounts_series(
        self, *, since: datetime, until: datetime
    ) -> dict[date, int]:
        """窗口内按 UTC 的天新增的账号数，稀疏；补 0 由调用方做。"""
        return await self._repo.accounts_series(since=since, until=until)

    async def accounts_series_by_kind(
        self, *, since: datetime, until: datetime
    ) -> dict[str, dict[date, int]]:
        """同 `accounts_series`，但真人 / agent 各一条。"""
        return await self._repo.accounts_series_by_kind(since=since, until=until)


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
        if nickname is not None:
            nickname = normalize_nickname(nickname)
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

        # SRP users cannot authenticate via legacy password
        if user.hashed_password.startswith("SRP:"):
            return None

        if not await asyncio.to_thread(
            bcrypt.checkpw,
            password.encode("utf-8"),
            user.hashed_password.encode("utf-8"),
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
        hashed = (
            await asyncio.to_thread(
                bcrypt.hashpw, new_password.encode("utf-8"), bcrypt.gensalt()
            )
        ).decode("utf-8")
        await self._user_repo.update_password(user_id, hashed)

    async def set_srp_credentials(
        self, user_id: int, srp_salt: str, srp_verifier: str
    ) -> None:
        await self._user_repo.update_password(user_id, f"SRP:{srp_salt}:{srp_verifier}")

    @staticmethod
    def _reject_reserved(username: str) -> None:
        """A handle the platform already uses to mean "not a person" (#345).

        Lives here rather than in the route because all three registration
        entry points converge on this service — and because 芝士's own rows go
        through the repository instead, which is exactly the bypass that must
        keep working (`IdentityService._create_agent_user` creates `cheese` and
        `cheese-<topic>`).
        """
        if is_reserved_username(username):
            raise ValueError("USERNAME_RESERVED")

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
        self._reject_reserved(username)
        if await self._user_repo.is_username_taken(username):
            raise ValueError("USERNAME_TAKEN")
        if await self._user_repo.is_email_taken(email):
            raise ValueError("EMAIL_TAKEN")

        hashed = (
            await asyncio.to_thread(
                bcrypt.hashpw, password.encode("utf-8"), bcrypt.gensalt()
            )
        ).decode("utf-8")
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
        self._reject_reserved(username)
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

    async def register_oauth_decision(
        self,
        *,
        email: str,
        username: str,
        nickname: str,
        srp_salt: str | None = None,
        srp_verifier: str | None = None,
        default_avatar_id: int = 1,
    ) -> tuple[User, UserProfile]:
        """Create the account chosen on the OAuth decision page. The user picked
        the username/nickname and optionally set a password (SRP credentials);
        without one the account authenticates solely through the provider."""
        self._reject_reserved(username)
        hashed = f"SRP:{srp_salt}:{srp_verifier}" if srp_salt and srp_verifier else None
        user = await self._user_repo.create_user(
            username=username,
            email=email,
            hashed_password=hashed,
        )
        profile = await self._profile_repo.create_profile(
            user_id=user.id,
            nickname=nickname or username,
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
        """Map User + UserProfile into a UserDto-compatible dict with counts & follow flag."""  # noqa: E501
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
