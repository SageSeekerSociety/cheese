"""A conversation's session on the session host: one pi session per
conversation, with no hands.

What a room needs and a person's 芝士 does not is absent rather than idle: no
project, no machine, no topic, no lease, no screen, no room log. The session is
told to use only the tools it is given, which act as the person through the
platform's own routes.

One conversation is one session, at a home of its own under the person's, so a
person's sessions are counted together: a person keeps at most
``SESSIONS_PER_PERSON`` running, starting one more letting their least
recently used one go. A session sits idle for ``IDLE_EXIT_S`` and then exits;
its conversation stays on disk, and the next question starts it again on it.
"""

import uuid

from app.core.config import settings
from app.core.sandbox_auth import mint_personal_credential
from app.domain.agent.harness import PI
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
#: The longest answer, in tokens; the model's other limits follow from it.
MAX_TOKENS = 1500
#: The context the session may grow to, and how much of it compaction keeps
#: free: pi summarises the older part of the conversation once a question's
#: context passes ``CONTEXT_TOKENS - RESERVE_TOKENS``, keeping the latest
#: ``KEEP_TOKENS`` verbatim. What a question costs stays bounded however long
#: the conversation runs.
CONTEXT_TOKENS = 32_000
RESERVE_TOKENS = 16_000
KEEP_TOKENS = 6_000

#: What a person's 芝士 may do (`sandbox/cheese`): read their own tasks and the
#: platform's documentation.
TOOLS = ("cheese_my_tasks", "cheese_docs_search", "cheese_docs_read")


def ref(user_id: int, conversation_id: uuid.UUID) -> SessionRef:
    """The conversation's session; one home per person, so their sessions are
    siblings."""
    return SessionRef(PI, f"personal/{user_id}/{conversation_id}")


def session(
    user_id: int, conversation_id: uuid.UUID, place: str
) -> tuple[SessionRef, SessionSpec, Access]:
    """The conversation's session, what it is started as, and what it acts
    with: the person's own credential, which reaches the model and nothing
    else (`sandbox_auth.mint_personal_credential`)."""
    spec = SessionSpec(
        system_prompt=system_prompt(place),
        model=settings.assistant_model,
        tools=TOOLS,
        footprint=Footprint(memory_mb=MEMORY_MB, group_limit=SESSIONS_PER_PERSON),
        idle_exit_s=IDLE_EXIT_S,
        gone_after_s=GONE_AFTER_S,
        host_wait_s=HOST_WAIT_S,
        model_settings=ModelSettings(
            thinking=False,
            max_tokens=MAX_TOKENS,
            context_tokens=CONTEXT_TOKENS,
            reserve_tokens=RESERVE_TOKENS,
            keep_tokens=KEEP_TOKENS,
        ),
        resume_token=str(conversation_id),
    )
    access = Access(
        mint_personal_credential(user_id=user_id, conversation_id=str(conversation_id))
    )
    return ref(user_id, conversation_id), spec, access
