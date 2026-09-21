"""房间里那份文件上的「保存到资料库」(#1085 结论四)。

房间里的文件不是项目产物：用户传一份文件进来让 芝士 改，改完在那个房间里拿走，事
情就结束了。但有时候他想把它留下 —— 不是要交出去，是**以后还要用**：另一个房间里
提到它、下一轮拿它当素材。留下来是**一个动作**，按了才算，不猜、不自动升。

留在哪里由那句话决定：留着要用的东西是**资料**，所以它进资料库，按原名寻址、撞名
加 `(2)`、所有房间读得到，和用户自己上传的那些并排。

**它不上产物清单，也不进 git**，两件事都不是省略：

- 清单上的一项要「会交给项目外的人」（结论三）。一份留着以后用的文件不满足它，为
  了让它上榜就得凭一次按钮伪造一条交付记录 —— 那一刻谁也没把它交给任何人。
- 成品不进库（结论五）。房间里摆出来的东西多半是构建产物，把它提交进用户的主干，
  既违反那一条，也违反「不属于用户代码库的东西，不写进用户的仓库」。

真正「文件本身就是源」的那条路走正常交付：芝士 在任务分支上改、递卡、人采纳合并，
二进制从那个口进 git，不从这个按钮进。
"""

import asyncio
import uuid
from pathlib import PurePosixPath

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_LIBRARY_SAVED,
    SEVERITY_INFO,
    WHO_HUMAN,
    notice,
)
from app.domain.library import service as library


async def save_to_library(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    path: str,
    by: str,
) -> str:
    """把房间里的一份文件留进资料库，返回它在那里的名字。

    撞名不覆盖，跟着资料库自己的规矩走：两次保存就是两份，各自留着 —— 谁也说不准
    第二份是第一份的新版，还是另一样同名的东西。
    """
    leaf = PurePosixPath(path).name
    if not leaf:
        raise ValidationError("这不是房间里的一份文件")
    data = await asyncio.to_thread(library.read_room_file, project_id, room_id, path)
    name = await asyncio.to_thread(library.write_library_file, project_id, leaf, data)
    await announce(
        session,
        place_id=room_id,
        content=f"{name} 已存进资料库",
        meta=notice(
            EVENT_LIBRARY_SAVED,
            severity=SEVERITY_INFO,
            who=WHO_HUMAN,
            detail=f"{by} 把这个房间里的 {leaf} 留进了资料库，每个房间都引用得到",
            detail_label="保存到资料库",
        ),
    )
    return name
