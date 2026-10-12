"""题目在外站做没做：平台持有的外站账号、题上挂的外站题号、每个领取者的核对结果。

学生在 YOJ（``yoj.ruc.edu.cn``）上做题，平台这边只看到「谁报名了」。这个域把两件
事接起来：一道题挂一个外站题号，同步时按题号去外站把 AC 记录拉下来，用**学号**对上
平台账号，结果写进 ``external_problem_result``。

三张表的取舍：

* ``external_problem_account`` —— 平台持有的外站账号，一个站一行（``id`` 照
  `domain/integration/models.py` 的 ``FeishuApp`` 定成固定值，于是「管理员配过没有」
  是一次 ``get``）。密码密封存，理由和 ``Integration.secret`` 一样；``username``
  不是秘密，界面原样显示。
* ``task_external_problem`` —— 题目挂的外站题号，外加「上一次同步成不成」。放一张
  表而不是给 ``task`` 加两列：外站题号是可有可无的附加信息，题目表已经很大，而且
  这两个字段总是被一起读写。
* ``external_problem_result`` —— 一个领取者一条。跟着**报名记录**（``member_id``）
  走，不是跟着用户走：名单上一行一条，小队报名的那行没有结果。

``source`` 现在只有 ``yoj`` 一个值，但它在每一行里。加一个站是加一个值，不是加一列
—— 一个已经有数据的表加列要分两次发版（``.claude/rules/migrations.md``），这个代价
现在付掉是零。
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Sequence,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.common import Timestamps

task_external_problem_seq = Sequence("task_external_problem_seq")

#: 平台持有的外站账号一行一个站，行 id 取固定值，于是「这个站配过没有」是一次
#: ``get``，也不会因为一次误插多出第二行。
EXTERNAL_PROBLEM_ACCOUNT_ROW_ID = 1

#: 现在只接这一个站。
SOURCE_YOJ = "yoj"


class ExternalProblemAccount(Timestamps, Base):
    """平台持有的外站账号。密码密封，只有平台管理员能改。"""

    __tablename__ = "external_problem_account"
    __table_args__ = (
        UniqueConstraint("source", name="uq_external_problem_account_source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    #: 登录名。YOJ 上就是老师自己的学号，不是秘密。
    username: Mapped[str] = mapped_column(String(64))
    #: ``{"password": …}``，密封（``Purpose.EXTERNAL_PROBLEM_SECRET``，绑到本行）。
    secret: Mapped[str] = mapped_column(Text)
    #: 最后保存它的平台管理员。
    updated_by: Mapped[str] = mapped_column(String(64))


class TaskExternalProblem(Timestamps, Base):
    """一道题挂在哪个外站题号上，以及上一次同步的结论。"""

    __tablename__ = "task_external_problem"
    # 名字都写全（`fk_` / `ix_` 不带表名）是照 `domain/agent_session/models.py` 的
    # ``agent_environments``：不带名字的约束在迁移里总得补一个，两处写不同的名字
    # 就是两份描述开始漂开的起点。
    __table_args__ = (
        UniqueConstraint(
            "task_id", "source", name="uq_task_external_problem_task_source"
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, task_external_problem_seq, primary_key=True
    )
    # 不指向 ``task.id``：这个域不认识题目域（跨域那条边在
    # `domain/task/external_roster.py` 上，只用来取名单）。加外键会多一条
    # ``app.domain.external_problem -> app.domain.task`` 的边，而那道守卫不许新欠。
    # 代价是没有级联：题目删掉时这一行要清，清理动作放在删题那一侧。
    # 索引也不另建：下面那条唯一约束就以它打头，「按题目查」走的就是它。
    task_id: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16))
    #: 外站上的题号，存字符串：它是外站的标识，不是平台要拿来算的数。
    problem_no: Mapped[str] = mapped_column(String(32))
    #: 挂上题号的老师。
    created_by: Mapped[str] = mapped_column(String(64))
    #: 从没同步过就是 NULL，不另给一个「还没同步」的状态值。
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: ok | failed。空串 = 还没同步过。
    last_status: Mapped[str] = mapped_column(String(16), default="", server_default="")
    #: 上一回为什么没成，原样给老师看（外站改版、账号被停、连不上都会到这里）。
    last_error: Mapped[str] = mapped_column(Text, default="", server_default="")


class ExternalProblemResult(Base):
    """一个领取者在一道题上的外站结论。

    只有一次同步的结论，没有历史：老师要看的是「现在过没过」。新一次同步整道题一起
    重写，所以这里不需要 ``updated_at`` 那种「什么时候动过」的字段，``synced_at``
    说的就是这一行是哪个时刻看到的。

    **主键就是「哪道题、名单上哪一行、哪个站」**：一次同步对每行至多写一条，天然唯一，
    也就不必再拿一个序列去当键。表本身是临时数据（下次同步整批重写），没有任何东西
    需要引用它。
    """

    __tablename__ = "external_problem_result"

    task_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    #: 报名记录 id（``task_membership.id``）。名单上一行一条。
    member_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    #: 拿来对的那个学号：同步当时这个人在平台上的用户名。留档是为了对不上时看得出
    #: 对的是什么 —— 用户名以后是可以改的。
    student_no: Mapped[str] = mapped_column(String(64))
    solved: Mapped[bool] = mapped_column(Boolean)
    #: 外站上最早那次 AC 的时间。没做出来就是 NULL。
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
