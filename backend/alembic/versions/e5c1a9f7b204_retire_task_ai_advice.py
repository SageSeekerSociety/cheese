"""Retire 启星研导: task advice, its conversations, and the daily AI quota

Revision ID: e5c1a9f7b204
Revises: d2b8f4a61c90
Create Date: 2026-10-01 06:30:00

The task page's generated advice and its chat were replaced by the person's
芝士 (assistant_conversations, #2285), and the per-person daily quota by
personal credits. Nothing reads these tables any more. Their rows are dropped
without a backup: the owner decided the old conversations need not be kept.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5c1a9f7b204"
down_revision: str | Sequence[str] | None = "d2b8f4a61c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "ai_message",
    "ai_conversation",
    "task_ai_advice_context",
    "task_ai_advice",
    "user_ai_quota",
)


#: Their standalone id sequences, which dropping a table does not take with it.
_SEQUENCES = (
    "ai_message_seq",
    "ai_conversation_seq",
    "task_ai_advice_context_seq",
    "user_ai_quota_seq",
)


#: The tables as they stood, rebuilt empty on the way down so that later
#: migrations' tests can walk the chain back past this one.
_SCHEMA = (
    """CREATE TABLE ai_conversation (
    id bigint NOT NULL,
    owner_id bigint NOT NULL,
    context_id bigint,
    conversation_id character varying(255) NOT NULL,
    title character varying(255),
    model_type text NOT NULL,
    module_type character varying(255) NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone
)""",
    """CREATE SEQUENCE ai_conversation_seq
    START WITH 1
    INCREMENT BY 50
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """CREATE TABLE ai_message (
    id bigint NOT NULL,
    conversation_id bigint NOT NULL,
    parent_id bigint,
    role character varying(255) NOT NULL,
    content text NOT NULL,
    model_type text NOT NULL,
    tokens_used integer,
    seu_consumed double precision,
    reasoning_content text,
    reasoning_time_ms integer,
    metadata_json text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone
)""",
    """CREATE SEQUENCE ai_message_seq
    START WITH 1
    INCREMENT BY 50
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """CREATE TABLE task_ai_advice (
    id bigint NOT NULL,
    task_id bigint NOT NULL,
    model_hash character varying(64) NOT NULL,
    status character varying(32) NOT NULL,
    topic_summary text,
    knowledge_fields text,
    learning_paths text,
    methodology text,
    team_tips text,
    raw_response text,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
)""",
    """CREATE TABLE task_ai_advice_context (
    id bigint NOT NULL,
    task_id bigint NOT NULL,
    section character varying(64),
    section_index integer,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone
)""",
    """CREATE SEQUENCE task_ai_advice_context_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """CREATE SEQUENCE task_ai_advice_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER SEQUENCE task_ai_advice_id_seq OWNED BY task_ai_advice.id""",
    """CREATE TABLE user_ai_quota (
    id bigint NOT NULL,
    user_id bigint,
    daily_seu_quota double precision,
    remaining_seu double precision,
    total_seu_consumed double precision,
    last_reset_time timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    deleted_at timestamp with time zone
)""",
    """CREATE SEQUENCE user_ai_quota_seq
    START WITH 1
    INCREMENT BY 50
    NO MINVALUE
    NO MAXVALUE
    CACHE 1""",
    """ALTER TABLE ONLY task_ai_advice ALTER COLUMN id SET DEFAULT nextval('task_ai_advice_id_seq'::regclass)""",
    """ALTER TABLE ONLY ai_conversation
    ADD CONSTRAINT ai_conversation_conversation_id_key UNIQUE (conversation_id)""",
    """ALTER TABLE ONLY ai_conversation
    ADD CONSTRAINT ai_conversation_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY ai_message
    ADD CONSTRAINT ai_message_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY task_ai_advice_context
    ADD CONSTRAINT task_ai_advice_context_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY task_ai_advice
    ADD CONSTRAINT task_ai_advice_pkey PRIMARY KEY (id)""",
    """ALTER TABLE ONLY user_ai_quota
    ADD CONSTRAINT user_ai_quota_pkey PRIMARY KEY (id)""",
    """CREATE INDEX ix_ai_conversation_owner_id ON ai_conversation USING btree (owner_id)""",
    """CREATE INDEX ix_ai_message_conversation_id ON ai_message USING btree (conversation_id)""",
    """CREATE INDEX ix_user_ai_quota_user_id ON user_ai_quota USING btree (user_id)""",
    """ALTER TABLE ONLY ai_message
    ADD CONSTRAINT ai_message_conversation_id_fkey FOREIGN KEY (conversation_id) REFERENCES ai_conversation(id)""",
    """ALTER TABLE ONLY task_ai_advice_context
    ADD CONSTRAINT task_ai_advice_context_task_id_fkey FOREIGN KEY (task_id) REFERENCES task(id)""",
    """ALTER TABLE ONLY task_ai_advice
    ADD CONSTRAINT task_ai_advice_task_id_fkey FOREIGN KEY (task_id) REFERENCES task(id)""",
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for sequence in _SEQUENCES:
        op.execute(f"DROP SEQUENCE IF EXISTS {sequence}")


def downgrade() -> None:
    # The rows are gone for good; only the empty tables come back.
    for statement in _SCHEMA:
        op.execute(statement)
