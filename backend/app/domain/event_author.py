"""一条事件的作者：参与者，还是平台自己。

`AuthorType` 曾经分三档 —— human / ai / system —— 而前两档是同一件事：有人说了
一句话。人和 agent 是同一种参与者（结论 1），区别只在**是哪一个**参与者，而那是
署名（`Block.author`，一条 handle）的事：一个房间坐得下好几个人和好几个 agent，
一个三档枚举说不出是哪一个，只说得出最粗的那一层，于是每个读它的地方都在用一个
答不了自己问题的字段。

所以这里只答一个问题：**这条事件是参与者写的，还是平台自己写的。**「是不是芝士
说的」去问署名（`app.domain.identity.handles`）。

## 为什么住在第 1 层

最下面的那些读点在**第 2 层**：`topic` 的未读数和「回复了我」都按这一列过滤，用的
还是自己声明的那张裸表（`topic/repositories/statements.py`）。这一档原来住在
`block/models.py`（第 4 层），第 2 层要判一次就得向上够两层的枚举，而 C5 要的正是
这个方向的边归零。

层级之外还有一条：读这一列的判据要求按**枚举成员**读
（`tests/unit/test_author_type_two_values.py`），够不到枚举的读点只能把成员写成一个
字符串字面量 —— 那正是那条守卫在防的东西。词汇住在每一层下面，判据才写得成它要求
的样子。

住在第 1 层就要谁都不 import：这个模块只 import `enum`。拿得到第 4 层的读者要用
现成的判据，`app.domain.block.authorship` 有一对孪生（`is_participant` 与
`participant_blocks`）。
"""

import enum


class AuthorType(enum.StrEnum):
    """谁写下了这条事件：一个参与者，还是平台自己。

    人和 agent 是同一种参与者（结论 1），所以这一列不回答「是人还是 AI」——
    那个问题由署名（``Block.author``，一个 handle）回答，而且只有它回答得了：
    一个房间里坐着好几个人和好几个 agent，一个档位说不出是哪一个。
    """

    participant = "participant"

    # 平台自己产的事件：部署提醒、闸门结论、自动重发。
    platform = "platform"
