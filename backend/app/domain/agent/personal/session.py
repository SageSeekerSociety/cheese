"""A conversation's session on the session host: one pi session per
conversation, and a sandbox of the conversation's own for its hands.

What a room needs and a person's 芝士 does not is absent rather than idle: no
project, no topic, no screen, no room log. Its platform tools act as the person
through the platform's own routes. Its own tools — reading and writing files,
running commands — work in a standard sandbox the conversation takes the first
time one of them is used and not before (``machine.holder.Personal``): a
question that runs nothing never waits for one, and never pays for one. The
sandbox holds no credential of the platform's, keeps nothing once it is idle,
and is paid for from the person's own credits.

One conversation is one session, at a home of its own under the person's, so a
person's sessions are counted together: a person keeps at most
``SESSIONS_PER_PERSON`` running, starting one more letting their least
recently used one go. A session sits idle for ``IDLE_EXIT_S`` and then exits;
its conversation stays on disk, and the next question starts it again on it.
"""

import uuid

from app.core import model_roles
from app.core.config import settings
from app.core.sandbox_auth import mint_personal_credential
from app.domain.agent import gateway_catalog
from app.domain.agent.executor_transport import DEFERRED_WORKSPACE
from app.domain.agent.harness import PI
from app.domain.agent.market import cloud_provisionable
from app.domain.agent.personal.prompt import system_prompt
from app.domain.agent.session_host.contract import (
    Access,
    Footprint,
    ModelSettings,
    SessionRef,
    SessionSpec,
)

#: How long a person's session may sit idle before it exits.
IDLE_EXIT_S = 180.0
#: How long a question waits for the session host to have memory for its
#: session. Sessions that finish answering exit within a few minutes, which
#: is what frees the memory; a host still full after that is overloaded.
HOST_WAIT_S = 180.0
#: How many of one person's sessions may run at once.
SESSIONS_PER_PERSON = 2
#: The memory each may use, pi and runner together.
MEMORY_MB = 512
#: How long its runner may be out of reach mid-answer before the answer is
#: given up on.
GONE_AFTER_S = 15.0
#: The longest answer, in tokens; the model's other limits follow from it. Its
#: reasoning counts, and so do a tool call's arguments: a 题目's whole
#: description written into a draft is one answer.
MAX_TOKENS = 16000
#: How much of the context compaction keeps free, and how much of the latest
#: conversation it keeps verbatim: pi's own defaults.
RESERVE_TOKENS = 16_000
KEEP_TOKENS = 20_000
#: The window assumed for a model the gateway declares none for: the one
#: Claude Code assumes for a model it does not know.
ASSUMED_WINDOW = 200_000

#: How long pi waits before retrying a failed model call, doubling for each of
#: its three retries: pi's own default, written here so that a test can make a
#: failing gateway fail at once.
RETRY_BASE_DELAY_MS = 2000

#: How hard it reasons, when the model honours it (the gateway's
#: ``cheese_efforts``): writing a 题目 from a handout is worth the deeper one.
EFFORT = "high"


def context_tokens(model: str) -> int:
    """The window pi is told ``model`` has. pi summarises the older part of the
    conversation once a question's context passes it less ``RESERVE_TOKENS``:
    where the model's provider recommends compacting
    (``GatewayModel.compact_window``), else short of the window the gateway
    declares, else of ``ASSUMED_WINDOW``. Sessions on Claude Code compact by
    the same rule (``session_launch.compact_windows``)."""
    known = next((m for m in gateway_catalog.snapshot() if m.id == model), None)
    window = (known and known.context_window) or ASSUMED_WINDOW
    recommended = known and known.compact_window
    return min(window, recommended + RESERVE_TOKENS) if recommended else window


def effort(model: str) -> str | None:
    """``EFFORT`` when ``model`` honours it, else the model's own default."""
    honoured = next(
        (m.efforts for m in gateway_catalog.snapshot() if m.id == model), ()
    )
    return EFFORT if EFFORT in honoured else None


