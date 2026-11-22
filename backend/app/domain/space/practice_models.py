"""巩固练习、复习排期、推送与讲解的行（§ learning analysis, step 4）。

学情分析第 4 步的五张表，和第 2、3 步的结果表分开放：那几张是学情分析写下的「AI 怎么
看你」，这几张是成员自己做的事（做题、复习）和它们的花费刹车。

- ``SpaceLearningPractice``：一组巩固题（3 道选择题）与这个人的作答；
- ``SpaceLearningReview``：一个人在一个知识点上的复习排期（固定间隔 1/3/7/14 天）；
- ``SpaceLearningPush``：老师按下的一次「推送巩固题」，只记人数，不记名单；
- ``SpaceLearningExplanation``：「先看一段讲解」的缓存，一人一个知识点一段；
- ``SpaceLearningMemberDay``：一个人一天替自己要了几次模型调用（每人每日上限）。

**只有本人。** 题、作答、排期、讲解都按 ``user_id`` 归一个人，路由只读写发请求的人
自己的行；老师那一侧只读整体数（做过巩固题的人里几人升档、已推送几人）。
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

#: 一组巩固题从哪来：自己点的、老师推送的、到期复习时再做的一组。
PRACTICE_SOURCES = ("self", "pushed", "review")


class SpaceLearningPush(Base):
    """老师在知识点详情里按下的一次「推送巩固题给卡住的成员」。

    只记这一次推给了几个人（``recipients``）：名单不在这里，也不在任何老师读得到的
    地方 —— 每个收到的人自己那组题（``SpaceLearningPractice.push_id``）指回这一行，
    而那一行只有本人读得到。
    """

    __tablename__ = "space_learning_push"
    __table_args__ = (
        Index("ix_space_learning_push_space_id", "space_id"),
        Index("ix_space_learning_push_knowledge_point_id", "knowledge_point_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id", ondelete="CASCADE", name="fk_space_learning_push_space_id_space"
        ),
        nullable=False,
    )
    knowledge_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="CASCADE",
            name="fk_space_learning_push_knowledge_point_id",
        ),
        nullable=False,
    )
    #: 按下推送的那位老师。
    pushed_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: 这一次新排上一组题的人数（已经有一组没做完的人不再加一组）。
    recipients: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningPractice(Base):
    """一组巩固题：模型按这个人在一个知识点上卡住的地方出的 3 道选择题，和他的作答。

    一组题走三步，每一步由一列说出来，不另设状态列：``generated_at`` 为空是「排着
    队」（老师推送的，还没出题），``completed_at`` 为空是「出好了、还没做完」，两个都
    有是「做完了」。**一个人在一个知识点上至多一组没做完的**（部分唯一索引）：「换一
    组」把没做完的那组换掉，老师再推一次也不会给已经有题在手的人多排一组。

    ``questions`` 是一组题本身（``[{stem, options, answer, explanation}]``），整组读
    整组写，不按路径查，所以是一格 JSONB 而不是一张题目表。``answers`` 是每道题第一次
    选的那个选项（未答为 -1），第一次作答就定下，之后不改 —— 算的是「第一次就做对」。

    ``level_before`` / ``level_after`` 是做这组题前后这个知识点的判断档位：老师端「做
    过巩固题的人里几人升档」按它们数，不用回头翻判断的历史。
    """

    __tablename__ = "space_learning_practice"
    __table_args__ = (
        # 一个人在一块板上的全部巩固题（本人那一页），也是 space_id 外键的索引。
        Index(
            "ix_space_learning_practice_space_user",
            "space_id",
            "user_id",
            "knowledge_point_id",
        ),
        Index(
            "uq_space_learning_practice_open",
            "space_id",
            "user_id",
            "knowledge_point_id",
            unique=True,
            postgresql_where=text("completed_at IS NULL"),
        ),
        Index("ix_space_learning_practice_knowledge_point_id", "knowledge_point_id"),
        Index("ix_space_learning_practice_push_id", "push_id"),
        CheckConstraint(
            "source IN ('self', 'pushed', 'review')",
            name="ck_space_learning_practice_source",
        ),
        CheckConstraint(
            "level_before IS NULL OR level_before IN ('mastered', 'shaky', 'weak')",
            name="ck_space_learning_practice_level_before",
        ),
        CheckConstraint(
            "level_after IS NULL OR level_after IN ('mastered', 'shaky', 'weak')",
            name="ck_space_learning_practice_level_after",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_practice_space_id_space",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    knowledge_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="CASCADE",
            name="fk_space_learning_practice_knowledge_point_id",
        ),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    push_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_learning_push.id",
            ondelete="SET NULL",
            name="fk_space_learning_practice_push_id",
        ),
        nullable=True,
    )
    questions: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    answers: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
    #: 做完时答对几道。
    correct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level_before: Mapped[str | None] = mapped_column(String(16), nullable=True)
    level_after: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SpaceLearningReview(Base):
    """一个人在一个知识点上的复习排期：下一次哪天该再做一组。

    一人一个知识点一行，每做完一组改写一次。``streak`` 是连着几组全对；下一次的间隔
    按它取 1 / 3 / 7 / 14 天（``learning_practice_service.REVIEW_INTERVALS``），连对四
    组算巩固住了，``due_on`` 置空、不再提醒。日子按平台的日历
    （``learning_insight_analysis.LOCAL_ZONE``）。
    """

    __tablename__ = "space_learning_review"
    __table_args__ = (
        Index(
            "uq_space_learning_review_space_user_point",
            "space_id",
            "user_id",
            "knowledge_point_id",
            unique=True,
        ),
        Index("ix_space_learning_review_knowledge_point_id", "knowledge_point_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_review_space_id_space",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    knowledge_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="CASCADE",
            name="fk_space_learning_review_knowledge_point_id",
        ),
        nullable=False,
    )
    streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    #: 下一次该复习的那一天；巩固住了（连对四组）为空。
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_practiced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningExplanation(Base):
    """「先看一段讲解」：模型针对一个知识点和这个人的误解写的一段话，缓存着。

    一人一个知识点一段。``basis_at`` 是写它时那条判断的 ``updated_at``：判断变了（学情
    分析改写、做完一组巩固题改了档位）才重写，没变就直接读这一段，不再调模型。
    """

    __tablename__ = "space_learning_explanation"
    __table_args__ = (
        Index(
            "uq_space_learning_explanation_space_user_point",
            "space_id",
            "user_id",
            "knowledge_point_id",
            unique=True,
        ),
        Index("ix_space_learning_explanation_knowledge_point_id", "knowledge_point_id"),
        CheckConstraint(
            "length(btrim(body)) > 0", name="ck_space_learning_explanation_body"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_explanation_space_id_space",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    knowledge_point_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space_knowledge_point.id",
            ondelete="CASCADE",
            name="fk_space_learning_explanation_knowledge_point_id",
        ),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    basis_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SpaceLearningMemberDay(Base):
    """一个人一天在一块板上替自己要了几次模型调用（出巩固题、写讲解）。

    每人每天的上限按它算，调用之前先记一次（失败的也算），同第 2 步的
    ``SpaceLearningRun.calls``。老师推送的那几组不记在这里（不是这个人要的），但和这几
    次一样记进整块板当天的 ``SpaceLearningRun.calls``：整块板的刹车只有一个。
    """

    __tablename__ = "space_learning_member_day"
    __table_args__ = (
        Index(
            "uq_space_learning_member_day_space_user_day",
            "space_id",
            "user_id",
            "day",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    space_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "space.id",
            ondelete="CASCADE",
            name="fk_space_learning_member_day_space_id_space",
        ),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
