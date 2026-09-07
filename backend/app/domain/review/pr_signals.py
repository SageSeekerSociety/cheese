"""PR 上发生的事，怎么回流给写这段代码的芝士。

一个开着的 PR 可以同时踩到好几件要人（或芝士）动手的事：CI 挂了、有人留了评审
意见、和主分支冲突了。它们互不蕴含 —— 一次红的 CI 不代表没人评审过，一条评审
意见也不会因为 CI 同时红了就变得不重要。所以这里把每一件独立排成一条待发
(`PendingNudge`)，由调用方一次性发完，**不允许其中一件先发生就把其余的挡掉**。

三件事各自的去重是**按内容**的，不是按「已经叫过一次」：

- 同一批失败连续轮询多次 → 签名不变 → 只发一次；
- 失败内容变了（多挂一个 job、换了一个 job、换了一个 commit）→ 签名变了 → 再发
  一次，因为那是芝士没见过的新事实。

签名存在卡片上（`AcceptCard.nudge_state`），不是进程内存里 —— 后端重启一次就把
所有在飞的 PR 重新叫一遍，是这条链路最容易犯又最难看出来的错。
"""

import enum
import hashlib
import re
from dataclasses import dataclass, field
from typing import Final

from app.domain.review.notes import NoteCode

#: 评审意见最多自动回流几轮。
#:
#: CI 失败和合并冲突是客观的：修好了它们就消失，所以重复叫多少轮都不会白叫，也
#: 就不设上限。评审意见不是 —— 一条「这个设计我不同意」芝士可能来回读三遍也读不
#: 出该改什么，第四轮自动回流比第三轮更可能只是空转。到顶之后卡面写清「来回过
#: 几轮了」，把它交给人，这正是上限存在的意义：**停下来叫人，而不是安静地继续**。
REVIEW_NUDGE_LIMIT: Final = 3

#: ANSI 转义序列：CSI（`ESC [ … 终结符`）、OSC（`ESC ] … BEL/ST`）、以及双字符
#: 的 Fe 序列。CI 日志里满地都是（颜色、光标移动、清屏），而它们最终会被贴进芝士
#: 的终端。
_ANSI = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[@-Z\\-_])"
)
#: 允许活下来的控制字符：只有换行和制表符。回车单独处理（`\r\n` → `\n`），因为
#: 一个裸 `\r` 在终端里会把上一行重新盖写一遍。
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sanitize_external(text: str) -> str:
    """把一段**仓库外的人能控制**的文本，洗成可以安全贴进芝士上下文的样子。

    PR 标题、分支名、评论正文、CI 日志，全都是 provider 可控的字符串：GitHub 不
    保证它们里面没有 ANSI 转义或别的控制字符，而这些文本最终会被粘进一个跑在
    tmux 里的终端。一段 `ESC [ 2 J` 能清掉整屏、`ESC ] 0 ; … BEL` 能改标题栏，
    更糟的是它们在任何一份日志、任何一次人工复核里都是**隐形**的。

    洗的是「载体」不是「内容」：换行和制表符留着（多行日志靠它们才读得懂），
    其余 C0/C1 控制字符和转义序列一律删掉。文字本身一个字不改 —— 提示注入是内容
    层的问题，这里不假装能解决它。
    """
    if not text:
        return ""
    cleaned = _ANSI.sub("", text)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL.sub("", cleaned)


class NudgeKind(enum.StrEnum):
    """一条待发属于哪一类。**同类之间才互相去重**，跨类永远不互相压制。"""

    #: PR 的检查没通过。
    ci = "ci"
    #: 有人在 PR 上留了评审意见 / 要求改动。
    review = "review"
    #: PR 和它的 base 分支冲突了，GitHub 合不了。
    conflict = "conflict"
    #: PR 满足合并规则了（CLEAN）—— 发给验收人的那条，同一个 head 只说一次。
    #: （「采纳被作废」不占一类：批准被清掉这件事一个 head 只会发生一次，
    #: 事件天然不重复。）
    ready = "ready"


