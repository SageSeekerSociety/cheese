"""学情分析（step 1 的底座）：这块板开不开学情分析、它的知识点清单、以及加入这块
板时那张告知卡。

一个概念 —— 这块板要对它的成员做学情分析，于是它得先说清自己在分析什么（一份知识点
清单）、告诉每个人这件事（一张告知卡，读了才算读过），并且那个开关由管理员来按。第 1
步只到这里：**不生成任何分析结果**，也不给学生看任何东西（每晚分析、学生的「我的学习
洞察」、老师的七个页签都是后面的步骤）。

三件事写在同一个 service 里，因为它们读的是同一块板、同一份权限（``edit_settings``
那个落地在路由上），而且开关关着的时候另两件都没有意义：知识点清单仍是空的可编辑的，
告知卡不再弹。

**模型那一问不在这一层**。AI 知识点建议要调网关、要记账（``usage.ledger``），那是
第 5 层的东西，而这一层（3 层，和 ``space`` 同层）只碰这一域的行。那份在
``app/domain/space/knowledge_suggestions.py``，路由把两边接起来。

权限（这块板归谁）由路由上的 ``Can(SpacePermission.edit_settings)`` 判；这一层只判
事情的形状（名字空不空、重不重、属于不属于这块板），与 ``SpaceService`` 的规矩一致。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.space.models import SpaceKnowledgePoint
from app.domain.space.repositories import (
    SpaceKnowledgePointRepository,
    SpaceLearningNoticeRepository,
)

#: 一条知识点的名字最长多少字 —— 和 ``SpaceKnowledgePoint.name`` 的列宽同一口径。
MAX_NAME_CHARS = 100
#: 一块板最多列多少条。够一个人把一门课的知识点列完，又不至于让设置页和 AI 建议
#: 的输入长到不像话。
MAX_POINTS = 200


def _clean_name(raw: object) -> str:
    name = str(raw or "").strip()
    if not name:
        raise BadRequestError(say("learningPointNameRequired"))
    if len(name) > MAX_NAME_CHARS:
        raise BadRequestError(say("learningPointNameTooLong", max=MAX_NAME_CHARS))
    return name


class SpaceLearningAnalysisService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._points = SpaceKnowledgePointRepository(session)
        self._notices = SpaceLearningNoticeRepository(session)

    @classmethod
    def of(cls, session: AsyncSession) -> SpaceLearningAnalysisService:
        return cls(session)

    # --- 知识点清单 --------------------------------------------------------

    async def list_points(self, space_id: int) -> list[dict]:
        """这块板的知识点清单，按显示顺序。设置页与「从清单添加」都读这一份。"""
        rows = await self._points.list_for_space(space_id)
        return [self._point_to_api(row) for row in rows]

    @staticmethod
    def _point_to_api(row: SpaceKnowledgePoint) -> dict:
        return {"id": row.id, "name": row.name, "displayOrder": row.display_order}

    async def add_point(self, *, space_id: int, name: object) -> dict:
        """往清单末尾加一条。同名（还在清单里的）拒绝 —— 与偏唯一索引同一判据，
        先在这里拦成一句人话，而不是让索引抛 IntegrityError。"""
        clean = _clean_name(name)
        if await self._points.name_taken(space_id=space_id, name=clean):
            raise BadRequestError(say("learningPointNameTaken", name=clean))
        if len(await self._points.list_for_space(space_id)) >= MAX_POINTS:
            raise BadRequestError(say("learningPointLimit", max=MAX_POINTS))
        row = await self._points.create(
            space_id=space_id,
            name=clean,
            display_order=await self._points.next_display_order(space_id),
        )
        return self._point_to_api(row)

    async def rename_point(self, *, space_id: int, point_id: int, name: object) -> dict:
        row = await self._live_point_or_404(space_id=space_id, point_id=point_id)
        clean = _clean_name(name)
        if clean != row.name and await self._points.name_taken(
            space_id=space_id, name=clean, exclude_id=point_id
        ):
            raise BadRequestError(say("learningPointNameTaken", name=clean))
        row = await self._points.update(point=row, name=clean)
        return self._point_to_api(row)

    async def remove_point(self, *, space_id: int, point_id: int) -> None:
        """软删：从清单里去掉，行和名字留着 —— 已经归在它下面的题还要显示这个名字，
        而且同一个名字以后还能再加回来（偏唯一索引只管 live 的行）。关联行不在这里
        删：``task_knowledge_point`` 是题目那一域的行，读的时候按 ``deleted_at`` 过滤，
        于是「这道题归在这个已删的知识点下」自然不再出现，却也不丢掉这个事实。"""
        row = await self._live_point_or_404(space_id=space_id, point_id=point_id)
        await self._points.soft_delete(point=row)

    async def _live_point_or_404(
        self, *, space_id: int, point_id: int
    ) -> SpaceKnowledgePoint:
        row = await self._points.get(space_id=space_id, point_id=point_id)
        if row is None:
            raise NotFoundError(
                "Knowledge point not found",
                data={"spaceId": space_id, "knowledgePointId": point_id},
            )
        return row

    async def valid_point_ids(
        self, *, space_id: int, point_ids: Sequence[int]
    ) -> list[int]:
        """``point_ids`` 中属于这块板、还 live 的那些，按传入顺序、去重。

        题目写知识点时用它：一条题只能归在自己板上的知识点下，给来一个别处的 id 就
        当它不存在（不是 400 —— 与 ``topics`` 那条「不做校验」一脉相承，但这里会
        把不属于这块板的丢掉，免得一道题挂上一块看不见的板的知识点）。
        """
        wanted = [pid for pid in dict.fromkeys(point_ids) if isinstance(pid, int)]
        if not wanted:
            return []
        found = await self._points.list_by_ids(space_id=space_id, point_ids=wanted)
        live = {row.id for row in found}
        return [pid for pid in wanted if pid in live]

    # --- 开关 --------------------------------------------------------------

    async def read_switch(self, space) -> dict:
        """这块板的开关，读三格：开没开、谁开的、什么时候开的。列上的
        ``learning_analysis_enabled`` 是权威；后两格只用于展示。"""
        return {
            "enabled": bool(getattr(space, "learning_analysis_enabled", False)),
            "enabledBy": getattr(space, "learning_analysis_enabled_by", None),
            "enabledAt": _ms(getattr(space, "learning_analysis_enabled_at", None)),
        }

    async def set_switch(self, *, space, actor_user_id: int, enabled: bool) -> dict:
        """开 / 关这块板的学情分析。开启那一刻记下是谁、什么时候；关掉保留那两格
        （「上一次是谁开的」比「没有」有用），但 ``enabled`` 才是判据。

        关掉**不**删任何东西：知识点清单、告知卡确认记录都留着。重新打开时，那些
        已经点过「我已阅读并知悉」的人不会再看到卡片（他们的记录还在）—— 这正是
        「开过一次就算告知过」的意思，而这次打开通知的是还没点过的人。
        """
        was = bool(getattr(space, "learning_analysis_enabled", False))
        space.learning_analysis_enabled = bool(enabled)
        if enabled and not was:
            space.learning_analysis_enabled_by = actor_user_id
            space.learning_analysis_enabled_at = datetime.now(UTC)
        space.updated_at = datetime.now(UTC)
        self._session.add(space)
        await self._session.flush()
        return await self.read_switch(space)

    # --- 告知卡 ------------------------------------------------------------

    async def notice_state(self, *, space, user_id: int) -> dict:
        """这个人进这块板时要不要弹告知卡，以及为什么。

        规则（唯一一条）：**开着** 且 **这个人没有确认记录** 才弹。于是本来就
        在板里的人、以及开关打开后才加入的人一视同仁 —— 都是下一次进来时看到它。

        三格永远都在：前端读 ``notice.acknowledged``（清掉卡片时也写它），少一格就会
        读出 ``undefined``（``frontend/src/types/spaces.ts`` 的
        ``SpaceLearningNoticeState``）。
        """
        enabled = bool(getattr(space, "learning_analysis_enabled", False))
        if not enabled:
            return {"enabled": False, "needsAcknowledge": False, "acknowledged": False}
        acknowledged = await self._notices.has_acknowledged(
            space_id=space.id, user_id=user_id
        )
        return {
            "enabled": True,
            "needsAcknowledge": not acknowledged,
            "acknowledged": acknowledged,
        }

    async def acknowledge(self, *, space_id: int, user_id: int) -> dict:
        """记下这次点击。只追加、幂等：再点一次不会有第二行，也不会改第一次的
        时间（``SpaceLearningNotice`` 的 docstring）。"""
        await self._notices.acknowledge(space_id=space_id, user_id=user_id)
        return {"acknowledged": True}


def _ms(moment: datetime | None) -> int | None:
    return int(moment.timestamp() * 1000) if moment is not None else None