#: What a person's 芝士 may do (`sandbox/cheese`), the same wherever it is asked
#: and for everyone, in this order: the list is part of every request's
#: unchanging start, which the model's cache matches on. One tool per kind of
#: thing the person deals with; reading and searching are one each, since
#: everything reads the same way by its ref (`assistant.refs`). What others see
#: goes through its gate (`assistant.gate`). The page the person has open is
#: said with each question (``prompt.page``); it never narrows the tools.
#: Whether a person's 芝士 may hand parts of its work to subagents (分身). Kept
#: only while the eval scores better with them than without
#: (``evals/assistant``, ``--no-subagents``).
BRANCHES = True

TOOLS = (
    "cheese_inbox",
    "cheese_read",
    "cheese_find",
    "cheese_help",
    "chat_send",
    "cheese_task",
    "cheese_card",
    "cheese_room",
    "cheese_people",
    "cheese_space",
    "cheese_settings",
    "cheese_routine",
    "cheese_notify",
    "cheese_todo",
    # 记忆是用户的：记下、读、删掉。项目共享记忆是文档芝士读的。
    "cheese_memory.save",
    "cheese_memory.read",
    "cheese_memory.forget",
    # 资料：在电脑和用户的资料库之间交文件；读附件是文档芝士的。
    "cheese_library.give",
    "cheese_library.take",
    "cheese_web",
    "cheese_problem",
)


def ref(user_id: int, conversation_id: uuid.UUID) -> SessionRef:
    """The conversation's session; one home per person, so their sessions are
    siblings."""
    return SessionRef(PI, f"personal/{user_id}/{conversation_id}")


def target(conversation_id: uuid.UUID) -> dict | None:
    """The sandbox the conversation's session works on, taken when a tool first
    needs it (``RemoteClient``): the conversation's own, which it asks for at
    the same path a room's session asks at. None where the deployment has no
    session host to name, and so no sandbox to give."""
    if not settings.agent_session_device_id or not cloud_provisionable(settings):
        return None
    conversation = str(conversation_id)
    return {
        "kind": "deferred",
        # The topic's session is named by the topic (``holder.personal_claims``).
        "resource_id": conversation,
        "session_id": conversation,
        "lease_path": f"/topics/{conversation}/sessions/{conversation}/work-lease",
        "setup_env": {},
        "workspace": DEFERRED_WORKSPACE,
        "mcp_servers": [],
    }


def session(
    user_id: int, conversation_id: uuid.UUID
) -> tuple[SessionRef, SessionSpec, Access]:
    """The conversation's session, what it is started as, and what it acts
    with: the person's own credential, which reaches the model and its own
    sandbox and nothing else (`sandbox_auth.mint_personal_credential`)."""
    machine = target(conversation_id)
    spec = SessionSpec(
        system_prompt=system_prompt(computer=machine is not None),
        model=model_roles.model("assistant_model"),
        tools=TOOLS,
        works=machine is not None,
        branches=BRANCHES,
        footprint=Footprint(memory_mb=MEMORY_MB, group_limit=SESSIONS_PER_PERSON),
        idle_exit_s=IDLE_EXIT_S,
        gone_after_s=GONE_AFTER_S,
        host_wait_s=HOST_WAIT_S,
        model_settings=ModelSettings(
            # It reasons: reading a handout and writing a 题目 from it needs it.
            thinking=True,
            effort=effort(model_roles.model("assistant_model")),
            max_tokens=MAX_TOKENS,
            context_tokens=context_tokens(model_roles.model("assistant_model")),
            reserve_tokens=RESERVE_TOKENS,
            keep_tokens=KEEP_TOKENS,
            retry_base_delay_ms=RETRY_BASE_DELAY_MS,
        ),
        resume_token=str(conversation_id),
    )
    access = Access(
        mint_personal_credential(user_id=user_id, conversation_id=str(conversation_id)),
        target=machine,
    )
    return ref(user_id, conversation_id), spec, access
