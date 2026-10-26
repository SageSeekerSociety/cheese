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
    # channel; changing who is in one is its own tool.
    "cheese_members": "cheese_room.members",
    "cheese_title": "cheese_room.rename",
    "cheese_channel_create": "cheese_room.create",
    "cheese_member_set": "cheese_people",
    # Everything else the room and a person's 芝士 do: routines, feedback,
    # skills, the session itself, the web, the platform's help, the library,
    # notifications, memory, the inbox, settings and problems.
    "cheese_routine_draft": "cheese_routine.create",
    "cheese_routine_list": "cheese_routine.list",
    "cheese_routine_update": "cheese_routine.update",
    "cheese_routine_pause": "cheese_routine.pause",
    "cheese_routine_report": "cheese_routine.report",
    "cheese_feedback_propose": "cheese_feedback.propose",
    "cheese_feedback_list": "cheese_feedback.list",
    "cheese_feedback_get": "cheese_feedback.get",
    "cheese_feedback_claim": "cheese_feedback.claim",
    "cheese_feedback_release": "cheese_feedback.release",
    "skills_search": "cheese_skill.search",
    "skills_read": "cheese_skill.read",
    "cheese_skill_draft": "cheese_skill.propose",
    "cheese_skill_update": "cheese_skill.update",
    "cheese_status": "cheese_session.status",
    "cheese_environment": "cheese_session.environment",
    "cheese_wait_environment": "cheese_session.wait",
    "cheese_compact": "cheese_session.compact",
    "cheese_note": "cheese_session.note",
    "cheese_deliver_at": "cheese_session.deliver_at",
    "cheese_library_ls": "cheese_library.list",
    "cheese_fetch": "cheese_web.fetch",
    "cheese_web_search": "cheese_web.search",
    "cheese_web_fetch": "cheese_web.fetch",
    "cheese_inbox_respond": "cheese_inbox.respond",
    "cheese_problem_write": "cheese_problem.write",
    "cheese_problem_publish": "cheese_problem.publish",
    "cheese_attachment_read": "cheese_library.read",
    "cheese_file_give": "cheese_library.give",
    "cheese_file_to_computer": "cheese_library.take",
    "cheese_memory_read": "cheese_memory.project",
    "cheese_docs_search": "cheese_help",
    "cheese_docs_read": "cheese_help",
    "cheese_settings_set": "cheese_settings",
    "cheese_notify_me": "cheese_notify",
}


def tool_key(name: str, args: object) -> str:
    """``cheese_task.close`` for a call that closed a task, whichever name and
    shape it was made under; the name itself for a tool without verbs."""
    action = args.get("action") if isinstance(args, dict) else None
    if isinstance(action, str) and action:
        return f"{name}.{action}"
    return RETIRED.get(name, name)
