"""How many of one project's turns may run at once.

Putting this in `config.py` would carry it past the size cap every module is
held to (`.claude/scripts/check-file-sizes.py`,
`.claude/rules/architecture.md`), and the rule stands alone anyway: it reads one
project setting and falls back to the deployment's own number,
`settings.max_concurrent_turns`.

The same answer has three readers, so it is written once. `work_policy` admits a
turn against it; `GET /projects/{project_id}/compute-configs` reports it along
with which of the two numbers it is; and a person or an agent moves it through
`PUT` of the same path, which writes ``project.settings["max_concurrent_turns"]``
as a member setting. A project's own number is therefore reachable three ways
and correct in each — before this module, `work_policy` read the override inline
and nothing else could.
"""

from collections.abc import Mapping

from app.core.config import settings

#: What a project's ceiling may be: what a person may set it to, and what a
#: stored one is read back as. The top is a bound on a number and on a typo,
#: not a measurement of what the pool holds — `max_concurrent_turns` is the only
#: gate a turn passes before one machine is asked to run it, so a project asking
#: for hundreds at once is refused where it can read why.
TURN_CEILING_MIN = 1
TURN_CEILING_MAX = 64


def project_max_concurrent_turns(
    project_settings: Mapping[str, object] | None,
) -> int | None:
    """The ceiling one project set for itself, or None when it runs at the
    deployment default. A stored value outside the range above is no ceiling at
    all — the reading `work_policy` has always had, kept here so that what is
    read back is what is enforced. A JSON ``true`` counts as outside it: it is
    an ``int`` in Python, and reading it as one throttles the project to a
    single turn at a time."""
    override = (project_settings or {}).get("max_concurrent_turns")
    if isinstance(override, bool) or not isinstance(override, int):
        return None
    return override if TURN_CEILING_MIN <= override <= TURN_CEILING_MAX else None


def max_concurrent_turns_for(project_settings: Mapping[str, object] | None) -> int:
    """The ceiling one project's turns are admitted under: its own when it has
    one, else this deployment's."""
    own = project_max_concurrent_turns(project_settings)
    return settings.max_concurrent_turns if own is None else own
