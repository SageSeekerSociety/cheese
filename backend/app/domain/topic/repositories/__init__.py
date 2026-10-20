"""话题的 storage。

**这个包仍是话题存储的唯一入口。** 它在 2026-10-15 之前是一个 1193 行的单文件
`repositories.py`；方法体搬进了同级子模块，这个包的导入路径、公开表面和 patch 点
一个字没变 —— 每个从 `app.domain.topic.repositories` 进来的名字还是同一个对象：

- `repository` —— `TopicRepository` 本身，方法全是一句转发；
- `reads` —— 整行、投影（`ListedRoom`）和计数；
- `writes` —— 建、锁、私聊房间的取得；
- `replied` —— 侧栏那盏黄灯（有人回复了我、我还没处理）；
- `unread` —— 角标未读、通知级别、已读游标；
- `statements` —— 共用的裸表与 SQL 判据（`readable_by`、`_mentions`、
  `_last_activity`、`seen_by`…），几个读点都从这里取，才不会有第二份定义；
- `vocabulary` —— 路由、前端和这里共用的封闭词表（`TopicSortField`、`SortOrder`）；
- `progress` —— `TopicProgressRepository`，两个方法一张表，不转发。

拆的规矩是「一个决定只写一遍」：每一条判据、每一次读、每一次写都只有一处实现，
类只转发。转发的方法不拿决定 —— 那正是两份实现不会漂移的原因。

2026-10-16，`topic -> block` 那几条向上的边收掉时，`author_type`
那一档不再逐字抄成字符串：它 import 第 1 层的 `app.domain.event_author`，因为判这一
列的守卫要求按枚举成员读，而抄下来的字符串正是它要防的东西。

`_replied_to_me_statement` 是这里唯一一个下划线名字，因为它原来就在这个模块里，
外面按老路径取它：`.claude/scripts/explain_channel_detail.py` 拿它出来对着几条
候选写法比执行计划。搬去 `replied.py` 之后原处不再有它，所以照样再导出，让那条
老路径连名字一起保住。

拆成**包**而不是几个同级模块，是为了不欠新债：同级的 `reads` 与 `unread` 互相
import 会在 C3（`sibling domains are acyclic`）上多出环，而包里的子模块 import 的
是父包 —— 那条「子指向祖先」的边契约本来就不算环。形状和
`app/domain/feedback/services/`（PR #3856）、`app/domain/review/services/` 一样。
"""

from __future__ import annotations

from app.domain.topic.repositories.progress import (
    TopicProgressRepository as TopicProgressRepository,
)
from app.domain.topic.repositories.replied import (
    _replied_to_me_statement as _replied_to_me_statement,
)
from app.domain.topic.repositories.repository import (
    TopicRepository as TopicRepository,
)
from app.domain.topic.repositories.statements import readable_by as readable_by
from app.domain.topic.repositories.statements import seen_by as seen_by
from app.domain.topic.repositories.vocabulary import ListedRoom as ListedRoom
from app.domain.topic.repositories.vocabulary import SortOrder as SortOrder
from app.domain.topic.repositories.vocabulary import (
    TopicSortField as TopicSortField,
)
from app.domain.topic.repositories.vocabulary import Unread as Unread
