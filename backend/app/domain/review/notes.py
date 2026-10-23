"""验收卡 note 的状态码，和唯一的写入口。

一张卡的 `note` 过去一个字段干两件事：给人看的一句话，和给代码看的状态。代码那
一半只能靠**前缀**认出来，而前缀是以 emoji 开头的字符串——于是「这条 note 意味着
什么」散落在 13 处 `startswith`、一处 `count("⟲")`，和浏览器里一份硬编码的 emoji
数组里。三者都会跟文案一起过期：改一句话就可能改掉一次判断，而没有任何东西会红。

现在状态住在 `AcceptCard.note_code` 里，`note` 只剩「给人看的一句话」。判断读码，
渲染读码算出来的级别，文案怎么写都不影响任何一方。和 `platform_failures.py` 的
code + copy contract 同一条规矩：文案在外，判断在内，中间传的是码不是字符。

**写 note 一律走 `record` / `annotate` / `clear`**，别直接赋值 `card.note`——那正是
让文案和状态分家的那个动作。
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from sqlalchemy.orm import Mapped

from app.domain.review_note_codes import (  # noqa: F401
    NOTE_MAX,
    NoteCode,
    NoteLevel,
    note_level,
)


class NotedCard(Protocol):
    """写入口真正会碰的那两列，按它们在模型上的样子写。

    这里不具名 `AcceptCard`：`review.models` 反过来要这一片的 `NoteCode`，两边互相
    import 就是一个环（`.importlinter` 的 C3）。写入口要的从来只是「一张有 note 和
    note_code 的卡」，那就只声明这两列——`AcceptCard` 天然满足，环也就没了。

    声明成 `Mapped[...]` 而不是 `str` / `NoteCode | None`：那两列在模型上就是
    `Mapped[...]`，而变量类型的协议成员是不变的，写成 `str` 时 pyright 会拒掉每一个
    「拿 AcceptCard 来调」的地方（`Mapped[str]` 不是 `str`）。按原样写，读写都照旧
    走 SQLAlchemy 的 `__get__` / `__set__`。

    `Mapped` 只在类型期存在，所以两处都带引号：这是类体里的真注解，不加引号会在
    导入时求值，而 `TYPE_CHECKING` 的 import 在运行时不在了。
    """

    note: "Mapped[str]"
    note_code: "Mapped[NoteCode | None]"


def record(card: NotedCard, code: NoteCode | None, text: str) -> None:
    """卡进入一个新状态：文案和码一起落。"""
    card.note = text[:NOTE_MAX]
    card.note_code = code


def annotate(card: NotedCard, text: str) -> None:
    """在现有 note 前面加一句，**不动码**。

    给的是补充说明（这次采纳为什么没走 PR、平台自己就是 forge），不是新状态。卡还
    停在原来那件事上，所以判断该看到的仍是原来那个码——这正是过去做不到的事：加一
    句前缀就把所有 `startswith` 挡在了外面。
    """
    old = (card.note or "").strip()
    card.note = (f"{text}；{old}" if old else text)[:NOTE_MAX]


def clear(card: NotedCard) -> None:
    """这张卡上没有要说的了。"""
    card.note = ""
    card.note_code = None
