"""A screen whose hook token stopped verifying is DEAF, and used to stay deaf.

The failure shape, which is what made this expensive: the turn observes nothing
at all. `first_output_s: null`, `tools: 0`, runs to its ceiling — identical to a
model that never spoke, so every hypothesis went to the model side (credentials,
gateway, disk, machine setup) while `claude` on the machine was working
perfectly and its every hook was being 401'd and dropped without a log line.

The mechanism:

* the hook forwarder sends the ``CHEESE_TOKEN`` its `claude` was launched with,
  on every event;
* that token is written into the session's environment at launch and never
  refreshed — `claude` read it once at exec, and the session is reused across
  many turns, so the freshly minted per-turn token never reaches it;
* ``sandbox_auth.SANDBOX_TOKEN`` used to fall back to a fresh random per PROCESS
  when the deployment did not pin it, so a backend restart re-signed with a new
  secret and every live session's token stopped verifying at once;
* nothing rebuilt them, so a restart alone left every topic permanently unable
  to reply.

The THIRD bullet is the root cause and it is fixed here: the signing secret is
derived from `jwt_secret` when unpinned, so it survives a restart and the
deployment-wide outage cannot happen at all.

The remaining defect — the 401 must leave a trace — goes over HTTP, so it lives
in `tests/integration/test_rejected_hook_is_visible.py`.
"""

from app.core.config import Settings


def test_the_signing_secret_survives_a_restart_without_being_pinned():
    """The root cause, and the reason the whole class of outage is gone: two
    processes of the same deployment must derive the SAME secret when
    SANDBOX_TOKEN is not pinned. A random per process re-signed everything on
    every restart and took the whole deployment deaf at once."""
    one = Settings(jwt_secret="a-real-deployment-secret")
    two = Settings(jwt_secret="a-real-deployment-secret")
    assert one.sandbox_signing_secret == two.sandbox_signing_secret
    # ...and it is neither empty nor the jwt secret itself (a domain separator
    # keeps a rotation of one from silently rotating the other).
    assert one.sandbox_signing_secret
    assert one.sandbox_signing_secret != one.jwt_secret
    # A pinned value still wins, so an operator can rotate it independently.
    assert Settings(sandbox_token="pinned").sandbox_signing_secret == "pinned"
    # A different deployment gets a different secret.
    assert (
        Settings(jwt_secret="another-deployment").sandbox_signing_secret
        != one.sandbox_signing_secret
    )
