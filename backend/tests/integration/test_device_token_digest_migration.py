"""a9c3e5f7b1d2: an enrolled machine keeps connecting once only its token's
digest is stored, including a machine the previous release enrolls while the
deploy is under way."""

import uuid

from app.domain.user.tokens import digest_token
from tests.integration.migration_replay import database_at, seed_room

BEFORE = "d7e1a2b3c4f5"
AFTER = "a9c3e5f7b1d2"


def _enrol(db, device_id: str, token: str) -> None:
    db.execute(
        "INSERT INTO device (device_id, name, token, team_id, created_at)"
        " VALUES ($1, 'm', $2, (SELECT team_id FROM projects LIMIT 1), now())",
        device_id,
        token,
    )


def test_machines_enrolled_before_and_during_the_deploy_get_their_digest():
    with database_at(BEFORE) as db:
        seed_room(db)
        before = f"tok-{uuid.uuid4().hex}"
        _enrol(db, "before", before)
        db.execute(
            "INSERT INTO model_credential_hosts (id, token, name) VALUES ($1, $2, 'm')",
            "host",
            before,
        )

        db.upgrade(AFTER)

        during = f"tok-{uuid.uuid4().hex}"
        # The previous release, still serving, inserts a token and no digest.
        _enrol(db, "during", during)
        digests = {
            row["device_id"]: row["token_digest"]
            for row in db.fetch("SELECT device_id, token_digest FROM device")
        }
        assert digests == {
            "before": digest_token(before),
            "during": digest_token(during),
        }
        assert db.fetchval(
            "SELECT token_digest FROM model_credential_hosts WHERE id = 'host'"
        ) == digest_token(before)
        # The release itself enrols with no token at all.
        db.execute(
            "INSERT INTO device (device_id, name, team_id, created_at)"
            " VALUES ('new', 'm', (SELECT team_id FROM projects LIMIT 1), now())"
        )
