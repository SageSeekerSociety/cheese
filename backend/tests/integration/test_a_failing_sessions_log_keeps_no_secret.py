"""What a session printed as it died is kept for 现场, without the credentials
in it.

The log of a session that failed to start goes into the turn's run record, the
failed row 现场 shows, and the room is told one sentence. A credential the
process printed (a token in a clone URL, a bearer header) reaches neither.
"""

import pytest

from app.domain.agent.session_host.driver import startup_refused
from tests.conftest import StubChannel
from tests.integration.test_session_start_failure_notice import _turn

SECRET = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
LOG = f"""cheese-runner 0f0f ended: Claude Code exited with status 1 before it started:
git clone https://x-access-token:{SECRET}@github.com/acme/rocket.git failed
curl -H 'Authorization: Bearer {SECRET}' https://api.github.com/ refused"""


class DiesPrintingItsToken(StubChannel):
    async def open(self, session, agent, launch):
        del session, agent, launch
        raise startup_refused(LOG, harness="Claude Code")


@pytest.fixture
def stub_hooks() -> DiesPrintingItsToken:
    # Overrides conftest's stub_hooks for this module; `client` picks it up.
    return DiesPrintingItsToken()


def test_the_failed_row_keeps_the_log_and_drops_the_token(client):
    room, frames = _turn(client)

    site = client.get(f"/topics/{room}/transcript").json()["data"]["data"]
    (row,) = [b for b in site if b.get("run_record") and b["meta"].get("failed")]
    assert "git clone" in row["meta"]["error"]
    assert "Authorization" in row["meta"]["error"]
    blocks = client.get(f"/topics/{room}/blocks").json()["data"]["data"]
    for kept in (site, blocks, frames):
        assert SECRET not in repr(kept)
