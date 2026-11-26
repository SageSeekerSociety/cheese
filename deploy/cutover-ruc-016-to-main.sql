-- Move the RUC production database (cheese.ruc.edu.cn) off the 0.16.x release
-- line and onto main's migration chain.
--
-- The 0.16.x line branched off main's chain at f6b7c8d9e0a1 and added two
-- revisions main never had: 6a987e64b29c (agent chat + connector substrate)
-- and b1c2d3e4f5a6 (v2.0 agent layer). Main reached the same features through
-- its own chain (the cheesex tables joined at 4006c4e8b583), so it cannot
-- locate b1c2d3e4f5a6, and its d3b8f1a20c11 would collide with the 0.16.x
-- `device`, `device_auth_code` and `device_project` tables.
--
-- This applies the downgrade of those two revisions, so the database is
-- exactly main's f6b7c8d9e0a1 schema (checked table by table in the
-- rehearsal), and records that revision. From there the release deploy's own
-- `alembic upgrade head` carries it the rest of the way, the same path every
-- other database took. No landed migration is edited, and main's graph gains
-- no node only one database would ever use.
--
-- It is not an alembic revision because it cannot be one: alembic would need
-- it ordered before d3b8f1a20c11, which only an edit to that landed migration
-- could do, and `CREATE EXTENSION pg_search` needs a superuser the app's
-- `cheese` role is not.
--
-- What it removes: every row of the 0.16.x agent layer (threads, blocks,
-- documents, reactions, enrolled devices, screens) and the agent-layer
-- projects b1c2d3e4f5a6 allowed to have no team/start/end, with their
-- memberships. Main's own c9f2a3b40e15 drops the `project` table later in the
-- same upgrade, so those projects would not survive it either.
-- deploy/cutover-ruc-016-to-main.sh archives all of it first, beside the full
-- backup, and runs this file as a superuser in one transaction with the
-- 0.16.x backend stopped, after the UTF8 rebuild.

DO $$
BEGIN
  IF (SELECT version_num FROM alembic_version) IS DISTINCT FROM 'b1c2d3e4f5a6' THEN
    RAISE EXCEPTION 'expected alembic version b1c2d3e4f5a6 (0.16.x head), found %',
      (SELECT string_agg(version_num, ',') FROM alembic_version);
  END IF;
  IF (SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = current_database()) <> 'UTF8' THEN
    RAISE EXCEPTION 'database is not UTF8: run cutover-sqlascii-to-utf8.sh first';
  END IF;
END $$;

-- b1c2d3e4f5a6, downgraded.
DROP TABLE message_reaction, document;
DROP SEQUENCE message_reaction_seq, document_seq;

-- 6a987e64b29c, downgraded (block's indexes, ix_block_fts included, go with it).
DROP TABLE block, thread_membership_application, thread_membership, thread,
  agent_screen, device_project, device_auth_code, device;
DROP SEQUENCE device_project_seq, thread_seq, thread_membership_seq,
  thread_application_seq, block_seq;

-- b1c2d3e4f5a6 made these nullable for agent-layer projects; f6b7c8d9e0a1 has
-- them NOT NULL, so those projects go before the constraint comes back.
DELETE FROM project_membership
  WHERE project_id IN (SELECT id FROM project
                       WHERE team_id IS NULL OR start_date IS NULL OR end_date IS NULL);
DELETE FROM project WHERE team_id IS NULL OR start_date IS NULL OR end_date IS NULL;
ALTER TABLE project
  ALTER COLUMN team_id SET NOT NULL,
  ALTER COLUMN start_date SET NOT NULL,
  ALTER COLUMN end_date SET NOT NULL;

-- Main's migrations run `CREATE EXTENSION IF NOT EXISTS` for these as `cheese`,
-- which may not create them; created here, those statements find them present.
CREATE EXTENSION IF NOT EXISTS pg_search;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Accounts main's 514d7c9cb013 refuses to index over (its unique indexes are
-- case-insensitive and these predate them); the owner approved this handling.
-- The ten pm_owner / pm_other @t.local accounts are tests from 2026-07-07:
-- soft-deleted. Of each case-only username pair, the account with no password,
-- OAuth or activity keeps its name with its id appended.
UPDATE "user" SET deleted_at = now()
  WHERE id IN (449, 450, 451, 452, 453, 454, 455, 456, 457, 458) AND email LIKE '%@t.local';
UPDATE "user" SET username = username || '_' || id
  WHERE id IN (83, 206, 328, 474);

-- 514d7c9cb013's own duplicate queries. A duplicate left here would stop the
-- deploy's upgrade after this transaction committed, with 0.16.x already
-- unable to run; failing here rolls everything back instead.
DO $$
DECLARE dups text;
BEGIN
  SELECT string_agg(label || ' ' || value || ' ' || ids::text, '; ') INTO dups FROM (
    SELECT 'username', lower(username), array_agg(id ORDER BY id) FROM "user"
      WHERE deleted_at IS NULL GROUP BY lower(username) HAVING count(*) > 1
    UNION ALL
    SELECT 'email', lower(email), array_agg(id ORDER BY id) FROM "user"
      WHERE deleted_at IS NULL GROUP BY lower(email) HAVING count(*) > 1
    UNION ALL
    SELECT 'user_profile', user_id::text, array_agg(id ORDER BY id) FROM user_profile
      WHERE deleted_at IS NULL GROUP BY user_id HAVING count(*) > 1
    UNION ALL
    SELECT 'oauth', provider_id || ':' || provider_user_id, array_agg(id ORDER BY id)
      FROM user_o_auth_connection GROUP BY provider_id, provider_user_id HAVING count(*) > 1
    UNION ALL
    SELECT 'passkey', credential_id, array_agg(id ORDER BY id) FROM passkey
      GROUP BY credential_id HAVING count(*) > 1
  ) AS d(label, value, ids);
  IF dups IS NOT NULL THEN
    RAISE EXCEPTION 'accounts 514d7c9cb013 cannot index: %', dups;
  END IF;
END $$;

UPDATE alembic_version SET version_num = 'f6b7c8d9e0a1';
