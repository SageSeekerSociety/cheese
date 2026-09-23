"""Project data access."""

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.domain.avatars.models import Avatar
from app.domain.project.models import (
    AiMode,
    Project,
    ProjectForge,
    ProjectGitInstallation,
    ProjectMember,
)
from app.domain.team.models import Team, TeamUserRelation
from app.domain.user.models import User, UserProfile


class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        name: str,
        owner_handle: str | None = None,
        ai_mode: AiMode = AiMode.collaborative,
        team_id: int | None = None,
        external_task_id: int | None = None,
    ) -> Project:
        project = Project(
            name=name,
            owner_handle=owner_handle,
            ai_mode=ai_mode,
            team_id=team_id,
            external_task_id=external_task_id,
        )
        self._session.add(project)
        await self._session.flush()
        await self._session.refresh(project)
        return project

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def team_for_project(self, project_id: uuid.UUID) -> int | None:
        """The owning team, including a personal team's older unassigned projects."""
        project = await self.get(project_id)
        if project is None:
            return None
        if project.team_id is not None:
            return project.team_id
        return await self._session.scalar(
            select(Team.id)
            .join(User, User.id == Team.personal_owner_user_id)
            .where(User.username == project.owner_handle, Team.deleted_at.is_(None))
        )

    async def teams_for_projects(
        self, projects: list[Project]
    ) -> dict[uuid.UUID, int | None]:
        """`team_for_project` 的批量版：一批项目 → 各自的所属小队。

        `team_id` 非空的直接取值，不发查询；为空的那批（个人小队的旧项目）用
        **一条** `Team JOIN User` 把 owner_handle → team_id 的映射一次查回，
        而不是逐项目各发一条 —— 管理页的项目额度表靠它把整段查询从 2N+1
        降到 3。
        """
        out: dict[uuid.UUID, int | None] = {}
        orphan_handles: set[str] = set()
        orphan_ids: dict[str, list[uuid.UUID]] = {}
        for project in projects:
            out[project.id] = project.team_id
            if project.team_id is None and project.owner_handle:
                orphan_handles.add(project.owner_handle)
                orphan_ids.setdefault(project.owner_handle, []).append(project.id)
        if orphan_handles:
            rows = await self._session.execute(
                select(User.username, Team.id)
                .join(User, User.id == Team.personal_owner_user_id)
                .where(
                    User.username.in_(orphan_handles), Team.deleted_at.is_(None)
                )
            )
            for username, team_id in rows.all():
                for project_id in orphan_ids.get(username, []):
                    out[project_id] = team_id
        return out

    async def get_by_team(self, team_id: int) -> Project | None:
        """The AI-workspace project for a 知是 Team (P4 native link), newest first."""
        stmt = (
            select(Project)
            .where(Project.team_id == team_id)
            .order_by(Project.created_at.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    async def list_all(self) -> list[Project]:
        stmt = select(Project).order_by(Project.created_at.desc())
        return list((await self._session.scalars(stmt)).all())

    async def list_by_team(self, team_id: int) -> list[Project]:
        """All projects owned by a team (项目归团队), newest first."""
        stmt = (
            select(Project)
            .where(Project.team_id == team_id)
            .order_by(Project.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_personal_legacy(self, owner_handle: str) -> list[Project]:
        """Pre-personal-team rows: team-less projects of one owner. New personal
        projects carry the personal team's id; these are the NULL-team leftovers
        that must still show on the personal team's 项目 page."""
        stmt = (
            select(Project)
            .where(Project.team_id.is_(None), Project.owner_handle == owner_handle)
            .order_by(Project.created_at.desc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def count(self) -> int:
        return int(
            (await self._session.scalar(select(func.count()).select_from(Project))) or 0
        )

    async def set_root_topic(self, project: Project, root_topic_id: uuid.UUID) -> None:
        project.root_topic_id = root_topic_id
        await self._session.flush()

    async def set_summary(self, project: Project, summary: str) -> None:
        project.summary = summary
        await self._session.flush()

    async def set_settings(self, project: Project, settings: dict[str, object]) -> None:
        """Replace the free-form settings blob. Callers merge — assigning a NEW
        dict is what makes SQLAlchemy see the change (the column is plain JSON,
        so an in-place mutation is invisible to the unit of work and silently
        never persists)."""
        project.settings = settings
        await self._session.flush()

    async def people(self, project_id: uuid.UUID) -> list[dict]:
        """名册上**人**的那一半：每个人的 handle、昵称、头像和角色。

        「这个项目里有谁」不在这里回答，在 ``membership/roster.py`` 的 ``roster()``
        ——这里只是它的两个来源之一，另一个是这个项目的队友。名字从 ``list_members``
        改成 ``people``，正是为了让「拿它当整张名册」在调用点就读得出不对。

        人这一半自己也有三个记录处 —— 成员行、所属小队、``owner_handle`` —— 三个都
        要读：所有者**故意**不写成员行（``ProjectService._seed_roster`` 说明了原因），
        所以少了这里补出来的那一行，一个成员表为空的项目里，它的所有者谁也看不见：
        他自己说的话渲染成一串裸 handle、没有头像，@ 他解析不到人、也通知不到谁。
        这是读出来的投影，不往表里放任何东西。
        """
        # Display name lives on UserProfile.nickname (main's User has only
        # username); join both, keyed by handle == username (fusion identity).
        # ``avatar_id`` rides along from the same profile row and is None here
        # in two cases, both of which mean "render the colored initial":
        #
        #   1. the outer join found no profile (a handle with no fusion user);
        #   2. the profile still points at the *global default* avatar.
        #
        # Case 2 is not a choice anyone made: every registration path hardcodes
        # ``default_avatar_id: int = 1`` (``domain/user/services.py``), as does
        # 芝士's own profile (``domain/identity/services.py``). Reporting that id
        # would make every member who never picked an avatar share one face —
        # strictly worse at telling people apart than the per-handle hashed
        # initial, which is the whole job of an avatar in a chat panel. So the
        # roster's contract is "avatar_id = the avatar this person chose, null if
        # they never chose one"; the join reads ``avatar_type`` rather than
        # comparing against a literal 1, because which row is the default is
        # seed data and differs per environment.
        stmt = (
            select(
                ProjectMember.user_handle,
                ProjectMember.role,
                UserProfile.nickname,
                UserProfile.avatar_id,
                Avatar.avatar_type,
            )
            .join(User, User.username == ProjectMember.user_handle, isouter=True)
            .join(UserProfile, UserProfile.user_id == User.id, isouter=True)
            .join(Avatar, Avatar.id == UserProfile.avatar_id, isouter=True)
            .where(ProjectMember.project_id == project_id)
        )
        rows = (await self._session.execute(stmt)).all()
        members = [
            {
                "handle": h,
                "role": str(role),
                "name": name or h,
                "avatar_id": None if avatar_type == "default" else avatar_id,
            }
            for (h, role, name, avatar_id, avatar_type) in rows
        ]
        project = await self.get(project_id)
        if project is None:
            return members
        explicit = {m["handle"] for m in members}
        if project.owner_handle and project.owner_handle not in explicit:
            name, avatar_id, avatar_type = await self._profile_of(project.owner_handle)
            # Front of the list: the owner is the first person a reader of the
            # roster is looking for. ``lead`` because that is what the owner can
            # do (manage the roster) expressed in the only vocabulary this field
            # has — ProjectRole carries no separate owner value.
            members.insert(
                0,
                {
                    "handle": project.owner_handle,
                    "role": "lead",
                    "name": name or project.owner_handle,
                    "avatar_id": None if avatar_type == "default" else avatar_id,
                    "source": "owner",
                },
            )
            explicit.add(project.owner_handle)
        if project.team_id is None:
            return members
        # Team access is inherited at read time, including teammates who join
        # after registration. Do not persist a second grant that survives leaving.
        team_rows = (
            await self._session.execute(
                select(
                    User.username,
                    UserProfile.nickname,
                    UserProfile.avatar_id,
                    Avatar.avatar_type,
                    TeamUserRelation.created_at,
                )
                .join(TeamUserRelation, TeamUserRelation.user_id == User.id)
                .outerjoin(UserProfile, UserProfile.user_id == User.id)
                .outerjoin(Avatar, Avatar.id == UserProfile.avatar_id)
                .where(
                    TeamUserRelation.team_id == project.team_id,
                    TeamUserRelation.deleted_at.is_(None),
                    User.deleted_at.is_(None),
                )
            )
        ).all()
        for handle, name, avatar_id, avatar_type, created_at in team_rows:
            if handle in explicit:
                continue
            members.append(
                {
                    "handle": handle,
                    "role": "member",
                    "name": name or handle,
                    "avatar_id": None if avatar_type == "default" else avatar_id,
                    "source": "team",
                    "team_id": project.team_id,
                    "created_at": created_at.isoformat(),
                }
            )
        return members

    async def _profile_of(
        self, handle: str
    ) -> tuple[str | None, int | None, str | None]:
        """``(nickname, avatar_id, avatar_type)`` for a handle, all None when no
        fusion user stands behind it — a handle that names nobody is normal here
        (agents, fixtures, a project handed to a handle that never registered),
        and the roster row still exists, it just falls back to the handle."""
        row = (
            await self._session.execute(
                select(UserProfile.nickname, UserProfile.avatar_id, Avatar.avatar_type)
                .select_from(User)
                .outerjoin(UserProfile, UserProfile.user_id == User.id)
                .outerjoin(Avatar, Avatar.id == UserProfile.avatar_id)
                .where(User.username == handle)
            )
        ).first()
        return (None, None, None) if row is None else (row[0], row[1], row[2])

    async def list_ids_for_space_tasks(self, space_id: int) -> list[uuid.UUID]:
        """Project ids for every 赛题 published under this Space (机构看板).

        One query rather than a walk: the 赛题 already carry `space_id`, and a
        project names the 赛题 it was created from, so the board is a join and
        not a four-level traversal through a parallel hierarchy (#370).
        """
        from app.domain.task.models import Task

        stmt = (
            select(Project.id)
            .where(
                Project.external_task_id.in_(
                    select(Task.id).where(Task.space_id == space_id)
                )
            )
            .order_by(Project.created_at.asc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_for_space(self, space_id: int) -> list[Project]:
        """Every project anchored on a 赛题 published under this Space.

        ``list_ids_for_space_tasks`` next door is the id-only twin (机构看板).
        A course needs the rows themselves — who each one belongs to and which
        team it is — so this is the same join returning the projects.
        """
        from app.domain.task.models import Task

        stmt = (
            select(Project)
            .where(
                Project.external_task_id.in_(
                    select(Task.id).where(Task.space_id == space_id)
                )
            )
            .order_by(Project.created_at.asc(), Project.id.asc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_for_space_and_owner(
        self, *, space_id: int, owner_handle: str
    ) -> list[Project]:
        """One person's projects created from 赛题 published under this Space.

        This is the course link's own question — 「他在这门课里已经有项目了吗」.
        Asking it by anchor 赛题 would answer it wrong: the anchor is whichever 题
        the course hangs its projects on, and that can move as the course grows.
        Asking by Space keeps 一学期一个项目 true across that move.
        """
        from app.domain.task.models import Task

        stmt = (
            select(Project)
            .where(
                Project.external_task_id.in_(
                    select(Task.id).where(Task.space_id == space_id)
                ),
                Project.owner_handle == owner_handle,
            )
            .order_by(Project.created_at.asc())
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_for_external_task(self, task_id: int) -> list[Project]:
        """Every project created from this 赛题 — the way back the link exists for."""
        result = await self._session.execute(
            select(Project)
            .where(Project.external_task_id == task_id)
            .order_by(Project.created_at)
        )
        return list(result.scalars())

    async def list_visible_to(
        self, *, handle: str | None, user_id: int | None
    ) -> list[Project]:
        """Projects this person has any claim on.

        The listing used to return EVERY project to everyone, which is fine with
        five of them and wrong the moment a class shows up: a student would see
        every other team's work, and every piece of debugging debris, in their
        own sidebar.

        A claim is one of three things — you own it, you are on its roster, or it
        belongs to a team you are in. Anything else is not yours to see here.
        """
        from app.domain.team.models import TeamUserRelation

        claims = []
        if handle:
            claims.append(Project.owner_handle == handle)
            claims.append(
                Project.id.in_(
                    select(ProjectMember.project_id).where(
                        ProjectMember.user_handle == handle
                    )
                )
            )
        if user_id:
            claims.append(
                Project.team_id.in_(
                    select(TeamUserRelation.team_id).where(
                        TeamUserRelation.user_id == user_id
                    )
                )
            )
        if not claims:
            # Nobody in particular is asking; that is not the same as everybody.
            return []
        result = await self._session.execute(
            select(Project).where(or_(*claims)).order_by(Project.created_at)
        )
        return list(result.scalars())


class ProjectGitInstallationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_project(
        self, project_id: uuid.UUID
    ) -> ProjectGitInstallation | None:
        stmt = select(ProjectGitInstallation).where(
            ProjectGitInstallation.project_id == project_id
        )
        return (await self._session.scalars(stmt)).first()

    async def get_by_installation(
        self, installation_id: int
    ) -> ProjectGitInstallation | None:
        stmt = select(ProjectGitInstallation).where(
            ProjectGitInstallation.installation_id == installation_id
        )
        return (await self._session.scalars(stmt)).first()

    async def upsert(
        self,
        *,
        project_id: uuid.UUID,
        installation_id: int,
        repo: str,
        account: str,
    ) -> ProjectGitInstallation:
        """Bind `installation_id` to `project_id` (replacing any prior repo the
        project was connected to). Raises ConflictError if the installation is
        already bound to a *different* project — a GitHub installation is never
        shared, or a minted token would be ambiguous about whose git operations
        it's for."""
        by_installation = await self.get_by_installation(installation_id)
        if by_installation is not None and by_installation.project_id != project_id:
            raise ConflictError(
                f"installation {installation_id} is already connected to "
                f"another project"
            )

        existing = await self.get_by_project(project_id)
        forge = await self._session.scalar(
            select(ProjectForge).where(ProjectForge.project_id == project_id)
        )
        if forge is None:
            forge = ProjectForge(project_id=project_id)
            self._session.add(forge)
        elif forge.kind == "forgejo":
            raise ConflictError("这个项目已有代码仓库；跨托管服务迁移尚未开放")
        forge.kind = "github_app"
        forge.repo = repo
        forge.url = f"https://github.com/{repo}.git"
        forge.api_url = "https://api.github.com"
        forge.default_branch = ""  # Resolved from the provider, never assumed main.
        if existing is not None:
            existing.installation_id = installation_id
            existing.repo = repo
            existing.account = account
            await self._session.flush()
            return existing

        row = ProjectGitInstallation(
            project_id=project_id,
            installation_id=installation_id,
            repo=repo,
            account=account,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row
