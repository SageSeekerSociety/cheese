"""Which action a recorded platform tool call is, under every name it was made by.

A tool for one object takes its verb as ``action`` (``sandbox/cheese``,
``_object``), so its name alone no longer says what a call did. A session
record keeps every call as it was made, so what reads one back — the line 现场
shows for it, the argument that line quotes — reads it by key:
``<tool>.<action>`` for one of an object's verbs, the bare name for a tool that
does one thing.

``RETIRED`` is the names that were folded into one of an object's verbs, and
the key each became. It describes calls already written down, so it stays: a
name that is still on the table but now takes an ``action`` is listed too, for
the calls made before it did. A tool removed outright, with nothing it became,
is not here: its old records keep their own label. The frontend's copy is
``RETIRED`` in ``frontend/src/lib/toolLabels.ts``, held to this one by
``tests/unit/test_tool_labels.py``.
"""

#: Retired name → the key it became.
RETIRED: dict[str, str] = {
    # The task itself: the room's verbs, and a person's 芝士 opening one.
    "cheese_task": "cheese_task.open",
    "cheese_task_state": "cheese_task.state",
    "cheese_close_task": "cheese_task.close",
    "cheese_task_open": "cheese_task.open",
    # Its review card: filed and kept up by the task's agent, decided by a
    # person's 芝士 as the reviewer.
    "cheese_accept_request": "cheese_card.request",
    "cheese_describe": "cheese_card.describe",
    "cheese_redeliver": "cheese_card.redeliver",
    "cheese_ready": "cheese_card.ready",
    "cheese_review_decide": "cheese_card.decide",
}


def tool_key(name: str, args: object) -> str:
    """``cheese_task.close`` for a call that closed a task, whichever name and
    shape it was made under; the name itself for a tool without verbs."""
    action = args.get("action") if isinstance(args, dict) else None
    if isinstance(action, str) and action:
        return f"{name}.{action}"
    return RETIRED.get(name, name)
