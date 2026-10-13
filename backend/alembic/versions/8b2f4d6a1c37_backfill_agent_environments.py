"""Copy each room's, task's, session's and project's machine choice into the
execution environment that replaces it

Revision ID: 8b2f4d6a1c37
Revises: 5e1a7c3b9d24
Create Date: 2026-10-12

A choice was `{"profile": "cloud" | "device", "device_id", "whole_machine"}`,
and what an agent on a device could see of it was the room's grant in
`device_topic.visibility`. Each becomes an environment (`agent.environment`):

- cloud: a sandbox of spec `standard`; with `whole_machine`, spec `vm`;
- a device D the room was given whole (`visibility = 'host'`): self-hosted D;
- any other device D: a sandbox on its owner's own computers,
  `devices_of:<owner>`, so an agent that worked on a person's machine stays on
  that person's machines;
- a device the platform was to pick, with nothing that says which: no
  environment, so the default applies. For a session, D is the device its lease
  is on; for a room or a task, the device the room was pinned to.

A lease held on one of the platform's machines is marked `platform`, which is
what keeps that machine out of every page that shows the session.

A project's default is copied the same way into `settings.execution_environment`;
one that left the device to the platform takes the owner of the oldest
self-hosted machine attached to the project or its team, when there is one.

Every statement fills only what is still empty, so a rerun (and the release
after this one, re-running it for rows the outgoing release wrote) is safe.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8b2f4d6a1c37"
down_revision: str | Sequence[str] | None = "5e1a7c3b9d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _environment(choice: str, device: str, room: str) -> str:
    """SQL for the environment ``choice`` (a json expression) becomes, with
    ``device`` the machine it means (a text expression, NULL for none) and
    ``room`` the room whose grant on that machine applies."""
    return f"""
    (CASE
      WHEN {choice}->>'profile' = 'cloud'
           AND COALESCE(({choice}->>'whole_machine')::boolean, false)
        THEN '{{"kind": "sandbox", "spec": "vm"}}'::json
      WHEN {choice}->>'profile' = 'cloud'
        THEN '{{"kind": "sandbox", "spec": "standard"}}'::json
      WHEN {choice}->>'profile' = 'device' AND EXISTS (
             SELECT 1 FROM device_topic dt
             WHERE dt.topic_id = {room} AND dt.device_id = {device}
               AND dt.visibility = 'host'
           ) AND EXISTS (SELECT 1 FROM device d WHERE d.device_id = {device})
        THEN json_build_object('kind', 'self_hosted', 'device_id', {device})
      WHEN {choice}->>'profile' = 'device'
        THEN (
          SELECT json_build_object(
            'kind', 'sandbox', 'spec', 'devices_of:' || d.owner_user_id
          )
          FROM device d
          WHERE d.device_id = {device} AND d.supply = 'self_hosted'
        )
    END)::jsonb
    """


def _pinned(room: str) -> str:
    return f"(SELECT dt.device_id FROM device_topic dt WHERE dt.topic_id = {room})"


def upgrade() -> None:
    session_room = "COALESCE((SELECT t.room_id FROM tasks t WHERE t.id = s.conversation_id), s.conversation_id)"
    leased = (
        "(SELECT d.device_id FROM device d"
        " WHERE d.device_id = s.work_lease->>'device_id'"
        " AND d.supply = 'self_hosted')"
    )
    op.execute(
        f"""
        INSERT INTO agent_environments
          (conversation_id, agent_handle, environment, chosen_by, updated_at)
        SELECT conversation_id, agent_handle, environment, chosen_by, now()
        FROM (
          SELECT DISTINCT ON (s.conversation_id, s.agent_handle)
            s.conversation_id,
            s.agent_handle,
            {
            _environment(
                "(s.execution_request->'choice')",
                f"COALESCE(s.execution_request->'choice'->>'device_id', {leased})",
                session_room,
            )
        } AS environment,
            (s.execution_request->'authorized_by')::jsonb AS chosen_by
          FROM agent_sessions s
          WHERE s.execution_request->'choice' IS NOT NULL
            AND json_typeof(s.execution_request->'choice') = 'object'
          ORDER BY s.conversation_id, s.agent_handle, s.updated_at DESC
        ) chosen
        WHERE environment IS NOT NULL
        ON CONFLICT (conversation_id, agent_handle) DO NOTHING
        """
    )
    op.execute(
        f"""
        UPDATE topics t
        SET default_environment = {
            _environment(
                "t.compute_config",
                f"COALESCE(t.compute_config->>'device_id', {_pinned('t.id')})",
                "t.id",
            )
        }
        WHERE t.default_environment IS NULL
          AND t.compute_config IS NOT NULL
          AND json_typeof(t.compute_config) = 'object'
        """
    )
    op.execute(
        f"""
        UPDATE tasks k
        SET default_environment = {
            _environment(
                "k.compute_config",
                f"COALESCE(k.compute_config->>'device_id', {_pinned('k.room_id')})",
                "k.room_id",
            )
        }
        WHERE k.default_environment IS NULL
          AND k.compute_config IS NOT NULL
          AND json_typeof(k.compute_config) = 'object'
        """
    )
    op.execute(
        """
        UPDATE agent_sessions s
        SET work_lease = (s.work_lease::jsonb || '{"platform": true}')::json
        WHERE s.work_lease->>'device_id' IN (
            SELECT d.device_id FROM device d WHERE d.supply = 'cloud'
          )
          AND NOT (s.work_lease::jsonb ? 'platform')
        """
    )
    attached = """
        (SELECT d.device_id FROM device d
         WHERE d.supply = 'self_hosted' AND (
           d.device_id IN (SELECT dp.device_id FROM device_project dp
                           WHERE dp.project_id = p.id)
           OR d.device_id IN (SELECT dt.device_id FROM device_team dt
                              WHERE dt.team_id = p.team_id))
         ORDER BY d.created_at LIMIT 1)
    """
    default = "(p.settings::jsonb->'compute_configs'->'default')::json"
    op.execute(
        f"""
        UPDATE projects p
        SET settings = (
          p.settings::jsonb || jsonb_build_object(
            'execution_environment',
            {
            _environment(
                default,
                f"COALESCE({default}->>'device_id', {attached})",
                "NULL::uuid",
            )
        }
          )
        )::json
        WHERE p.settings::jsonb ? 'compute_configs'
          AND NOT p.settings::jsonb ? 'execution_environment'
          AND jsonb_typeof(p.settings::jsonb->'compute_configs'->'default') = 'object'
          AND ({
            _environment(
                default,
                f"COALESCE({default}->>'device_id', {attached})",
                "NULL::uuid",
            )
        }) IS NOT NULL
        """
    )


def downgrade() -> None:
    """Nothing to undo: the old choices were left where they were, the
    previous migration's downgrade drops the columns this filled, and the
    release before this one reads neither ``settings.execution_environment``
    nor a lease's ``platform``."""
