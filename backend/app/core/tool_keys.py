"""Which action a platform tool call is, under every name it was ever made by.

A tool for one object takes its verb as ``action`` (``sandbox/cheese``,
``_object``), so its name alone no longer says what a call did. Whatever reads
a call back — the line 现场 shows for it, the argument that line quotes — reads
it by key: ``<tool>.<action>`` for one of an object's verbs, the bare name for
a tool that does one thing.

``RETIRED`` is the names the platform's tool table no longer has, and the
action each became. It describes calls that already exist, so it is permanent:
a session record keeps every call as it was made, and a Codex thread keeps the
tool list it was started with for good — ``thread/resume`` takes none, and
Codex refuses a name that is not on its list before the call reaches the
runner — so an old thread goes on calling the old names
(``harness/codex/tools.py`` reads them through ``as_called_now``). A name that
is still on the table but now takes an ``action`` is listed too, for the calls
made before it did.

Standard library only: the Codex runner archive ships this file. The
frontend's copy is ``RETIRED`` in ``frontend/src/lib/toolLabels.ts``, held to
this one by ``tests/unit/test_tool_labels.py``.
"""

#: Retired name → (the key it became, its parameters that were renamed, old →
#: new).
RETIRED: dict[str, tuple[str, dict[str, str]]] = {}


def _action(args: object) -> str | None:
    action = args.get("action") if isinstance(args, dict) else None
    return action if isinstance(action, str) and action else None


def tool_key(name: str, args: object) -> str:
    """``cheese_task.close`` for a call that closed a task, whichever name and
    shape it was made under; the name itself for a tool without verbs."""
    action = _action(args)
    if action is not None:
        return f"{name}.{action}"
    retired = RETIRED.get(name)
    return retired[0] if retired else name


def as_called_now(name: str, args: dict) -> tuple[str, dict]:
    """A call made under a retired name, as the same call is made today: the
    tool it became, with its ``action`` and its parameters' current names. A
    call already in today's shape comes back as it was."""
    retired = RETIRED.get(name)
    if retired is None or _action(args) is not None:
        return name, args
    key, renamed = retired
    tool, _, action = key.partition(".")
    now = {renamed.get(param, param): value for param, value in args.items()}
    if action:
        now["action"] = action
    return tool, now
