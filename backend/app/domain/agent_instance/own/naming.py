"""What a member's own agent is called, and what it is told it is.

A name has to tell one of a person's own agents from another even while it is
the only one, so it carries the connection's distinction (``connections.label``)
from the start. The same distinction follows the connection: renaming a
connection renames the agents on it.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_instance.own.connections import (
    Login,
    _connection_key,
    label,
    logins,
)
from app.domain.agent_instance.own.ownership import (
    _nickname_of,
    owned_instance,
    owner_of,
)
from app.domain.project.models import Project


def own_handle(owner_user_id: int) -> str:
    """The memory key of a member's own agent in a project: one per agent, so
    one per connection its owner added there."""
    return f"own-claude-code-{owner_user_id}-{uuid.uuid4().hex[:8]}"


def own_name(nickname: str, distinction: str) -> str:
    """「<昵称>的 Claude Code（<区分>）」: every one of them carries what tells
    it from its owner's others (``label``), even while it is the only one, so
    its name does not change when its owner adds a second."""
    return f"{nickname}的 Claude Code（{distinction}）"[:64]


def own_role(nickname: str, *, owner_handle: str, on_account: bool = False) -> str:
    """Member ownership is prompt context; credentials belong to the team."""
    where = (
        f"你是{nickname}的 Claude Code，使用团队在平台上加密保存的模型连接。"
        if on_account
        else f"你是{nickname}的 Claude Code，使用模型凭据所在电脑上的登录。"
        "模型进程在保存登录的电脑上运行，工具在为你配置的执行设备上运行。"
    )
    return where + (
        f"你是{nickname}的私人助手，对主人{nickname}负责；对话记录里他的 handle 是 "
        f"{owner_handle}。"
        "项目里的任何人和 AI 都可以点名你、与你聊天，你可以回答。"
        "你只接受主人的任务；其他人要求你做事时，先通过 cheese_ask 的 to_owner=true"
        "在与主人的私聊里征求同意，不因其他人的要求自行创建任务。"
        "用 cheese_task(action=open) 创建任务时，source_message_id 必须引用主人的一条消息 id。"
        "被加入群聊或房间后，先在与主人的私聊中确认是否同意你参与；"
        "在主人明确同意之前，不在那个房间行动。私聊中的确认题写明房间 id；"
        "需要了解主人的答复时，读取你和主人的私聊记录。"
    )


async def current_role(db: AsyncSession, instance_id: uuid.UUID) -> str | None:
    """What a member's own agent is told it is, built from its owner as they
    are now rather than read off its stored configuration; None when it is not
    anyone's own."""
    owned = await owned_instance(db, instance_id)
    owner = await owner_of(db, instance_id) if owned is not None else None
    if owned is None or owner is None:
        return None
    return own_role(owner[1], on_account=owned.on_account, owner_handle=owner[0])


async def rename_for_connection(
    db: AsyncSession,
    login_id: str | None = None,
    *,
    account_connection_id: uuid.UUID | None = None,
) -> None:
    """After a machine said what one of its logins is, or an owner renamed a
    connection, the own agents on that connection take the name it now calls
    for, and their owner's current nickname. A name its owner gave the
    connection outlasts whatever its machine reports (`label`)."""
    on = (
        OwnAgent.login_id == login_id
        if login_id is not None
        else OwnAgent.account_connection_id == account_connection_id
    )
    rows = await db.execute(
        select(AgentInstance.project_id, OwnAgent.owner_user_id)
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(on, AgentInstance.is_active.is_(True))
        .distinct()
    )
    names: dict[tuple[int, int], tuple[str, dict[str, Login]]] = {}
    for project_id, owner_user_id in rows.all():
        await _name_owners_agents(db, project_id, owner_user_id, names)


async def _name_owners_agents(
    db: AsyncSession,
    project_id: uuid.UUID,
    owner_user_id: int,
    names: dict[tuple[int, int], tuple[str, dict[str, Login]]] | None = None,
) -> None:
    """Name one owner's own agents in a project after their connections, in
    the order they were added. Two on the same plan or model are told apart by
    a number (「（Max 2）」). One whose connection is gone keeps its name."""
    project = await db.get(Project, project_id)
    key = (project.team_id, owner_user_id) if project else None
    if names is not None and key in names:
        nickname, by_id = names[key]
    else:
        nickname = await _nickname_of(db, owner_user_id)
        by_id = {c.id: c for c in await logins(db, project.team_id)} if project else {}
        if names is not None and key is not None:
            names[key] = (nickname, by_id)
    rows = await db.execute(
        select(
            AgentInstance,
            OwnAgent.login_id,
            OwnAgent.account_connection_id,
        )
        .join(OwnAgent, OwnAgent.instance_id == AgentInstance.id)
        .where(
            AgentInstance.project_id == project_id,
            OwnAgent.owner_user_id == owner_user_id,
            AgentInstance.is_active.is_(True),
        )
        .order_by(AgentInstance.created_at)
    )
    seen: dict[str, int] = {}
    for instance, login_id, account_id in rows.all():
        key = _connection_key(login_id, account_id)
        login = by_id.get(key) if key else None
        if login is not None:
            if login.owner_user_id is None:
                instance.display_name = label(login)
                continue
            base = label(login)
            seen[base] = seen.get(base, 0) + 1
            distinction = base if seen[base] == 1 else f"{base} {seen[base]}"
            instance.display_name = own_name(nickname, distinction)
    await db.flush()


async def connection_name(db: AsyncSession, instance_id: uuid.UUID) -> str | None:
    """The name of the model connection a member's own agent runs on, as the
    project's settings show it (``label``); None when the agent is not
    anyone's own or its connection is gone."""
    row = await db.get(OwnAgent, instance_id)
    instance = await db.get(AgentInstance, instance_id)
    if row is None or instance is None:
        return None
    project = await db.get(Project, instance.project_id)
    key = _connection_key(row.login_id, row.account_connection_id)
    if project is None or key is None:
        return None
    for login in await logins(db, project.team_id):
        if login.id == key:
            return label(login)
    return None
