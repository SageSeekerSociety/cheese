"""Which template databases a run may clean up, and which are not its business.

The rule used to be "every template but mine", written inline in an async
function that talked to a live Postgres — so the only way to test it was to race
a server, and it shipped the race. Two runs on one machine whose migration
histories differ (two worktrees, one on a branch with a new migration) are each
"the other's stale template", so they deleted each other's, back and forth, and
a copy that landed on a just-dropped template died in setup with
``template database "cheesex_tpl_<hash>" does not exist`` — a red that pointed
at the code under test instead of at the harness.

The rule is a pure function now (``tests.conftest._templates_to_drop``), so
these tests are that rule, spelled out.
"""

from tests.conftest import _TEMPLATE_GRACE_S, _templates_to_drop

OWN = "cheesex_tpl_aaaaaaaaaaaa"
OTHER = "cheesex_tpl_bbbbbbbbbbbb"

# An arbitrary fixed instant; the rule never reads the clock itself.
NOW = 1_700_000_000.0


def _old_rule(existing, own):
    """The rule this replaces, as it was: drop every template that is not mine."""
    return sorted(name for name in existing if name != own)


def test_own_template_is_never_dropped():
    """The next thing this run does is copy from it — however old it looks."""
    assert _templates_to_drop([OWN], OWN, {OWN: 0.0}, now=NOW) == []


def test_a_template_another_history_just_built_survives():
    """The exact case that used to red: B finishes its build three seconds after
    A's, A's template looks like a superseded history to B, and B drops it while
    A is copying from it."""
    doomed = _templates_to_drop([OWN, OTHER], OWN, {OTHER: NOW - 3}, now=NOW)
    assert doomed == []


def test_the_old_rule_fails_this():
    """Self-proof that the change is the fix and not decoration: the rule being
    replaced drops a template another history built three seconds ago, which is
    precisely what the new one refuses to do."""
    existing, last_used = [OWN, OTHER], {OTHER: NOW - 3}
    assert _old_rule(existing, OWN) == [OTHER]
    assert _templates_to_drop(existing, OWN, last_used, now=NOW) == []


def test_a_template_nobody_used_for_longer_than_the_grace_is_dropped():
    """The cleanup the rule still has to do: a history that has not been copied
    from in a long time is not coming back, and its 90-table copy is what fills
    the resident Postgres's tmpfs."""
    doomed = _templates_to_drop(
        [OWN, OTHER], OWN, {OTHER: NOW - _TEMPLATE_GRACE_S - 1}, now=NOW
    )
    assert doomed == [OTHER]


def test_the_grace_boundary_keeps_and_drops_on_the_right_sides():
    """A threshold nobody can read off the code is a threshold that drifts."""
    fresh = {OTHER: NOW - _TEMPLATE_GRACE_S + 1}
    exactly_at = {OTHER: NOW - _TEMPLATE_GRACE_S}
    assert _templates_to_drop([OWN, OTHER], OWN, fresh, now=NOW) == []
    assert _templates_to_drop([OWN, OTHER], OWN, exactly_at, now=NOW) == [OTHER]


def test_a_template_with_no_recorded_use_counts_as_stale():
    """Nothing this version builds goes unstamped, so an unstamped template was
    built by an older one and cannot be placed in time. Dropping it is the
    recoverable direction: an old-version run that still wants it rebuilds it."""
    assert _templates_to_drop([OWN, OTHER], OWN, {}, now=NOW) == [OTHER]


def test_a_marker_from_the_future_keeps_its_template():
    """Two runs on one machine need not agree on the clock; a stamp ahead of
    ours is a fresh template, not a negative age to act on."""
    assert _templates_to_drop([OWN, OTHER], OWN, {OTHER: NOW + 60}, now=NOW) == []


def test_a_name_that_is_not_a_template_is_left_alone():
    """The rule decides, so a name the caller's query let through — another
    run's working database, say — can never be dropped by it."""
    existing = ["cheesex_test_gw0", "cheesex_test_gw0_c", OTHER]
    assert _templates_to_drop(existing, OWN, {}, now=NOW) == [OTHER]
