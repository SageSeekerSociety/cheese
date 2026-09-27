"""谁是一个题目板的管理员，以及这条主张能开哪几扇门 —— 一处。

角色只有三种：**所有者 / 管理员 / 成员**。落到库里，所有者与管理员就是
``space_admin_relation`` 里的一行 —— 建版的人（``SpaceService.create_space``
写进去的 OWNER）与创建者后来设的 ADMIN；其余人是成员。这条规则本身早就在
``SpaceRepository.build_membership_predicate`` 里写过一遍（成员行 OR 管理员关系）；
本模块是它的**单点形态**，给「这人管不管得了这个板子」这个是非题用，而不是列表谓词。

为什么单独一个模块、而不是继续往各处路由里写 ``task.creator_id != user_id``：
那条判据是「这道题的出题者」，比「这个板的管理员」窄。一块板上有几十道题、几位
管理员，出题的人会换、管理员也会换；判据一旦按接口各写一遍就会像
``app.auth.project_access`` 顶部记的那样慢慢走样。所以打分、发布、导出参与者三处
不同的门，问的是同一个问题、调的是同一个函数。

**刻意不在这里**的是「谁能看见这个题目板」（``SpaceRepository.is_member``）与
「谁能看见某道题」（``TaskVisibilityService.can_view_task``）—— 前者是成员就有、
后者还认邮箱域名白名单。它们是「看得见」，这里是「管得了」，两者不能互相顶替：
看得见板子不等于管得了它。**发题是这条界线上的例外** —— 重设计之后发题不是管理
动作，``may_publish_in_space`` 问的正是「看得见」，见该函数的说明。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.repositories import (
    SpaceAdminRelationRepository,
    SpaceRepository,
)
from app.domain.task.models import Task
from app.domain.task.repositories import TaskRepository


async def is_space_admin(
    session: AsyncSession, *, space_id: int | None, user_id: int | None
) -> bool:
    """这份 ``space`` 是不是他的：管理员关系里的一行（OWNER 或 ADMIN）。

    ``user_id is None``（没登录/没有数字 id）是 False —— 「没人问」不能读成
    「谁都可以」。``space_id is None``（题还没挂板）同理。

    每次现查，不缓存：撤掉管理员之后下一次调用就得是 False。这不是性能问题，
    是一条权限判断必须活在它被问的那一刻 —— ``project_access`` 的撤权测试测的
    也是同一件事。
    """
    if space_id is None or user_id is None:
        return False
    relation = await SpaceAdminRelationRepository(session).get_relation(
        space_id, user_id
    )
    return relation is not None


async def may_teach_task(session: AsyncSession, *, task: Task, user_id: int) -> bool:
    """能不能动这道题的管理面：出题者本人，**或**它所在板的所有者/管理员。

    两条并列的主张，谁也不从谁推出：出题者可能不是管理员（改成任何人都能出题之后
    更是如此），管理员手上的题也可能不是他自己出的（一块板上几十道题、几位管理员，
    谁出的都可能）。所以这里是 ``or``，删掉任一条都会挡住一个真实的人。

    一题一判据，一题一个函数：评审（建/改/全量替换/删）、替人报名、管报名（改/删）、
    编辑/删除题目、重提审核、看名单、看提交列表 —— 全是「出题人或管理员对这道题能
    做的事」，从前它们各写一遍 ``task.creator_id == user_id`` 或内联的
    ``is_creator and not is_space_admin``，出题者那条有了、管理员那条时有时无。
    """
    if task.creator_id is not None and task.creator_id == user_id:
        return True
    return await is_space_admin(session, space_id=task.space_id, user_id=user_id)


async def may_publish_in_space(
    session: AsyncSession, *, space_id: int | None, user_id: int | None
) -> bool:
    """能不能往这个题目板里发题：**在这个板里的人**都能发。

    题目板是一块「任何人都能出题的板」，所以发题不是管理动作、审核才是。这道门问
    的是「看不看得见这个板」（``SpaceRepository.is_member``：成员行 OR 管理员关系），
    不是「管不管得了它」—— 判据与板子本身可见性同源，一个看得见板子却发不了题的
    人这里不成立。

    权限没有因此放松，只是从「谁能发」挪到了「谁能批」：发出来的题一律 ``approved=2``
    （待审、仓库里写死），上板要过 ``PATCH /tasks/{id}`` 那道只对所有者与管理员开的门。

    从前这里是 ``is_space_admin``（一次收权改动，当时的注释写「只有空间管理员和空间
    创建者能发布题目」）；重设计把那条口径改掉了 —— 现在是「任何人可出题，所有者与
    管理员审核」。
    """
    if space_id is None or user_id is None:
        return False
    return await SpaceRepository(session).is_member(space_id=space_id, user_id=user_id)


async def is_admin_of_projects_task(
    session: AsyncSession, *, external_task_id: int | None, user_id: int | None
) -> bool:
    """这个项目的赛题所挂题目板的管理员 —— ``may_read_project`` 用的第五条主张。

    ``project.external_task_id`` → ``Task.space_id`` → 管理员关系。题不存在、没挂
    板、或人不是管理员都是 False。取的就是「管理员版面」，比出题者那条宽一格：一块
    板的管理员读得到这块板产出的每一个项目，正如出题者读得到自己这道题产出的项目。
    """
    if external_task_id is None:
        return False
    task = await TaskRepository(session).get_by_id(external_task_id)
    if task is None or task.space_id is None:
        return False
    return await is_space_admin(session, space_id=task.space_id, user_id=user_id)
