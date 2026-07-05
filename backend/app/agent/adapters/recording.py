"""RecordingAgent: an in-memory Agent adapter that records delivered text.

The trivial reference implementation of the Agent contract — no real execution.
Useful as a dev/test default and for orchestrator tests, mirroring the codebase
convention of in-memory default impls behind a Protocol.
"""

from app.agent.interfaces import AgentContext


class RecordingSession:
    def __init__(self, session_id: str, context: AgentContext) -> None:
        self._id = session_id
        self.context = context
        self.received: list[str] = []
        self._alive = True

    @property
    def session_id(self) -> str:
        return self._id

    async def send(self, text: str) -> None:
        if not self._alive:
            raise RuntimeError(f"session {self._id} is stopped")
        self.received.append(text)

    async def stop(self) -> None:
        self._alive = False

    async def is_alive(self) -> bool:
        return self._alive


class RecordingAgent:
    def __init__(self) -> None:
        self.sessions: list[RecordingSession] = []

    async def start(self, context: AgentContext) -> RecordingSession:
        session = RecordingSession(f"rec-{len(self.sessions) + 1}", context)
        self.sessions.append(session)
        return session
