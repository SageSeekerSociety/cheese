"""房间里那份文件上的「保存到项目」(#1085 结论四)。

房间里的文件不是项目产物：用户传一份文件进来让 芝士 改，改完在那个房间里拿走，事
情就结束了 —— 不需要历史、不需要分支、也不需要在项目首页占一行。所以升级是**一个
动作，不是一套机制**：按了才算，不猜、不自动升。

按下去之后发生三件事，缺一件这份东西就是半个：

1. **文件进项目那棵树**。它就是源 —— 一份 .docx 没有别的东西能重建它，二进制从这
   一个口进 git（结论五）。
2. **清单上多一项或多一版**。名字默认就是这份文件的名字；点名清单上已有的一项，
   它就是那一项的新一版。
3. **记一次交付**。版本是数出来的（采纳了的卡有几张就是第几版），所以这次保存也必
   须留下一张卡 —— 另造一条「没有卡的版本」就等于多一条会和清单悄悄对不上的路。
   这张卡一落地就是 accepted：按按钮的人就是做这个决定的人，和采纳是同一类动作。
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_ARTIFACT_DECLARED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.project import artifacts
from app.domain.project.models import ProjectArtifact
from app.domain.review.models import AcceptStatus, DeliverableKind
from app.domain.review.repositories import AcceptCardRepository
from app.domain.workspace import identity as identity_mod
from app.domain.workspace import service as ws


@dataclass(frozen=True, slots=True)
class Saved:
    artifact: ProjectArtifact
    version: int
    #: 它在项目那棵树里的名字。
    path: str
    #: 内容和主干上那一份一模一样时为空 —— 这次保存没有落下新的提交。
    commit: str | None


async def save_to_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    by: str,
    artifact_id: str | None = None,
    name: str | None = None,
) -> Saved:
    """把房间里的一份文件存成项目的产物。

    `artifact_id` 点名清单上已有的一项（这份文件是它的新一版）；不给就按名字新建一
    项，名字默认是文件名。两者都是人的判断，所以这里不猜。
    """
    leaf = PurePosixPath(path).name
    if not leaf:
        raise ValidationError("这不是房间里的一份文件")
    data = await asyncio.to_thread(ws.read_room_file, project_id, room_id, path)
    named = (artifact_id or "").strip()
    declared = (
        await artifacts.reuse(session, project_id=project_id, artifact_id=named)
        if named
        else await artifacts.claim(
            session, project_id=project_id, name=(name or "").strip() or leaf
        )
    )
    landed = await asyncio.to_thread(
        ws.commit_file_on_base,
        project_id,
        path=leaf,
        data=data,
        message=f"chore: add {leaf}",
        # 作者是按下保存的那个人：这份东西是他决定进项目的。
        author=identity_mod.GitIdentity(by, f"{by}@zhishi.local"),
    )
    card = await AcceptCardRepository(session).add(
        topic_id=room_id,
        reviewer_handle=by,
        status=AcceptStatus.accepted,
        change_subject=f"chore: add {leaf}",
        artifact_id=declared.id,
        deliverable_kind=DeliverableKind.file,
        deliverable_name=leaf,
    )
    card.decided_by = by
    card.decided_at = datetime.now(UTC)
    await session.flush()
    await asyncio.to_thread(ws.write_artifact_snapshot, project_id, card.id, leaf, data)
    summary = await artifacts.summary(session, declared.id)
    version = summary.version if summary else 1
    await announce(
        session,
        place_id=room_id,
        content=f"《{declared.name}》已保存到项目，是它的第 {version} 版",
        meta=notice(
            EVENT_ARTIFACT_DECLARED,
            severity=SEVERITY_INFO,
            who=WHO_HUMAN,
            detail=f"{by} 把这个房间里的 {leaf} 存成了项目的产物",
            detail_label="保存到项目",
        ),
    )
    return Saved(
        artifact=declared,
        version=version,
        path=leaf,
        commit=landed.get("sha") if landed.get("committed") else None,
    )
