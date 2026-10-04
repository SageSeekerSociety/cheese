"""an answer is a versioned log

A question's answer was two keys on the block's ``meta``: ``answered`` held the
chosen option and ``answered_by`` the handle that chose it. One value each, so
there was nowhere to put a correction, and no record of which of the two the
room was looking at after somebody changed their mind. This is now
``answer_log``, an ordered list whose last entry is the one in force; the
entries before it are the answers that were replaced and they stay.

``options`` moves with it: a plain list of the option texts becomes a list of
``{"text": ...}`` objects so an option can carry an explanation. Order is the
only thing that makes an option list mean anything, so the rebuild walks the
array with ``WITH ORDINALITY`` and aggregates in that order rather than letting
``jsonb_agg`` pick one.

What this deliberately does NOT invent: the moment an old answer was given is
not recorded anywhere on the block, so the migrated entry gets ``at: null``. A
timestamp read off the migration clock would be a lie the room would keep
reading. The entry does carry what the block really held — the option and the
handle — plus ``client_op_id: "migrated"``, which is how the answer path
recognises a row it did not write. ``asked`` is left exactly as it was, and
``allow_other`` / ``reject_option`` / ``ask_group`` are not added: those only
exist on questions asked after this, and an absent key is the honest reading
for the old ones.

Only the shape the ask route ever produced is touched (``meta`` an object,
``options`` an array, every element a string) — same discipline as
``89fb9b9a11e0``: an array of any other shape was never written, and guessing
at one would be a second corruption.

Who gets converted is decided by WHO SIGNED THE QUESTION, not by anything the
row says about how it was asked (an old row records no origin, and inventing
one would be the lie above) and not by any roster that only describes today.
An agent's history moves because the agent's answer path now writes the log; a
person's question stays in the two keys it was asked with, because the person's
answer path — the one click from the composer — still writes exactly that. So
the scope is rows whose ``author`` the backend itself calls an agent: the union
of the historical default ``cheese``, of every handle derived from an
``agent_instances`` row (``cheese-`` plus the first 12 hex chars of the
instance id — a pure function of the id, so it survives the seat being
retired), and of every username carrying an ``agent_bindings`` row, which is
the very test ``IdentityService.is_agent`` makes. No prefix guessing: a human
cannot register under ``cheese``/``cheese-…`` (#345), so those strings match
exactly.

Upgrade and downgrade use that same scope. A retired seat's rows are in it on
purpose: retiring drops the roster entry, not the agent's identity, and what it
signed is still the agent's history.

Revision ID: e5a1c7d3b284
Revises: c3e8a51f0d27
Create Date: 2026-09-30

This unreleased first Ask revision follows the current main head. The following
Ask revisions retain their order and released parents stay intact.

"""

from collections.abc import Sequence

from alembic import op

