"""models belong to teammates; a project keeps one main model

Revision ID: c4e8a2f61d93
Revises: e5a17c3d9b42
Create Date: 2026-11-17

A model is now a teammate's setting: what it runs on in conversation
(``model``), in a task (``task_model``) and for its subagents
(``subagent_model``). A project keeps only its main model (``default_model``),
the default for a teammate that chose none; with none set, the platform picks
per scene (「自动」).

Two project settings move onto teammates:

* ``default_subagent_model`` (the project's subagent default) is written into
  every teammate of the project whose ``subagent_model`` is unset, and the
  project key is removed. A teammate on a member's own account is left alone:
  it runs on its own login, which no project model reaches.
* A project made from a new-user starter had its 题目 pack's model written in
  as its main model. That model is what the pack's credits do the work on, so
  it moves to the project's default teammate's ``task_model`` (when unset) and
  the main model is cleared, leaving conversation to the platform's pick. A
  starter's 题目 is one ``onboarding_starters`` names, or one whose pack is the
  starters' own (a ``model`` and ``once_per_person``: an earlier starter that
  was deleted and made again keeps that pack). Only a project whose main model
  still equals its pack's model moves; one a member changed keeps its choice.

Each teammate written gets its ``version`` raised, so an editor open on the old
configuration is refused rather than writing it back. Every statement filters
on what is not yet done, so a rerun changes nothing.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e8a2f61d93"
down_revision: str | Sequence[str] | None = "e5a17c3d9b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUBAGENT_DEFAULT_TO_TEAMMATES = """
UPDATE agent_instances AS ai
SET configuration = (
        ai.configuration::jsonb
        || jsonb_build_object(
            'subagent_model', p.settings::jsonb ->> 'default_subagent_model'
        )
    )::json,
    version = ai.version + 1,
    updated_at = now()
FROM projects AS p
WHERE ai.project_id = p.id
  AND coalesce(p.settings::jsonb ->> 'default_subagent_model', '') <> ''
  AND coalesce(ai.configuration::jsonb ->> 'subagent_model', '') = ''
  AND NOT EXISTS (SELECT 1 FROM own_agents AS o WHERE o.instance_id = ai.id)
"""

_DROP_SUBAGENT_DEFAULT = """
UPDATE projects
SET settings = (settings::jsonb - 'default_subagent_model')::json
WHERE settings::jsonb ? 'default_subagent_model'
"""

# The starters' projects whose main model is still their pack's model, with it.
_STARTER_PROJECTS = """
SELECT p.id AS project_id,
       p.default_agent_instance_id AS agent_id,
       t.protocol_override::jsonb -> 'resource_pack' ->> 'model' AS model
FROM projects AS p
JOIN task AS t ON t.id = p.external_task_id
WHERE coalesce(t.protocol_override::jsonb -> 'resource_pack' ->> 'model', '') <> ''
  AND p.settings::jsonb ->> 'default_model'
      = t.protocol_override::jsonb -> 'resource_pack' ->> 'model'
  AND (
      t.id IN (SELECT task_id FROM onboarding_starters)
      OR t.protocol_override::jsonb -> 'resource_pack' -> 'once_per_person'
         = 'true'::jsonb
  )
"""

_STARTER_MODEL_TO_TEAMMATE = f"""
UPDATE agent_instances AS ai
SET configuration = (
        ai.configuration::jsonb || jsonb_build_object('task_model', s.model)
    )::json,
    version = ai.version + 1,
    updated_at = now()
FROM ({_STARTER_PROJECTS}) AS s
WHERE ai.id = s.agent_id
  AND coalesce(ai.configuration::jsonb ->> 'task_model', '') = ''
"""

_CLEAR_STARTER_MAIN_MODEL = f"""
UPDATE projects AS p
SET settings = (p.settings::jsonb - 'default_model')::json
FROM ({_STARTER_PROJECTS}) AS s
WHERE p.id = s.project_id
"""


def upgrade() -> None:
    op.execute(sa.text(_SUBAGENT_DEFAULT_TO_TEAMMATES))
    op.execute(sa.text(_DROP_SUBAGENT_DEFAULT))
    # The teammate first: clearing the main model is what stops a project
    # matching, so the other order would move nothing.
    op.execute(sa.text(_STARTER_MODEL_TO_TEAMMATE))
    op.execute(sa.text(_CLEAR_STARTER_MAIN_MODEL))


def downgrade() -> None:
    """Leaves the models on the teammates.

    A teammate's ``subagent_model`` or ``task_model`` cannot be told apart from
    one a person chose, and the previous release ignores both keys."""
