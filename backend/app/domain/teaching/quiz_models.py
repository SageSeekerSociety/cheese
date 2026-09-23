"""小测 —— 一门课里的一周，学生答一次，客观题当场判、主观题等老师复核。

**它不新造提交体系。** 作业走 ``TaskSubmission``（一份交上去的成果，可能反复改）；
小测是另一件事：一组当场要答的题、一次作答、一个分数。两者的生命周期不同，所以
是两套行 —— 但**可见性只有一套**：小测挂在教学单元上（``unit_id``），**单元发布
了，它的小测才存在**给学生。这一点是复用 ``published_at`` 那条查询的语义，不是
另立一份规则 —— 见 ``app.domain.teaching.models`` 的模块说明。

**判分分成两半，界就在题型上**：客观题（单选 / 多选 / 判断 / 填空）交卷那一刻在
服务端一次判完，学生立刻看到分；主观题（简答）判不了，它落到老师的复核队列里
（``awarded_points`` 为 NULL 就是「等着人判」），老师判完这次作答才算判完
（``QuizAttempt.graded_at`` 由 NULL 变成时间）。

**正确答案从不发给学生。** 作答在截止前可以重交（改一改再交是正常的），只要能
拿到答案键，重交就变成抄答案。所以学生的载荷里只有「你这题得了多少分」，没有
「正确选项是哪个」。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    Sequence,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

quiz_seq = Sequence("quiz_seq")
quiz_question_seq = Sequence("quiz_question_seq")
quiz_attempt_seq = Sequence("quiz_attempt_seq")
quiz_answer_seq = Sequence("quiz_answer_seq")

#: 题型。**客观题在交卷那一刻判完**，其余进老师的复核队列 —— 这张表是那条界的
#: 唯一定义处（``OBJECTIVE_KINDS``），别在别处再抄一遍。
SINGLE_CHOICE = "SINGLE_CHOICE"
MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
TRUE_FALSE = "TRUE_FALSE"
FILL_BLANK = "FILL_BLANK"
SHORT_ANSWER = "SHORT_ANSWER"

OBJECTIVE_KINDS = frozenset({SINGLE_CHOICE, MULTIPLE_CHOICE, TRUE_FALSE, FILL_BLANK})
QUESTION_KINDS = OBJECTIVE_KINDS | {SHORT_ANSWER}


def is_objective(kind: str) -> bool:
    """客观题在交卷那一刻判完，主观题进老师的复核队列。

    这条判据只此一处 —— 它决定「谁会被自动判」，别在服务与路由里各抄一遍。
    """
    return kind in OBJECTIVE_KINDS


class Quiz(Base):
    """一次小测。挂在教学单元上，数量上限是「一个单元一次」。"""

    __tablename__ = "quiz"

    id: Mapped[int] = mapped_column(
        BigInteger, quiz_seq, primary_key=True, server_default=quiz_seq.next_value()
    )
    #: The 题目板 — same board as the unit's, carried here so a quiz can be read
    #: without walking to the unit.
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: The week it belongs to. **Visibility comes from this unit's
    #: ``published_at``**, never from a column on the quiz.
    unit_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    #: NULL = 不设截止。过了截止不能再交（而截止前可以改答案重交）。
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QuizQuestion(Base):
    """一道题。``answer`` 的形状由 ``kind`` 决定：

    - 单选 / 判断：选项下标（判断是 0 / 1）
    - 多选：选项下标列表（判分是**全对才给分**，不做部分分）
    - 填空：可以接受的答案字符串列表（判分时去空白、不分大小写）

    简答没有机器答案 —— 它的 ``answer`` 是老师写给自己的参考要点，不参与判分。
    """

    __tablename__ = "quiz_question"

    id: Mapped[int] = mapped_column(
        BigInteger,
        quiz_question_seq,
        primary_key=True,
        server_default=quiz_question_seq.next_value(),
    )
    quiz_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 卷面次序。老师改次序就是改这个数，不用删了重加。
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    #: 选项文本（选择题用；非选择题是空表）。
    options: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: 见类说明。**从不发给学生。**（JSONB 里放什么由 ``kind`` 定，不是一个固定形状）
    answer: Mapped[Any] = mapped_column(JSONB, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QuizAttempt(Base):
    """一个学生在一次小测上的一份作答。

    键是 (quiz, user)：**一个人一份**。截止前重交是替换（不是追加新版本）—— 小测
    不是作业，交的是答案不是成果，留一串版本只会让「他到底交的哪一份」变成问题。
    ``graded_at`` 为 NULL = 还有主观题等着老师判。
    """

    __tablename__ = "quiz_attempt"

    id: Mapped[int] = mapped_column(
        BigInteger,
        quiz_attempt_seq,
        primary_key=True,
        server_default=quiz_attempt_seq.next_value(),
    )
    quiz_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: NULL = 主观题还等着人判；判完（或本来就只有客观题）写时间。
    graded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class QuizAnswer(Base):
    """一份作答里的一道题。

    分数**不聚合到 attempt 上存一份**：总分是这些行的和，现算。存一份就有了两个
    真相（改了分忘了更新总数），而这里要读的量是一张卷子、不是一百万行。
    """

    __tablename__ = "quiz_answer"

    id: Mapped[int] = mapped_column(
        BigInteger,
        quiz_answer_seq,
        primary_key=True,
        server_default=quiz_answer_seq.next_value(),
    )
    attempt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 学生答的。形状随题型：下标 / 下标列表 / 字符串（JSONB，不是一个固定形状）。
    response: Mapped[Any] = mapped_column(JSONB, nullable=False)
    #: NULL = 等着人判（只有主观题会这样）。客观题在交卷那一刻就写好了。
    awarded_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    graded_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    graded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
