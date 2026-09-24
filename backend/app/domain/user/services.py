import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import is_placeholder_email
from app.core.errors import UnprocessableEntityError
from app.domain.identity.handles import is_reserved_username
from app.domain.user.models import User, UserProfile
from app.domain.user.passwords import (
    check_password,
    hash_password,
    password_too_long,
)
from app.domain.user.repositories import (
    UserFollowingRepository,
    UserProfileRepository,
    UserRepository,
    UserStatisticsRepository,
)
from app.domain.user.srp_verifier import srp_password_matches

USERNAME_MIN_LENGTH = 4
USERNAME_MAX_LENGTH = 32
_USERNAME_RE = re.compile(
    rf"[a-zA-Z0-9_-]{{{USERNAME_MIN_LENGTH},{USERNAME_MAX_LENGTH}}}"
)

NICKNAME_MAX_LENGTH = 50

# A nickname must carry at least one letter, digit or CJK ideograph, so that a
# name made only of punctuation or invisible characters cannot be saved.
_NICKNAME_MEANINGFUL_RE = re.compile(r"[0-9A-Za-z㐀-䶿一-鿿]")


async def user_by_handle(session: AsyncSession, handle: str) -> User | None:
    return await UserRepository(session).get_by_username(handle)


async def users_by_handle(
    session: AsyncSession, handles: Iterable[str]
) -> dict[str, User]:
    """handle -> 活着的 User 行；平台上没有（或已注销）的不在映射里。

    `faces_by_handle` 答「这个人叫什么、长什么样」，这里答「这个账号存不存在、
    什么时候注册的」——成员管理那份名单要把「死权限」（配置里写了、平台上没
    这个人）画出来。批量一条查询（`UserRepository.get_by_handles`，自带
    `deleted_at IS NULL`），不按 handle N+1。

    放在 user 域而不是调用方，和 ``faces_by_handle`` 是同一个理由：「账号存不
    存在」是 `User` 的事实，而判据（删掉的算不算）只写一份才不会两处各答一次。
    """
    wanted = {h for h in handles if h}
    if not wanted:
        return {}
    return await UserRepository(session).get_by_handles(sorted(wanted))


async def handle_is_taken(session: AsyncSession, handle: str) -> bool:
    """Whether a user or a team already holds ``handle`` — one namespace."""
    return await UserRepository(session).is_username_taken(handle)


def handle_is_available_shape(handle: str) -> bool:
    """A handle anyone may take: a username's alphabet, and no reserved name."""
    return is_valid_username(handle) and not is_reserved_username(handle)


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


async def usernames_by_ids(
    session: AsyncSession, user_ids: Iterable[int]
) -> dict[int, str]:
    """用户 id -> handle, keyed, for callers that need to look each one up."""
    users = await UserRepository(session).get_by_ids(list(set(user_ids)))
    return {uid: user.username for uid, user in users.items()}


async def lookup_account(session: AsyncSession, q: str) -> dict | None:
    """``{handle, name, avatar_id}`` for an exact username or email, or None."""
    found = await UserRepository(session).lookup_account(q)
    if found is None:
        return None
    handle, name, avatar_id = found
    return {"handle": handle, "name": name, "avatar_id": avatar_id}


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


def is_valid_username(username: str) -> bool:
    """The one username rule every registration entry point applies."""
    return _USERNAME_RE.fullmatch(username) is not None


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

    async def count_accounts_between(self, *, since: datetime, until: datetime) -> int:
        """窗口内新增的账号总数（`accounts_series` 的合计版，看板环比用）。"""
        return await self._repo.count_accounts_between(since=since, until=until)


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
        """Validate username/password against the stored credential.

        Returns (user, profile) when successful; otherwise None.
        """
        user = await self._user_repo.get_by_username(username)
        if user is None or not await self.verify_password(user, password):
            return None

        profile = await self._profile_repo.get_profile_by_user_id(user.id)
        if profile is None:
            # In the Kotlin / NestJS world every active user should have a profile.
            # If it is missing, treat as authentication failure for now.
            return None

        return user, profile

    async def verify_password(self, user: User, password: str) -> bool:
        """Check ``password`` against the user's stored credential.

        An SRP record is checked by recomputing its verifier and, on a match,
        replaced with a bcrypt hash. A password bcrypt cannot hold (over 72
        bytes) is accepted but leaves the SRP record in place.
        """
        stored = user.hashed_password or ""
        if not stored or not password:
            return False
        if not stored.startswith("SRP:"):
            return await check_password(password, stored)
        if not srp_password_matches(stored, user.username, password):
            return False
        if not password_too_long(password):
            await self._user_repo.update_password(
                user.id, await hash_password(password)
            )
        return True

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
        hashed = await hash_password(new_password)
        await self._user_repo.update_password(user_id, hashed)

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
        """Create a new user whose password is stored as a bcrypt hash.

        The email code is the caller's to check; this does not send mail.
        """
        self._reject_reserved(username)
        if await self._user_repo.is_username_taken(username):
            raise ValueError("USERNAME_TAKEN")
        if await self._user_repo.is_email_taken(email):
            raise ValueError("EMAIL_TAKEN")

        hashed = await hash_password(password)
        return await self._create_account(
            username=username,
            email=email,
            hashed_password=hashed,
            nickname=nickname,
            avatar_id=default_avatar_id,
        )

    async def register_oauth_decision(
        self,
        *,
        email: str,
        username: str,
        nickname: str,
        password: str | None = None,
        default_avatar_id: int = 1,
    ) -> tuple[User, UserProfile]:
        """Create the account chosen on the OAuth decision page. The user picked
        the username/nickname and optionally set a password; without one the
        account authenticates solely through the provider."""
        self._reject_reserved(username)
        hashed = await hash_password(password) if password else None
        return await self._create_account(
            username=username,
            email=email,
            hashed_password=hashed,
            nickname=nickname or username,
            avatar_id=default_avatar_id,
        )

    async def _create_account(
        self,
        *,
        username: str,
        email: str,
        hashed_password: str | None,
        nickname: str,
        avatar_id: int,
    ) -> tuple[User, UserProfile]:
        """Insert the user and its profile; the unique indexes decide.

        The callers' taken-checks only give an earlier answer: two requests can
        both pass them. The loser's insert fails on ``uq_user_username_lower``
        or ``uq_user_email_lower`` and gets the same errors the checks raise.
        """
        try:
            user = await self._user_repo.create_user(
                username=username,
                email=email,
                hashed_password=hashed_password,
            )
        except IntegrityError:
            if await self._user_repo.is_username_taken(username):
                raise ValueError("USERNAME_TAKEN") from None
            if await self._user_repo.is_email_taken(email):
                raise ValueError("EMAIL_TAKEN") from None
            raise
        profile = await self._profile_repo.create_profile(
            user_id=user.id,
            nickname=nickname,
            intro="",
            avatar_id=avatar_id,
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
        if viewer_id == user.id:
            # Only the owner is told: an account without an address of its own
            # must add one before it can be recovered.
            base["emailMissing"] = is_placeholder_email(user.email)

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
