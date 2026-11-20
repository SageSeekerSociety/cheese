"""A document conversation's session on the session host: one pi session per
comment thread, or per selection box.

Its tools read and edit the document and look things up in the project as the
person asking. For a task's document whose machine is there, the session reads
the task's work on it besides (`document/machine.py`): pi's own read, ls, find
and grep, with their hands on the task's checkout, and nothing that writes or
runs a command. It never takes a machine of its own.

One conversation is one session, at a home under its project's, so a
project's sessions are counted together: starting one more than
``SESSIONS_PER_PROJECT`` lets the project's least recently used idle one go. A
session sits idle for ``IDLE_EXIT_S`` and exits; the conversation's next
question starts it again on the same conversation.

It runs on the agent's model, called with a credential naming the document's
task (its project, for a document of no task), the agent and the conversation, so
what it spends is the project's like any call of that agent (`llm_proxy`).
"""

import uuid

from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent.document.question import (
    HOST_WAIT_S,
    TOKEN_TTL_S,
    Asked,
    Bound,
    Surroundings,
    system_prompt,
)
from app.domain.agent.harness import PI
from app.domain.agent.session_host.contract import (
    Access,
    Footprint,
    SessionRef,
    SessionSpec,
)

#: How long a conversation's session may sit idle before it exits.
IDLE_EXIT_S = 60.0
#: How many of one project's sessions may be kept running at once.
SESSIONS_PER_PROJECT = 4
#: The memory each may use, pi and runner together.
MEMORY_MB = 512
#: How long its runner may be out of reach mid-answer before the answer is
#: given up on.
GONE_AFTER_S = 15.0

#: What a document's 芝士 may do (`sandbox/cheese`): read and edit the
#: document and its mini-apps, and look things up in the project as the person asking.
TOOLS = (
    # 文档只读和改：新建、列出资料库是房间里的事，这里不给（`catalog.schemas_of`
    # 按这几个动词给出 cheese_doc）。
    "cheese_doc.get",
    "cheese_doc.edit",
    # 整篇写：只给空着的文档写第一版（起草项目总览）；写过的文档平台会拒，
    # 只能一处一处地改（`living_docs.replace_document`）。
    "cheese_doc.set",
    "cheese_app",
    "cheese_project_search",
    "cheese_memory.project",
    "cheese_library.read",
)


def ref(project_id: uuid.UUID, key: uuid.UUID) -> SessionRef:
    """Conversation ``key``'s session; one home per project, so a project's
    sessions are siblings."""
    return SessionRef(PI, f"docs/{project_id}/{key}")


def session_for(
    *,
    asked: Asked,
    key: uuid.UUID,
    bound: Bound,
    around: Surroundings,
    where: str,
) -> tuple[SessionRef, SessionSpec, Access]:
    """Conversation ``key``'s session, on the agent's model, with a credential
    for that conversation; its tools act on the document or the room file.

    A file's question names its room in ``CHEESE_TOPIC``: that is what the
    tools reading a room's file — `cheese_library(action=read)` — address it by
    (`document` names the document instead, and a file's question has none)."""
    env = {"CHEESE_PROJECT": str(asked.project_id)}
    if asked.document_id is not None:
        env["CHEESE_DOCUMENT"] = str(asked.document_id)
    if asked.task_id is not None:
        env["CHEESE_TOPIC"] = str(asked.task_id)
    elif asked.room_id is not None:
        env["CHEESE_TOPIC"] = str(asked.room_id)
    spec = SessionSpec(
        system_prompt=system_prompt(
            bound.agent_name,
            around.goals,
            around.memory,
            where=where,
            workspace=(around.machine or {}).get("workspace"),
        ),
        model=bound.wire_model,
        tools=TOOLS,
        footprint=Footprint(memory_mb=MEMORY_MB, group_limit=SESSIONS_PER_PROJECT),
        idle_exit_s=IDLE_EXIT_S,
        gone_after_s=GONE_AFTER_S,
        host_wait_s=HOST_WAIT_S,
        resume_token=str(key),
        env=env,
    )
    access = Access(
        mint_scoped_token(
            project_id=str(asked.project_id),
            topic_id=str(asked.task_id or asked.room_id or "") or None,
            agent_handle=bound.agent_handle,
            resource_id=str(key),
            document_id=str(asked.document_id) if asked.document_id else None,
            ttl_s=TOKEN_TTL_S,
        ),
        around.machine,
    )
    return ref(asked.project_id, key), spec, access
