"""教学单元 —— 一门课的时间线。

课程 = 一串教学单元。每个单元是一个星期（或一次课）里学生要拿到的东西：知识点、
课件、要交的作业、截止时间。单元挂在题目板上（题目板就是一门课，见
``app.domain.shell.catalog.is_course_shell``）。

**「发布了才有」是这个模型存在的理由。** ``published_at`` 为 NULL 的单元对学生
不存在 —— 它不进列表、不进详情、也不该进 agent 的上下文。这样「随课程推进才能
得到更多知识」是默认行为，不靠老师每周记得去改一份配置；老师想提前放出来，就发布
它。

作业不新造提交体系：``assignment_task_id`` 指向这个题目板里的一道 ``Task``，提交、
截止、重交、评审与打分全部沿用现成的那一套。
"""

from datetime import datetime

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

teaching_unit_seq = Sequence("teaching_unit_seq")


class TeachingUnit(Base):
    __tablename__ = "teaching_unit"

    id: Mapped[int] = mapped_column(
        BigInteger,
        teaching_unit_seq,
        primary_key=True,
        server_default=teaching_unit_seq.next_value(),
    )
    #: The 题目板 this unit belongs to — and therefore the course.
    space_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: Which week of the course. Two units may share a week (a lecture and a lab).
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: `Knowledge` rows this week teaches. Ids, not copies: the knowledge base is
    #: where the text lives and where it is edited.
    knowledge_point_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    #: `Material` rows handed out this week (课件).
    material_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: The assignment, carried by a real `Task` of this board — see the module
    #: docstring. NULL for a lecture-only unit.
    assignment_task_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    #: NULL = not published, i.e. it does not exist for a student yet.
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