@dataclass(frozen=True)
class ReviewSignal:
    """PR 上一条需要芝士看的评审动静。

    `id` 是 GitHub 给的那个 id（review 的、或 review comment 的），全局唯一且不
    随内容变化 —— 去重签名就是这些 id 的集合，所以「又来了一条新意见」必然换签
    名，「同一批意见被轮询读到十次」必然不换。
    """

    id: str
    #: `changes_requested` / `commented` / `comment`（行内评论）。
    kind: str
    author: str
    body: str
    #: 行内评论指到哪个文件（没有就空）。
    where: str = ""

    def line(self) -> str:
        """写进消息里的一行。作者名和正文都当外部文本洗过。"""
        who = sanitize_external(self.author) or "某人"
        head = f"{who}（{_REVIEW_KIND_WORDS.get(self.kind, self.kind)}）"
        if self.where:
            head += f" 在 {sanitize_external(self.where)}"
        body = sanitize_external(self.body).strip()
        return f"- {head}：{body}" if body else f"- {head}"


_REVIEW_KIND_WORDS: Final = {
    "changes_requested": "要求改动",
    "commented": "评审意见",
    "comment": "行内评论",
}


@dataclass
class PendingNudge:
    """排好队、还没发出去的一条回流。

    调用方把它们**全部**收集完再统一发。中间某一步读 GitHub 失败，不能把已经排
    好队的其它待发一起丢掉 —— 那正是「一件事把另一件事吞掉」的另一种写法。
    """

    kind: NudgeKind
    #: 这条待发的内容签名。和卡上记着的一样就不发。
    signature: str
    #: 房间里看到的那一行。
    event: str
    #: 送到芝士面前的提示词。
    content: str
    #: 折叠区的原文，和它的标题。
    detail: str = ""
    detail_label: str = ""
    #: 卡面那一行（空 = 不动卡面）。
    note: str = ""
    #: 卡面那一行对应的状态码。
    note_code: NoteCode | None = None
    #: 平台提示的类别码。
    event_type: str = ""
    #: 这一类到顶了没有；到顶的那条只写卡面、不叫芝士。
    capped: bool = False


#: 一轮里同时排了好几条待发时，卡面只留优先级最高的那一句 —— 卡面是**一行**，
#: 而三件事都要人动手。消息不受这个排序影响：每一条都会送到，这正是「互不压制」
#: 的意思，被排掉的只是「卡上显示哪一句」。
NOTE_PRIORITY: Final[dict[NudgeKind, int]] = {
    NudgeKind.ci: 3,
    NudgeKind.conflict: 2,
    NudgeKind.review: 1,
}


@dataclass
class NudgeLedger:
    """卡上那本「哪一类、发到哪个签名、发过几轮」的账。

    存成 JSON 而不是几个列，因为它整本是一起读、一起写的，而且类别还会加。
    `seen` 是去重键，`attempts` 只有带上限的类别（评审意见）用得上。
    """

    seen: dict[str, str] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, raw: object) -> "NudgeLedger":
        """从卡上读回来。任何读不懂的形状都退化成空账 —— 一本坏账最多让芝士被多
        叫一次，而为它抛异常会让整轮轮询停掉。"""
        if not isinstance(raw, dict):
            return cls()
        seen = raw.get("seen")
        attempts = raw.get("attempts")
        return cls(
            seen=(
                {str(k): str(v) for k, v in seen.items() if isinstance(v, str)}
                if isinstance(seen, dict)
                else {}
            ),
            attempts=(
                {
                    str(k): v
                    for k, v in attempts.items()
                    if isinstance(v, int) and not isinstance(v, bool)
                }
                if isinstance(attempts, dict)
                else {}
            ),
        )

    def dump(self) -> dict:
        """写回卡上。**返回新对象**：SQLAlchemy 看不见 JSON 列的原地修改，就地
        改一个 dict 等于改完不落库。"""
        return {"seen": dict(self.seen), "attempts": dict(self.attempts)}

    def already_sent(self, kind: NudgeKind, signature: str) -> bool:
        return self.seen.get(kind.value) == signature

    def rounds(self, kind: NudgeKind) -> int:
        return self.attempts.get(kind.value, 0)

    def record(self, kind: NudgeKind, signature: str) -> None:
        self.seen[kind.value] = signature
        self.attempts[kind.value] = self.rounds(kind) + 1


def signature(*parts: str) -> str:
    """一串事实的内容签名。

    存的是哈希不是原文：原文可以是几千字的 CI 日志，而这一列每张卡都要带着。
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8", "replace"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:32]
