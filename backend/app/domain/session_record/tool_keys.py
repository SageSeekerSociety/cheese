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
    "cheese_reopen_task": "cheese_task.reopen",
    "cheese_task_open": "cheese_task.open",
    # Its review card: filed and kept up by the task's agent, decided by a
    # person's 芝士 as the reviewer.
    "cheese_accept_request": "cheese_card.request",
    "cheese_describe": "cheese_card.describe",
    "cheese_redeliver": "cheese_card.redeliver",
    "cheese_ready": "cheese_card.ready",
    "cheese_review_decide": "cheese_card.decide",
    # The conversation: reading it, and editing your own message in it.
    "cheese_chat_list": "cheese_chat.list",
    "cheese_chat_search": "cheese_chat.search",
    "cheese_chat_get": "cheese_chat.get",
    "cheese_chat_replies": "cheese_chat.replies",
    "chat_edit": "cheese_chat.edit",
    # A person's 芝士 sending as the person is the same chat_send.
    "cheese_message_send": "chat_send",
    # Documents, the mini-apps in them, and their canvases.
    "cheese_doc_get": "cheese_doc.get",
    "cheese_doc_set": "cheese_doc.set",
    "cheese_doc_edit": "cheese_doc.edit",
    "cheese_doc_new": "cheese_doc.new",
    "cheese_doc_list": "cheese_doc.list",
    "cheese_app_save": "cheese_app.save",
    "cheese_app_get": "cheese_app.get",
    "cheese_canvas_get": "cheese_canvas.get",
    "cheese_canvas_edit": "cheese_canvas.edit",
    # The room itself: its roster and its name, and a person's 芝士 making a
    # channel or changing who is in one.
    "cheese_members": "cheese_room.members",
    "cheese_title": "cheese_room.rename",
    "cheese_channel_create": "cheese_room.create",
    "cheese_member_set": "cheese_room.member",
}


def tool_key(name: str, args: object) -> str:
    """``cheese_task.close`` for a call that closed a task, whichever name and
    shape it was made under; the name itself for a tool without verbs."""
    action = args.get("action") if isinstance(args, dict) else None
    if isinstance(action, str) and action:
        return f"{name}.{action}"
    return RETIRED.get(name, name)
