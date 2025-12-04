from __future__ import annotations

from datetime import datetime

from enum import Enum

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Question(Base):
    __tablename__ = "question"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[int] = mapped_column(Integer, nullable=False)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bounty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_answer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("answer.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class QuestionFollowerRelation(Base):
    __tablename__ = "question_follower_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("question.id"), nullable=False)
    follower_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class QuestionTopicRelation(Base):
    __tablename__ = "question_topic_relation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("question.id"), nullable=False)
    topic_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)


class QuestionQueryLog(Base):
    __tablename__ = "question_query_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    viewer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("question.id"), nullable=False)
    ip: Mapped[str] = mapped_column(String, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(nullable=False)


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
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class VoteType(str, Enum):
    UPVOTE = "UPVOTE"
    DOWNVOTE = "DOWNVOTE"


class QuestionVote(Base):
    __tablename__ = "question_vote"
    __table_args__ = (
        UniqueConstraint("question_id", "user_id", name="uq_question_vote"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question_id: Mapped[int] = mapped_column(Integer, ForeignKey("question.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    vote_type: Mapped[str] = mapped_column(String(length=16), nullable=False)

    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)
