"""`topics` 路由手上那些**行的形状** —— 从 ORM 行里只读得到的几位。

路由不该按名字 import 对方领域的 `models`（C2，`.importlinter`），而它手上的行都是
ORM 行：属性是 SQLAlchemy `Mapped` 描述符。所以这里按模型的原样声明 `Mapped[...]`，
pyright 才拿得它比得过调用方那一行；读一位仍旧得到它裹着的值。契约本身在
`room_task.presentation`（`TopicRow` / `CardRow`），这里只把路由这一层多读的几位
接上去。

摆成独立模块是为了让 `topics.py` 守在文件大小上限之内 —— 协议是类型，不是行为，
和路由的那个大文件分开放正是它们该在的地方。位置从 `app/api/routes/` 挪到
`app/api/`（2026-10-12）：C4 那条契约按目录认路由，摆在 `routes/` 下就是个路由模块，
于是另一个帮手（`app/api/topics_signals.py`）import 它就成了「路由 import 路由」，
一条链地红了；这里没有路由，只有两个协议，按 C4 自己的说法该和别的共用帮手一起住在
`app.api` 下。
"""

import uuid
from datetime import datetime
from typing import Protocol

from sqlalchemy.orm import Mapped

from app.domain.room_task import presentation


class TopicRow(presentation.TopicRow, Protocol):
    """这一层从一个房间行上读到的形状。

    结构协议，不是 ORM 类：它手上的每一行都来自同域的 `TopicService`（ORM 行）。成员
    按模型的原样声明 `Mapped[...]`（`presentation.TopicRow`，那份是**行**的契约），
    pyright 才拿得它比得过那一行；`TopicOut.model_validate` 只读属性、不认类，所以这
    几位就是这里需要的全部。

    和 `presentation.TopicSignals` 分得开：那一份是 `topic` 窄读出口交出来的**纯值**
    （`topic.repositories.ListedRoom`），属性是普通标量，接不住 ORM 行。
    """


class CardRow(presentation.CardRow, Protocol):
    """这一层从一张验收卡上读到的形状 —— ORM 行，按 `AcceptCard` 的原样声明 `Mapped`。

    行来自 `AcceptService`。除了卡面要的五个信号（`presentation.CardRow`）之外，这
    里还读它挂在哪、骑哪个 PR、什么时候建的。和 `presentation.CardRow` 一样，成员是
    `Mapped[...]` 而不是普通标量 —— pyright 比的是类成员，ORM 行的属性是 `Mapped`
    描述符，读出来才解包成值。
    """

    id: Mapped[uuid.UUID]

    task_id: Mapped[uuid.UUID | None]

    topic_id: Mapped[uuid.UUID]

    decided_at: Mapped[datetime | None]

    note: Mapped[str]

    gate_passed_at: Mapped[datetime | None]

    gate_output: Mapped[str]

    pr_number: Mapped[int | None]

    pr_url: Mapped[str | None]

    created_at: Mapped[datetime]
