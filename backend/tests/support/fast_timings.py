"""One place that makes the product wait milliseconds instead of minutes.

A test that exercises a retry, a poll loop or a give-up timeout otherwise has two
options, and the tree has paid for both: sleep for real — the layers' 10/15/30 s
ceilings in ``backend/tests/conftest.py`` exist because tests did this — or hand
``monkeypatch.setattr`` the same half-dozen knobs, which is how the same knob ends
up pinned in five files with five different values, none of them the one the code
actually computes against.

``fast_timings`` is the second option done once. Ask for the fixture and the
knobs below are already at millisecond values for that test; add one that is not
in the list with ``fast_timings.set(module, "NAME")``. Monkeypatch undoes all of
it at teardown, so nothing leaks into the next test.

When *not* to ask for it. A test that asserts the arithmetic of a wait — "the
wait doubles with each failure up to the cap" — is judging the numbers, and this
fixture is for tests that only need the wait over with. Two kinds of knob are
deliberately absent for the same reason: TTLs and lease durations (shrinking one
does not shorten a wait, it changes what the test means) and the login/lockout
backoff constants (asserted on as values, not waited out).

Read the target table as a claim about the product: each entry must be a
``NAME = value`` assignment in the module naming it, because that is the binding
the module's own readers see. A constant imported into a module and read from
there would be pinned in the wrong place — ``tests/unit/test_fast_timings.py``
holds every entry to that.
"""

import importlib

# What "millisecond-scale" means here. One value on purpose: a test that needs a
# different one needs a reason, and the reason is more readable at the call site
# than as a second constant in this table.
FAST_S = 0.05

# Modules that define the knob and read it themselves at call time.
MODULE_KNOBS = (
    ("app.api.routes.notifications_live", "BEAT_SECONDS"),
    ("app.domain.agent.device_hub", "LINK_SILENCE_S"),
    ("app.domain.agent.device_hub_rpc", "OWNER_CONNECT_RETRY_MAX_DELAY_S"),
    ("app.domain.agent.device_hub_rpc", "OWNER_CONNECT_RETRY_WINDOW_S"),
    ("app.domain.agent.room.sessions", "RUNNER_GONE_S"),
    ("app.domain.agent.session_host.claude_code", "STARTUP_WAIT_S"),
    ("app.domain.agent.session_host.host", "READ_WAIT_S"),
    ("app.domain.agent.turn_adoption", "RECONCILE_EVERY_S"),
    ("app.domain.machine.enrollment", "SSH_READY_POLL_S"),
    ("app.domain.machine.enrollment", "SSH_READY_WAIT_S"),
    ("app.domain.machine.session_work", "PREPARING_WAIT_S"),
    ("app.domain.review.events", "RECONNECT_DELAY_S"),
)

# Settings fields, with the value each one takes. They are not all FAST_S: three
# are declared ``int``, and one of those is ``gt=0``, so the smallest value it
# accepts is 1 — writing 0.05 there would sit in the model unvalidated (this
# settings class does not validate assignment) and then be read back as a float
# where the type says int. Values match what tests already pin these to.
SETTINGS_KNOBS = (
    ("chat_progress_reminder_after_s", 1),
    ("microcloud_reconcile_interval_s", 0.0),
    ("subscription_ingest_interval_s", 0),
    ("topic_archive_cleanup_delay_s", 0),
    ("turn_silence_timeout_s", FAST_S),
)


class FastTimings:
    """The applied knobs, plus a way to add one this table does not carry."""

    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch
        self.applied = {}

    def apply(self):
        module_imports = {
            name: importlib.import_module(name) for name, _ in MODULE_KNOBS
        }
        for module_name, name in MODULE_KNOBS:
            self.set(
                module_imports[module_name], name, FAST_S, label=f"{module_name}.{name}"
            )
        from app.core.config import settings

        for name, value in SETTINGS_KNOBS:
            self.set(settings, name, value, label=f"settings.{name}")
        return self

    def set(self, target, name, value=FAST_S, label=None):
        """Pin one more knob for this test, and remember what it became."""
        self._monkeypatch.setattr(target, name, value)
        self.applied[label or name] = value
        return value
