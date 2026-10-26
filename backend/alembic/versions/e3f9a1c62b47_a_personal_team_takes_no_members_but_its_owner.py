"""A personal team takes no members but its owner.

One person's team is theirs alone: personal projects belong to it, and it owns
their personal compute, model connections and credits. #2249 closed that door in
the application — ``TeamRepository.add_member`` (every way into a team ends
there) and the invitation door refuse anyone but the owner — but the rule lived
only in that one function. A raw write, a backfill, or a path added later could
still put a second person in one, and rows written before #2249 are still there.

This migration makes the rule hold in the database: a trigger on
``team_user_relation`` refuses a live membership row on a personal team whose
user is not that team's owner. Rows are still ended by setting ``deleted_at`` —
something that already exists is retired, never silently rewritten.

What it cleans up, all of it data the rule never allowed:

- live member rows on a personal team whose user is not its owner;
- pending membership applications (invitations and requests) aimed at a
  personal team — nothing can answer them yes, and a pending invitation would
  wait in its recipient's list for good;
- a join token on a personal team, if one ever got there: that team is reached
  by its owner's handle, never by a link;
- open recruitment posts on a personal team: the plaza lists posts by status
  and never looks at the team, so an OPEN one keeps offering a door that is not
  there.

The previous release keeps serving during the deploy and is not broken by any
of this: since #2249 it already refuses these same writes.

Revision ID: e3f9a1c62b47
Revises: a9c3e5f7b1d2
Create Date: 2026-10-26 16:10:00.000000

"""

from collections.abc import Sequence

from migration_helpers import with_lock_retries

from alembic import op

revision: str = "e3f9a1c62b47"
down_revision: str | Sequence[str] | None = "a9c3e5f7b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with_lock_retries(
        "team, team_user_relation, team_membership_application, team_recruitment_post"
    )
    # 1. Anyone who got in before the rule existed is out. Ending the row
    #    (deleted_at) keeps it as history; the trigger below is what says it can
    #    never come back.
    op.execute(
        """
        UPDATE team_user_relation r
        SET deleted_at = now(), updated_at = now()
        FROM team t
        WHERE r.team_id = t.id
          AND t.personal_owner_user_id IS NOT NULL
          AND r.deleted_at IS NULL
          AND r.user_id <> t.personal_owner_user_id
        """
    )
    # 2. An application into a personal team can never be answered yes. A
    #    request is refused by the team; an invitation is withdrawn.
    op.execute(
        """
        UPDATE team_membership_application a
        SET status = CASE a.type
                         WHEN 'REQUEST' THEN 'REJECTED'
                         ELSE 'CANCELED'
                     END,
            processed_at = now(),
            updated_at = now()
        FROM team t
        WHERE a.team_id = t.id
          AND t.personal_owner_user_id IS NOT NULL
          AND a.status = 'PENDING'
          AND a.deleted_at IS NULL
        """
    )
    # 3. A personal team has no address of its own: it goes by its owner's
    #    handle, and `TeamService.join_link` refuses to make it one. Clear any
    #    token that got there before that refusal existed.
    op.execute(
        """
        UPDATE team
        SET join_token = NULL, updated_at = now()
        WHERE personal_owner_user_id IS NOT NULL
          AND join_token IS NOT NULL
        """
    )
    # 4. An ad on a personal team asks people into a team that takes nobody.
    #    Withdrawn like the invitations above: the plaza lists posts by status
    #    and never looks at the team, so an OPEN one keeps offering a door that
    #    is not there.
    op.execute(
        """
        UPDATE team_recruitment_post p
        SET status = 'CLOSED', updated_at = now()
        FROM team t
        WHERE p.team_id = t.id
          AND t.personal_owner_user_id IS NOT NULL
          AND p.status = 'OPEN'
          AND p.deleted_at IS NULL
        """
    )
    # 5. The rule, where no code path can go around it.
    op.execute(
        """
        CREATE FUNCTION team_membership_respects_personal_teams()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE owner_id integer;
        BEGIN
            -- An ended row is history, not a member: soft-deleting the
            -- leftovers above (and anyone leaving later) stays possible.
            IF NEW.deleted_at IS NOT NULL THEN
                RETURN NEW;
            END IF;
            SELECT personal_owner_user_id INTO owner_id
            FROM team WHERE id = NEW.team_id;
            IF owner_id IS NOT NULL AND NEW.user_id <> owner_id THEN
                RAISE EXCEPTION
                    'a personal team takes no members but its owner'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER team_membership_respects_personal_teams
        BEFORE INSERT OR UPDATE ON team_user_relation
        FOR EACH ROW
        EXECUTE FUNCTION team_membership_respects_personal_teams()
        """
    )


def downgrade() -> None:
    """Drop the rule. What it ended stays ended: the rows it soft-deleted are
    indistinguishable from anyone having left the team, and the applications it
    answered are answered."""
    op.execute(
        "DROP TRIGGER team_membership_respects_personal_teams ON team_user_relation"
    )
    op.execute("DROP FUNCTION team_membership_respects_personal_teams()")
