"""待我处理里不属于哪个项目的那几件：有人邀请我、有人想把项目转让给我、有人申请加入
我管的团队，以及我出的题目上等我批准的领取和等我批改的提交。

它们和 `awaiting_deps.waiting_items` 是同一份清单的另一半：那一半按项目里的事实现
算（任务、验收卡、提问、拍板），这一半按邀请和申请现在的状态现算 —— 还在等回答的
就在，答了、撤了、过期了就不在。不读通知表，理由和 `room_task/awaiting.py` 写的一
样：通知是一条事件记录，读不出那件事后来是不是已经了结了。

谁会看到哪一件，和当初通知发给谁是同一个名单：项目邀请给被邀请的人
（`membership/services.py` 的 `_notify_invitee`），团队邀请给被邀请的人，加入申请给
那个团队的所有者和管理员（`team/membership_services.py`）。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.device import mcp_machines
from app.domain.membership.services import (
    pending_invitations_for,
    pending_transfers_for,
)
from app.domain.project.services import ProjectService
from app.domain.remote_mcp import local as mcp_local
from app.domain.space.teaching_queue import teaching_queue
from app.domain.team import membership_services as team_applications
from app.domain.team.models import TeamMembershipApplication
from app.domain.team.services import team_service
from app.domain.user.services import faces_by_handle, usernames_by_ids

AskKind = Literal["project_invite", "project_transfer", "team_invite", "join_request"]


@dataclass(frozen=True, slots=True)
class Face:
    """一行上画的一个人或一个团队：名字、地址里的名字、自己挑过的头像。"""

    name: str
    handle: str | None
    avatar_url: str | None

    def as_dict(self) -> dict:
        return {"name": self.name, "handle": self.handle, "avatarUrl": self.avatar_url}


@dataclass(frozen=True, slots=True)
class MembershipAsk:
    """一件等这个人回答的邀请或申请。"""

    kind: AskKind
    #: 回答时交回去的那个号：项目邀请是 uuid，团队的邀请和申请是数字。
    id: str
    #: 加入的是哪里：项目，或团队（团队带着它的 id，批准申请的接口要它）。
    place_id: str
    place: Face
    #: 发起的人：邀请人，或提出申请的人。
    person: Face
    at: datetime
    #: 邀请给的角色：团队邀请是 `MEMBER` / `ADMIN` / `OWNER`，项目邀请是
    #: `member` / `mentor`；其余两种没有。
    role: str | None = None
    #: 邀请或申请时附的那句话；没写就空。
    message: str = ""
    #: 邀请是发起人自己的芝士代他发的。
    by_assistant: bool = False

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "placeId": self.place_id,
            "place": self.place.as_dict(),
            "person": self.person.as_dict(),
            "role": self.role,
            "message": self.message,
            "byAssistant": self.by_assistant,
            "at": self.at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class TaskAsk:
    """A 题目 this person teaches with claims to approve or submissions to
    review (`space.teaching_queue.teaching_queue`)."""

    task_id: int
    task_name: str
    space_id: int
    space_name: str
    claims: int
    reviews: int
    at: datetime
    kind: Literal["task_queue"] = "task_queue"

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "taskId": self.task_id,
            "taskName": self.task_name,
            "spaceId": self.space_id,
            "spaceName": self.space_name,
            "claims": self.claims,
            "reviews": self.reviews,
            "at": self.at.isoformat(),
        }


async def task_asks(db: AsyncSession, *, user_id: int | None) -> list[TaskAsk]:
    """What waits on the 题目 this person teaches, one row per 题目."""
    if not user_id:
        return []
    return [
        TaskAsk(
            task_id=w.task_id,
            task_name=w.task_name,
            space_id=w.space_id,
            space_name=w.space_name,
            claims=w.claims,
            reviews=w.reviews,
            at=w.at,
        )
        for w in await teaching_queue(db, user_id)
    ]


def _avatar(avatar_id: int | None) -> str | None:
    """自己挑过的头像的地址；没挑过就没有，界面画彩色首字母。"""
    if avatar_id is None:
        return None
    return f"{settings.avatar_base_url.rstrip('/')}/avatars/{avatar_id}"


async def membership_asks(
    db: AsyncSession, *, handle: str, user_id: int | None
) -> list[MembershipAsk]:
    """等 `handle` 回答的邀请和申请，新的在前。"""
    invitations = await pending_invitations_for(db, handle)
    transfers = await pending_transfers_for(db, handle)
    team_invites: list[TeamMembershipApplication] = []
    join_requests: list[TeamMembershipApplication] = []
    if user_id is not None:
        team_invites = await team_applications.pending_invitations_for(db, user_id)
        join_requests = await team_applications.pending_requests_answerable_by(
            db, user_id
        )
    if not (invitations or transfers or team_invites or join_requests):
        return []

    # 每种实体一次取完：一屏上的邀请人、申请人、团队、项目各问一趟。
    projects = await ProjectService(db).get_projects_by_ids(
        [i.project_id for i in invitations] + [t.project_id for t in transfers]
    )
    teams_of = team_service(db)
    team_ids = list({a.team_id for a in (*team_invites, *join_requests)})
    teams = await teams_of.get_teams_by_ids(team_ids)
    team_avatars = await teams_of.chosen_avatar_ids(team_ids)
    usernames = await usernames_by_ids(
        db,
        [a.initiator_id for a in team_invites] + [a.user_id for a in join_requests],
    )
    people = await faces_by_handle(
        db,
        [
            *(i.inviter_handle for i in invitations),
            *(t.from_handle for t in transfers),
            *usernames.values(),
        ],
    )

    def person(person_handle: str) -> Face:
        nickname, avatar_id = people.get(person_handle, (None, None))
        return Face(
            name=nickname or person_handle,
            handle=person_handle,
            avatar_url=_avatar(avatar_id),
        )

    asks: list[MembershipAsk] = []
    for invitation in invitations:
        project = projects.get(invitation.project_id)
        if project is None:
            continue
        asks.append(
            MembershipAsk(
                kind="project_invite",
                id=str(invitation.id),
                place_id=str(project.id),
                place=Face(name=project.name, handle=None, avatar_url=None),
                person=person(invitation.inviter_handle),
                at=invitation.created_at,
                role=invitation.role,
                by_assistant=invitation.invited_by_assistant,
            )
        )
    for transfer in transfers:
        project = projects.get(transfer.project_id)
        if project is None:
            continue
        asks.append(
            MembershipAsk(
                kind="project_transfer",
                id=str(transfer.id),
                place_id=str(project.id),
                place=Face(name=project.name, handle=None, avatar_url=None),
                person=person(transfer.from_handle),
                at=transfer.created_at,
            )
        )

    def team_ask(
        kind: AskKind, application: TeamMembershipApplication, person_id: int
    ) -> MembershipAsk | None:
        team = teams.get(application.team_id)
        person_handle = usernames.get(person_id)
        if team is None or person_handle is None:
            return None
        return MembershipAsk(
            kind=kind,
            id=str(application.id),
            place_id=str(team.id),
            place=Face(
                name=team.name,
                handle=team.handle,
                avatar_url=_avatar(team_avatars.get(team.id)),
            ),
            person=person(person_handle),
            at=application.created_at,
            role=application.role if kind == "team_invite" else None,
            message=application.message or "",
        )

    for application in team_invites:
        if ask := team_ask("team_invite", application, application.initiator_id):
            asks.append(ask)
    for application in join_requests:
        if ask := team_ask("join_request", application, application.user_id):
            asks.append(ask)
    asks.sort(key=lambda ask: ask.at, reverse=True)
    return asks


@dataclass(frozen=True, slots=True)
class RunAsk:
    """一个项目要在我自己的电脑上运行一条命令（一个本地 MCP 服务器），等我允许。
    同一条命令允许过就不再问，命令改了再问（`remote_mcp/local.py`）。"""

    id: str
    project_id: str
    project_name: str
    server: str
    command: str
    #: 我的哪台电脑。
    device: str
    #: 命令写在哪：仓库的 `.mcp.json`（repository）、项目设置（settings）
    #: 或队友类型（type）。
    source: str
    #: 谁写的：`.mcp.json` 是最近改它的人，按仓库里写的名字；项目设置是添加它的
    #: 成员；读不出来没有。
    author: Face | None
    at: datetime

    def as_dict(self) -> dict:
        return {
            "kind": "mcp_run",
            "id": self.id,
            "projectId": self.project_id,
            "projectName": self.project_name,
            "server": self.server,
            "command": self.command,
            "device": self.device,
            "source": self.source,
            "author": self.author.as_dict() if self.author else None,
            "at": self.at.isoformat(),
        }


async def run_asks(db: AsyncSession, *, handle: str) -> list[RunAsk]:
    """等 `handle` 允许在自己电脑上运行的命令，新的在前。"""
    owned = await mcp_machines.owned(db, handle)
    waiting = await mcp_local.waiting_for(db, owned)
    if not waiting:
        return []
    projects = await ProjectService(db).get_projects_by_ids(
        list({a.project_id for a in waiting})
    )
    names = await mcp_machines.names(db, owned)
    people = await faces_by_handle(
        db, [a.author for a in waiting if a.source == "settings" and a.author]
    )

    def author(a) -> Face | None:
        if not a.author:
            return None
        if a.source != "settings":
            return Face(a.author, None, None)
        nickname, avatar_id = people.get(a.author, (None, None))
        return Face(nickname or a.author, a.author, _avatar(avatar_id))

    return [
        RunAsk(
            id=str(a.id),
            project_id=str(a.project_id),
            project_name=projects[a.project_id].name,
            server=a.name,
            command=a.command,
            device=names.get(a.device_id, a.device_id),
            source=a.source,
            author=author(a),
            at=a.created_at,
        )
        for a in waiting
        if a.project_id in projects
    ]
