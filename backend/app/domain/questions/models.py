from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class Question(Base):
    __tablename__ = "question"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[int] = mapped_column(Integer, nullable=False)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bounty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_answer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuestionFollowerRelation(Base):
    __tablename__ = "question_follower_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    follower_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuestionTopicRelation(Base):
    __tablename__ = "question_topic_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuestionQueryLog(Base):
    __tablename__ = "question_query_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    viewer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ip: Mapped[str] = mapped_column(String, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionSearchLog(Base):
    __tablename__ = "question_search_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keywords: Mapped[str] = mapped_column(String, nullable=False)
    first_question_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_size: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[str] = mapped_column(String, nullable=False)
    duration: Mapped[float] = mapped_column(Integer, nullable=False)
    searcher_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ip: Mapped[str] = mapped_column(String, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VoteType(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class Attitude(Base):
    __tablename__ = "attitude"
    __table_args__ = (
        UniqueConstraint(
            "attitudable_id",
            "user_id",
            "attitudable_type",
            name="attitude_attitudable_id_user_id_attitudable_type_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attitudable_type: Mapped[str] = mapped_column(
        PgEnum("COMMENT", "QUESTION", "ANSWER", name="AttitudableType", create_type=False),
        nullable=False,
    )
    attitudable_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attitude: Mapped[str] = mapped_column(
        PgEnum("POSITIVE", "NEGATIVE", name="AttitudeTypeNotUndefined", create_type=False),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QuestionInvitation(Base):
    __tablename__ = "question_invitation_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
