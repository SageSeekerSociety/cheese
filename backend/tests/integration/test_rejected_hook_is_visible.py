"""A rejected sandbox hook must leave a trace.

Split out of `tests/unit/test_deaf_sandbox_token.py` because it goes over HTTP
and the `client` fixture is DB-backed — `tests/unit/` is the no-server tier.
The defect it covers is the same one and the most expensive of the three: a
401'd hook used to vanish with no log at all, so a deaf sandbox looked exactly
like a model that never spoke, and every hypothesis went to the model side.
"""

import uuid

TOPIC = str(uuid.uuid4())


def test_a_rejected_hook_is_logged(client, caplog):
    """A 401'd hook is not hostile traffic to drop quietly — it is our own
    agent, locked out."""
    with caplog.at_level("WARNING"):
        r = client.post(
            f"/sandbox/hooks/{TOPIC}",
            json={"hook_event_name": "Stop"},
            headers={"X-Cheese-Token": "not-a-real-token"},
        )

    assert r.status_code == 401
    assert [rec for rec in caplog.records if "sandbox hook rejected" in rec.message]