revision: str = "e5a1c7d3b284"
down_revision: str | Sequence[str] | None = "c1f7a09b34d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `meta` is json, not jsonb (see `Block.meta`), so cast to jsonb to walk it
    # and cast back on assignment. The author filter is the scope spelled out in
    # the module docstring: only what an agent signed is converted, so a person's
    # question keeps the two keys its own answer path still writes.
    op.execute(
        """
        WITH agent_authors AS (
            SELECT 'cheese' AS handle
            UNION
            SELECT 'cheese-' || left(replace(id::text, '-', ''), 12)
              FROM agent_instances
            UNION
            SELECT u.username
              FROM "user" u
              JOIN agent_bindings ab ON ab.user_id = u.id
        ),
        ask AS (
            SELECT b.id, to_jsonb(b.meta) AS m
              FROM blocks b
             WHERE b.meta IS NOT NULL
               AND json_typeof(b.meta) = 'object'
               AND json_typeof(b.meta -> 'options') = 'array'
               AND NOT EXISTS (
                   SELECT 1
                     FROM jsonb_array_elements(to_jsonb(b.meta) -> 'options') AS e
                    WHERE jsonb_typeof(e) <> 'string'
               )
               AND b.author IN (SELECT handle FROM agent_authors)
        ),
        rebuilt AS (
            SELECT id,
                   (m - 'options' - 'answered' - 'answered_by')
                     || jsonb_build_object(
                          'options',
                          (
                              SELECT COALESCE(
                                         jsonb_agg(
                                             jsonb_build_object('text', e)
                                             ORDER BY ord
                                         ),
                                         '[]'::jsonb
                                     )
                                FROM jsonb_array_elements_text(m -> 'options')
                                     WITH ORDINALITY AS t(e, ord)
                          )
                        )
                     || CASE
                          WHEN m -> 'answered' IS NOT NULL
                               AND jsonb_typeof(m -> 'answered') <> 'null'
                          THEN jsonb_build_object(
                                   'answer_log',
                                   jsonb_build_array(
                                       jsonb_build_object(
                                           'v', 1,
                                           'kind', 'option',
                                           'option', m -> 'answered',
                                           'note', NULL::text,
                                           'by', COALESCE(
                                               m ->> 'answered_by', ''
                                           ),
                                           'at', NULL::text,
                                           'client_op_id', 'migrated'
                                       )
                                   )
                               )
                          ELSE '{}'::jsonb
                        END AS new_meta
              FROM ask
        )
        UPDATE blocks b
           SET meta = r.new_meta::json
          FROM rebuilt r
         WHERE b.id = r.id
        """
    )


def downgrade() -> None:
    # Restores the two keys and loses what they never held. `options` goes back
    # to a list of texts. `answered` / `answered_by` come back from the LAST log
    # entry — the one in force — because a pair of single-valued keys cannot
    # hold a history: a question that was corrected comes back showing its final
    # answer with no sign it ever held another. `at` was already lost on the way
    # up and is not recoverable from anywhere, so it is not written back.
    op.execute(
        """
        WITH agent_authors AS (
            SELECT 'cheese' AS handle
            UNION
            SELECT 'cheese-' || left(replace(id::text, '-', ''), 12)
              FROM agent_instances
            UNION
            SELECT u.username
              FROM "user" u
              JOIN agent_bindings ab ON ab.user_id = u.id
        ),
        ask AS (
            SELECT b.id, to_jsonb(b.meta) AS m
              FROM blocks b
             WHERE b.meta IS NOT NULL
               AND json_typeof(b.meta) = 'object'
               AND json_typeof(b.meta -> 'options') = 'array'
               AND b.author IN (SELECT handle FROM agent_authors)
        ),
        texts AS (
            SELECT id, m,
                   (
                       SELECT COALESCE(
                                  jsonb_agg(
                                      CASE
                                          WHEN jsonb_typeof(e) = 'string'
                                          THEN e
                                          ELSE to_jsonb(
                                              COALESCE(e ->> 'text', '')
                                          )
                                      END
                                      ORDER BY ord
                                  ),
                                  '[]'::jsonb
                              )
                         FROM jsonb_array_elements(m -> 'options')
                              WITH ORDINALITY AS t(e, ord)
                   ) AS opts,
                   (m -> 'answer_log') AS log
              FROM ask
        ),
        rebuilt AS (
            SELECT id,
                   (m - 'options' - 'answer_log')
                     || jsonb_build_object('options', opts)
                     || CASE
                          WHEN jsonb_typeof(log) = 'array'
                               AND jsonb_array_length(log) > 0
                          THEN jsonb_build_object(
                                   'answered',
                                   log -> (jsonb_array_length(log) - 1)
                                        -> 'option',
                                   'answered_by',
                                   COALESCE(
                                       log -> (jsonb_array_length(log) - 1)
                                            ->> 'by',
                                       ''
                                   )
                               )
                          ELSE '{}'::jsonb
                        END AS new_meta
              FROM texts
        )
        UPDATE blocks b
           SET meta = r.new_meta::json
          FROM rebuilt r
         WHERE b.id = r.id
        """
    )
