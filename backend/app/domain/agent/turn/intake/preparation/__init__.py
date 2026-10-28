"""Real turn intake: recipient and prompt preparation, without a service host.

**This package is `preparation`'s facade.** One module held all of it until
2026-10-16; the bodies moved into the sibling modules of this package, cut by
what each one is about:

  * `launch` — what a seat's session would be started with, read without
    opening one, and which harness it would run on;
  * `assembly` — `prepare`: the room, the recipient, memory and the machine,
    read in one transaction and handed on as `TurnContext`;
  * `prompt` — the text that turn is handed, and the replay count over it;
  * `gates` — the two things that stop a turn between resolving its machine and
    occupying a model: nothing runs here, or the tier policy proposes instead;
  * `streaming` — `converse`: the half that starts a session and streams it.

What stays in `__init__.py` is what the tree reaches by *this* path: the
`TurnPreparation` class, with one-line forwarders to the bodies above, and the
names re-exported from `context` (see the note there) so a caller that already
named `…intake.preparation` for one of them still resolves. `held_blocks` is
re-exported here as well, and is the one name a test patches on this module
object (backend/tests/integration/native_deferred_message.py); `_held_blocks`
forwards to it so `assembly.prepare` reaches the name through the object
instead of importing this module back — the facade is the one place a
monkeypatch on it is seen, and a submodule importing its own facade at runtime
is the cycle this shape exists to avoid.

A package rather than five flat siblings: these bodies call into each other, and
a child of this package importing its own package is not one of the pairs C3
(`sibling domains under app.domain are acyclic`) counts — the same shape
`device_hub` and `feedback/services` took. Nothing outside this package changed:
no caller, no import path, no test.
"""

import logging
import uuid
from collections.abc import AsyncIterator, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import BaseError
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.turn.intake.context import (
    _FIT_NOTE_CHARS,  # noqa: F401  (imported only to be re-exported)
    Launch,
    ModelOptions,
    TurnBail,
    TurnContext,
    _Machines,
    _MemoryBooks,
    _proposal_frames,  # noqa: F401  (re-exported)
    carried_skills,  # noqa: F401  (re-exported; the branch's test imports it here)
    human_private_owner,  # noqa: F401  (re-exported)
    living_doc_text,  # noqa: F401  (re-exported; read by tests)
)
from app.domain.agent.turn.intake.preparation import assembly, launch, streaming
from app.domain.agent.turn.state.live import LiveWork
from app.domain.agent.turn.steps.send import SendEffects

# Re-exported so a name the tree reaches through this module keeps resolving,
# and as the module global `_held_blocks` below reads at call time: the one test
# double for it is set here (native_deferred_message.py).
from app.domain.delivery.receipts import held_blocks

logger = logging.getLogger(__name__)


class TurnPreparation:
    """Assemble room, recipient, memory and model facts in one transaction."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        compute: _Machines,
        memory: _MemoryBooks,
        base_prompt: str,
        skills: str,
        model_options: ModelOptions,
        refuse: Callable[[str], BaseError],
        live: LiveWork,
        send_effects: SendEffects,
    ) -> None:
        self.sessions = sessions
        self.compute = compute
        self.memory = memory
        self.base_prompt = base_prompt
        self.skills = skills
        self.model_options = model_options
        self.refuse = refuse
        self.live = live
        self.send_effects = send_effects

    async def _backend_for(
        self, session, agent, project
    ) -> tuple[str, RoomSessions | None]:
        return await launch.backend_for(self, session, agent, project)

    async def _held_blocks(self, session, **kwargs):
        """Read the held-block ids through this module's `held_blocks`, at call
        time. `assembly` gets them here rather than importing this module back:
        the facade is the one place a replacement for it is seen, and a
        submodule importing its own facade at runtime is a cycle."""
        return await held_blocks(session, **kwargs)

    async def launch_inputs(
        self, topic_id: uuid.UUID, agent_handle: str, *, acting: str
    ) -> Launch | None:
        return await launch.launch_inputs(self, topic_id, agent_handle, acting=acting)

    async def prepare(
        self,
        *,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID,
        user_block_id: uuid.UUID | None,
        platform_turn: bool = False,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> "TurnContext | TurnBail":
        return await assembly.prepare(
            self,
            topic_id=topic_id,
            content=content,
            turn_id=turn_id,
            user_block_id=user_block_id,
            platform_turn=platform_turn,
            delivery_id=delivery_id,
            recipient_instance_id=recipient_instance_id,
        )

    async def converse(
        self,
        *,
        topic_id: uuid.UUID,
        content: str,
        turn_id: uuid.UUID,
        author: str,
        user_block_id: uuid.UUID | None,
        is_resume: bool = False,
        continuation_id: uuid.UUID | None = None,
        platform_turn: bool = False,
        delivery_id: uuid.UUID | None = None,
        recipient_instance_id: uuid.UUID | None = None,
    ) -> AsyncIterator[dict]:
        async for frame in streaming.converse(
            self,
            topic_id=topic_id,
            content=content,
            turn_id=turn_id,
            author=author,
            user_block_id=user_block_id,
            is_resume=is_resume,
            continuation_id=continuation_id,
            platform_turn=platform_turn,
            delivery_id=delivery_id,
            recipient_instance_id=recipient_instance_id,
        ):
            yield frame
