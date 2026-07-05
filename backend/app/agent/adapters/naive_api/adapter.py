"""NaiveApiAgent: the machine-less Agent adapter (direct LLM).

``send(text)`` runs one bounded agentic loop: feed the conversation + tool
schema to the model, execute any tool calls it emits (via the injected
executor), feed results back, and repeat until the model stops calling tools or
a step cap is hit. Output reaches the platform only through those tool calls —
the loop never parses the model's prose as commands.
"""

from typing import Any

from app.agent.adapters.naive_api.types import ChatModel, ToolExecutor
from app.agent.authorization.authorizer import ProjectActor
from app.agent.interfaces import AgentContext

_DEFAULT_MAX_STEPS = 8


class NaiveApiSession:
    def __init__(
        self,
        session_id: str,
        actor: ProjectActor,
        model: ChatModel,
        tools: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        max_steps: int,
    ) -> None:
        self._id = session_id
        self._actor = actor
        self._model = model
        self._tools = tools
        self._execute_tool = execute_tool
        self._max_steps = max_steps
        self._alive = True
        self.messages: list[dict[str, Any]] = []

    @property
    def session_id(self) -> str:
        return self._id

    async def send(self, text: str) -> None:
        if not self._alive:
            raise RuntimeError(f"session {self._id} is stopped")
        self.messages.append({"role": "user", "content": text})
        for _ in range(self._max_steps):
            response = await self._model.respond(self.messages, self._tools)
            if response.text:
                self.messages.append({"role": "assistant", "content": response.text})
            if not response.tool_calls:
                return
            for call in response.tool_calls:
                try:
                    result = await self._execute_tool(self._actor, call.name, call.args)
                    content = str(result)
                except Exception as exc:  # tool errors are fed back, not fatal
                    content = f"error: {exc}"
                self.messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": content}
                )
        # Step cap reached with the model still calling tools — stop the turn
        # rather than loop forever; the record shows the truncation.
        self.messages.append(
            {"role": "system", "content": f"[stopped after {self._max_steps} tool steps]"}
        )

    async def stop(self) -> None:
        self._alive = False

    async def is_alive(self) -> bool:
        return self._alive


class NaiveApiAgent:
    def __init__(
        self,
        model: ChatModel,
        tools: list[dict[str, Any]],
        execute_tool: ToolExecutor,
        max_steps: int = _DEFAULT_MAX_STEPS,
    ) -> None:
        self._model = model
        self._tools = tools
        self._execute_tool = execute_tool
        self._max_steps = max_steps
        self._count = 0

    async def start(self, context: AgentContext) -> NaiveApiSession:
        self._count += 1
        session = NaiveApiSession(
            session_id=f"naive-{self._count}",
            actor=context.actor,
            model=self._model,
            tools=self._tools,
            execute_tool=self._execute_tool,
            max_steps=self._max_steps,
        )
        if context.role_prompt:
            session.messages.append({"role": "system", "content": context.role_prompt})
        return session
