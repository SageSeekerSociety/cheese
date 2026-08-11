"""Initial schema

Revision ID: a95752502bb0
Revises:
Create Date: 2026-03-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a95752502bb0"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Enum types — used as column types; created automatically on first table use
# ---------------------------------------------------------------------------
attitudable_type_enum = postgresql.ENUM(
    "COMMENT", "QUESTION", "ANSWER", name="AttitudableType", create_type=True
)
attitude_type_enum = postgresql.ENUM(
    "POSITIVE", "NEGATIVE", name="AttitudeTypeNotUndefined", create_type=True
)
avatar_type_enum = postgresql.ENUM(
    "default", "predefined", "upload", name="AvatarType", create_type=True
)
comment_commentable_type_enum = postgresql.ENUM(
    "ANSWER", "COMMENT", "QUESTION", name="CommentCommentabletypeEnum", create_type=True
)


def upgrade() -> None:
    # -- Sequences --
    op.execute(
        sa.schema.CreateSequence(
            sa.Sequence("ai_conversation_seq", start=1, increment=50)
        )
    )
    op.execute(
        sa.schema.CreateSequence(sa.Sequence("ai_message_seq", start=1, increment=50))
    )
    op.execute(sa.schema.CreateSequence(sa.Sequence("discussion_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("discussion_reaction_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("knowledge_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("knowledge_label_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("notification_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("project_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("project_membership_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("reaction_type_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("space_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("space_categories_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("space_user_rank_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("space_admin_relation_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_membership_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_topics_relation_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_submission_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_submission_entry_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_submission_review_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("task_ai_advice_context_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("team_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("team_user_relation_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("team_membership_application_seq")))
    op.execute(
        sa.schema.CreateSequence(
            sa.Sequence("user_ai_quota_seq", start=1, increment=50)
        )
    )
    op.execute(sa.schema.CreateSequence(sa.Sequence("user_id_seq")))
    op.execute(
        sa.schema.CreateSequence(sa.Sequence("user_following_relationship_id_seq"))
    )
    # task_submission_schema_seq is not in metadata (composite PK table)

    # -----------------------------------------------------------------------
    # Independent tables (no FK dependencies)
    # -----------------------------------------------------------------------
    op.create_table(
        "ai_conversation",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("ai_conversation_seq", start=1, increment=50),
            nullable=False,
        ),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=True),
        sa.Column("conversation_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("model_type", sa.Text(), nullable=False),
        sa.Column("module_type", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id"),
    )
    op.create_index("ix_ai_conversation_owner_id", "ai_conversation", ["owner_id"])

    op.create_table(
        "answer",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "answer_favorited_by_user",
        sa.Column("answer_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("answer_id", "user_id"),
    )

    op.create_table(
        "answer_vote",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("answer_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("vote_type", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("answer_id", "user_id", name="uq_answer_vote"),
    )

    op.create_table(
        "attachment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "attitude",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("attitudable_type", attitudable_type_enum, nullable=False),
        sa.Column("attitudable_id", sa.Integer(), nullable=False),
        sa.Column("attitude", attitude_type_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attitudable_id",
            "user_id",
            "attitudable_type",
            name="attitude_attitudable_id_user_id_attitudable_type_key",
        ),
    )

    op.create_table(
        "avatar",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("avatar_type", avatar_type_enum, nullable=False),
        sa.Column("usage_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "comment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("commentable_type", comment_commentable_type_enum, nullable=False),
        sa.Column("commentable_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "discussion",
        sa.Column("id", sa.Integer(), sa.Sequence("discussion_seq"), nullable=False),
        sa.Column("model_type", sa.String(255), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "discussion_mentioned_users",
        sa.Column("discussion_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("discussion_id", "user_id"),
    )

    op.create_table(
        "discussion_reaction",
        sa.Column(
            "id", sa.Integer(), sa.Sequence("discussion_reaction_seq"), nullable=False
        ),
        sa.Column("discussion_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("reaction_type_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "group",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "group_membership",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("member_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "group_profile",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("intro", sa.String(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "group_question_relationship",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "group_target",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("intro", sa.String(), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=False),
        sa.Column("ended_at", sa.Date(), nullable=False),
        sa.Column("attendance_frequency", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "knowledge",
        sa.Column("id", sa.BigInteger(), sa.Sequence("knowledge_seq"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("type", sa.String(255), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("material_id", sa.Integer(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(255), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("discussion_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "knowledge_label",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("knowledge_label_seq"), nullable=False
        ),
        sa.Column("knowledge_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "knowledge_upvote",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("knowledge_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_id", "user_id", name="uq_knowledge_upvote"),
    )

    op.create_table(
        "llm_call_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_seu", sa.Float(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("request_type", sa.String(32), nullable=False),
        sa.Column("context_type", sa.String(32), nullable=True),
        sa.Column("context_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_call_log_user_id", "llm_call_log", ["user_id"])

    op.create_table(
        "material",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("uploader_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires", sa.Integer(), nullable=True),
        sa.Column("download_count", sa.Integer(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "material_bundle",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rating", sa.Float(), nullable=False),
        sa.Column("rating_count", sa.Integer(), nullable=False),
        sa.Column("my_rating", sa.Float(), nullable=True),
        sa.Column("comments_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "materialbundles_relation",
        sa.Column("material_id", sa.Integer(), nullable=False),
        sa.Column("bundle_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("material_id", "bundle_id"),
    )

    op.create_table(
        "notification",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("notification_seq"), nullable=False
        ),
        sa.Column("receiver_id", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(255), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column("content", postgresql.JSONB(), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("is_aggregatable", sa.Boolean(), nullable=False),
        sa.Column("aggregation_key", sa.String(255), nullable=True),
        sa.Column("aggregate_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized", sa.Boolean(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_notification_receiver_read_created",
        "notification",
        ["receiver_id", "read", "created_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_notification_aggregation",
        "notification",
        ["receiver_id", "aggregation_key", "aggregate_until"],
        postgresql_using="btree",
    )

    op.create_table(
        "passkey",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.Text(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("counter", sa.Integer(), nullable=False),
        sa.Column("device_type", sa.Text(), nullable=False),
        sa.Column("backed_up", sa.Boolean(), nullable=False),
        sa.Column("transports", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_passkey_user_id", "passkey", ["user_id"])

    op.create_table(
        "project",
        sa.Column("id", sa.BigInteger(), sa.Sequence("project_seq"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("color_code", sa.String(7), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("leader_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("external_task_id", sa.BigInteger(), nullable=True),
        sa.Column("github_repo", sa.String(255), nullable=True),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "question",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("type", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("bounty", sa.Integer(), nullable=False),
        sa.Column("accepted_answer_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "question_follower_relation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("follower_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "question_invitation_relation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "question_query_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("viewer_id", sa.Integer(), nullable=True),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("ip", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "question_search_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("keywords", sa.String(), nullable=False),
        sa.Column("first_question_id", sa.Integer(), nullable=True),
        sa.Column("page_size", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("searcher_id", sa.Integer(), nullable=True),
        sa.Column("ip", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "question_topic_relation",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "reaction_type",
        sa.Column("id", sa.Integer(), sa.Sequence("reaction_type_seq"), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    op.create_table(
        "space",
        sa.Column("id", sa.BigInteger(), sa.Sequence("space_seq"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("intro", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=True),
        sa.Column("enable_rank", sa.Boolean(), nullable=False),
        sa.Column("default_category_id", sa.Integer(), nullable=True),
        sa.Column("announcements", postgresql.JSONB(), nullable=False),
        sa.Column("task_templates", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "space_admin_relation",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("space_admin_relation_seq"),
            nullable=False,
        ),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "space_categories",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("space_categories_seq"), nullable=False
        ),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "space_user_rank",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("space_user_rank_seq"), nullable=False
        ),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "task",
        sa.Column("id", sa.BigInteger(), sa.Sequence("task_seq"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("intro", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("space_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("submitter_type", sa.SmallInteger(), nullable=False),
        sa.Column("approved", sa.SmallInteger(), nullable=False),
        sa.Column("participant_limit", sa.Integer(), nullable=True),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("registration_start_at", sa.DateTime(), nullable=True),
        sa.Column("default_deadline", sa.BigInteger(), nullable=False),
        sa.Column("resubmittable", sa.Boolean(), nullable=False),
        sa.Column("editable", sa.Boolean(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("require_real_name", sa.Boolean(), nullable=False),
        sa.Column("min_team_size", sa.Integer(), nullable=True),
        sa.Column("max_team_size", sa.Integer(), nullable=True),
        sa.Column("reject_reason", sa.String(), nullable=False),
        sa.Column("team_locking_policy", sa.String(50), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "team",
        sa.Column("id", sa.BigInteger(), sa.Sequence("team_seq"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("intro", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_team_name", "team", ["name"])

    op.create_table(
        "topic",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), sa.Sequence("user_id_seq"), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_ai_quota",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("user_ai_quota_seq", start=1, increment=50),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("daily_seu_quota", sa.Float(), nullable=True),
        sa.Column("remaining_seu", sa.Float(), nullable=True),
        sa.Column("total_seu_consumed", sa.Float(), nullable=True),
        sa.Column("last_reset_time", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_ai_quota_user_id", "user_ai_quota", ["user_id"])

    op.create_table(
        "user_following_relationship",
        sa.Column(
            "id",
            sa.Integer(),
            sa.Sequence("user_following_relationship_id_seq"),
            nullable=False,
        ),
        sa.Column("followee_id", sa.Integer(), nullable=False),
        sa.Column("follower_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_o_auth_connection",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("provider_user_id", sa.Text(), nullable=False),
        sa.Column("raw_profile", postgresql.JSONB(), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_o_auth_connection_user_id", "user_o_auth_connection", ["user_id"]
    )

    op.create_table(
        "user_profile",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("nickname", sa.String(), nullable=False),
        sa.Column("intro", sa.String(), nullable=False),
        sa.Column("avatar_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_real_name_access_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("accessor_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("module_type", sa.String(), nullable=True),
        sa.Column("module_entity_id", sa.Integer(), nullable=True),
        sa.Column("access_reason", sa.String(), nullable=False),
        sa.Column("ip_address", sa.String(), nullable=False),
        sa.Column("access_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_real_name_identities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("encrypted", sa.Boolean(), nullable=False),
        sa.Column("real_name", sa.String(), nullable=False),
        sa.Column("student_id", sa.String(), nullable=False),
        sa.Column("grade", sa.String(), nullable=False),
        sa.Column("major", sa.String(), nullable=False),
        sa.Column("class_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # -----------------------------------------------------------------------
    # Tables with FK dependencies
    # -----------------------------------------------------------------------
    op.create_table(
        "ai_message",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("ai_message_seq", start=1, increment=50),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("role", sa.String(255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_type", sa.Text(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("seu_consumed", sa.Float(), nullable=True),
        sa.Column("reasoning_content", sa.Text(), nullable=True),
        sa.Column("reasoning_time_ms", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["ai_conversation.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_message_conversation_id", "ai_message", ["conversation_id"])

    op.create_table(
        "project_membership",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("project_membership_seq"), nullable=False
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.SmallInteger(), nullable=False),
        sa.Column("notes", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "task_ai_advice",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("model_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("topic_summary", sa.Text(), nullable=True),
        sa.Column("knowledge_fields", sa.Text(), nullable=True),
        sa.Column("learning_paths", sa.Text(), nullable=True),
        sa.Column("methodology", sa.Text(), nullable=True),
        sa.Column("team_tips", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "task_ai_advice_context",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("task_ai_advice_context_seq"),
            nullable=False,
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("section", sa.String(64), nullable=True),
        sa.Column("section_index", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "task_membership",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("task_membership_seq"), nullable=False
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("participant_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved", sa.SmallInteger(), nullable=False),
        sa.Column("is_team", sa.Boolean(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("completion_status", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "task_submission_schema",
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("type", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.PrimaryKeyConstraint("task_id", "index"),
    )

    op.create_table(
        "task_topics_relation",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("task_topics_relation_seq"),
            nullable=False,
        ),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("topic_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "team_membership_application",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("team_membership_application_seq"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("initiator_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(255), nullable=False),
        sa.Column("status", sa.String(255), nullable=False),
        sa.Column("role", sa.String(255), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("processed_by_id", sa.Integer(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "team_user_relation",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("team_user_relation_seq"), nullable=False
        ),
        sa.Column("team_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "task_submission",
        sa.Column(
            "id", sa.BigInteger(), sa.Sequence("task_submission_seq"), nullable=False
        ),
        sa.Column("membership_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("submitter_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["membership_id"], ["task_membership.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "task_submission_entry",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("task_submission_entry_seq"),
            nullable=False,
        ),
        sa.Column("task_submission_id", sa.BigInteger(), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_attachment_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["task_submission_id"], ["task_submission.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "task_submission_review",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Sequence("task_submission_review_seq"),
            nullable=False,
        ),
        sa.Column("submission_id", sa.BigInteger(), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["submission_id"], ["task_submission.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order
    op.drop_table("task_submission_review")
    op.drop_table("task_submission_entry")
    op.drop_table("task_submission")
    op.drop_table("team_user_relation")
    op.drop_table("team_membership_application")
    op.drop_table("task_topics_relation")
    op.drop_table("task_submission_schema")
    op.drop_table("task_membership")
    op.drop_table("task_ai_advice_context")
    op.drop_table("task_ai_advice")
    op.drop_table("project_membership")
    op.drop_table("ai_message")
    op.drop_table("user_real_name_identities")
    op.drop_table("user_real_name_access_logs")
    op.drop_table("user_profile")
    op.drop_table("user_o_auth_connection")
    op.drop_table("user_following_relationship")
    op.drop_table("user_ai_quota")
    op.drop_table("user")
    op.drop_table("topic")
    op.drop_table("team")
    op.drop_table("task")
    op.drop_table("space_user_rank")
    op.drop_table("space_categories")
    op.drop_table("space_admin_relation")
    op.drop_table("space")
    op.drop_table("reaction_type")
    op.drop_table("question_topic_relation")
    op.drop_table("question_search_log")
    op.drop_table("question_query_log")
    op.drop_table("question_invitation_relation")
    op.drop_table("question_follower_relation")
    op.drop_table("question")
    op.drop_table("project")
    op.drop_table("passkey")
    op.drop_table("notification")
    op.drop_table("materialbundles_relation")
    op.drop_table("material_bundle")
    op.drop_table("material")
    op.drop_table("llm_call_log")
    op.drop_table("knowledge_upvote")
    op.drop_table("knowledge_label")
    op.drop_table("knowledge")
    op.drop_table("group_target")
    op.drop_table("group_question_relationship")
    op.drop_table("group_profile")
    op.drop_table("group_membership")
    op.drop_table("group")
    op.drop_table("discussion_reaction")
    op.drop_table("discussion_mentioned_users")
    op.drop_table("discussion")
    op.drop_table("comment")
    op.drop_table("avatar")
    op.drop_table("attitude")
    op.drop_table("attachment")
    op.drop_table("answer_vote")
    op.drop_table("answer_favorited_by_user")
    op.drop_table("answer")
    op.drop_table("ai_conversation")

    # Drop sequences
    op.execute(
        sa.schema.DropSequence(sa.Sequence("user_following_relationship_id_seq"))
    )
    op.execute(sa.schema.DropSequence(sa.Sequence("user_id_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("user_ai_quota_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("team_membership_application_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("team_user_relation_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("team_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("task_ai_advice_context_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("task_submission_review_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("task_submission_entry_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("task_submission_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("task_topics_relation_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("task_membership_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("task_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("space_admin_relation_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("space_user_rank_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("space_categories_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("space_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("reaction_type_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("project_membership_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("project_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("notification_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("knowledge_label_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("knowledge_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("discussion_reaction_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("discussion_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("ai_message_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("ai_conversation_seq")))

    # Drop enum types
    comment_commentable_type_enum.drop(op.get_bind(), checkfirst=True)
    avatar_type_enum.drop(op.get_bind(), checkfirst=True)
    attitude_type_enum.drop(op.get_bind(), checkfirst=True)
    attitudable_type_enum.drop(op.get_bind(), checkfirst=True)
