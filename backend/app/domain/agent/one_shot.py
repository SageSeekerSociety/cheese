"""One question put to the project's own model, with no session around it.

The platform writes a few short texts on a project's behalf — the project's
「现状」 (`project_situation.writer`), the 说明 of a person's hand-in
(`agent.hand_in_draft`). Each is one call: a system prompt, the material, and
one answer back. It runs on the project's model for the scene it stands in
(`model_catalog.pick`), on the project's key on the gateway, so it is priced,
admitted and charged as a turn in the project is (`docs/manual/dev/gateway.md`).
A project whose model does not go through the gateway (a member's own
subscription) is not asked: no other model is put in its place.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.document import question
from app.domain.agent.model_catalog import Scene, catalog, pick
from app.domain.agent.supply import GATEWAY
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.identity.handles import agent_instance_handle
from app.domain.project.services import ProjectService
from app.domain.service_keys import gateway_base

logger = logging.getLogger(__name__)


class ProjectModels(Protocol):
    """The project's key on the gateway, and how what it spent is charged
    (`ChatService` is one)."""

    async def project_gateway_key(self, project_id: uuid.UUID) -> str | None: ...

    async def charge_turn_spend(
        self, project_id: uuid.UUID, topic_id: uuid.UUID | None, turn_id: uuid.UUID
    ) -> object: ...


@dataclass(frozen=True)
class Answer:
    content: str
    model: str


async def bind(
    session: AsyncSession, project_id: uuid.UUID, scene: Scene
) -> question.Bound | None:
    """The model the project runs ``scene`` on, admitted as a turn there would
    be; None when it does not go through the gateway. A refusal by the
    project's quota or model tier raises, as `question.admit` does."""
    project = await ProjectService(session).get_or_404(project_id)
    picked = pick(scene, catalog(project.settings), project_settings=project.settings)
    chosen = picked.binding
    if chosen.supply != GATEWAY:
        return None
    agent = await AgentInstanceService(session).for_project(project)
    bound = question.Bound(
        agent_handle=agent_instance_handle(agent.instance_id),
        agent_name=agent.display_name or "",
        model=chosen.model,
        wire_model=chosen.wire_model,
        supply=chosen.supply,
        automatic=picked.automatic,
    )
    await question.admit(session, project_id, bound)
    return bound


async def ask(
    *,
    project_id: uuid.UUID,
    bound: question.Bound,
    system: str,
    material: str,
    models: ProjectModels,
    timeout: float,
    max_tokens: int,
    attempts: int = 1,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Answer | None:
    """The model's answer, or None: no key, a failed call, or an empty answer
    every time it was asked. ``attempts`` asks again after an empty answer
    (a reasoning model can spend its whole budget thinking). What the calls
    spent is charged to the project whichever way it went."""
    key = await models.project_gateway_key(project_id)
    if not key:
        return None
    content = ""
    asked = False
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout), transport=transport
        ) as client:
            for _ in range(attempts):
                asked = True
                r = await client.post(
                    f"{gateway_base()}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": bound.wire_model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": material},
                        ],
                        "max_tokens": max_tokens,
                    },
                )
                r.raise_for_status()
                message = r.json()["choices"][0]["message"]
                content = (message.get("content") or "").strip()
                if content:
                    break
    except Exception:  # the caller has its own answer without one
        logger.warning("one-shot call failed project=%s", project_id, exc_info=True)
        content = ""
    if asked:
        # Spent on the project's key: settled from the gateway's records, as a
        # turn in the project is.
        await models.charge_turn_spend(project_id, None, uuid.uuid4())
    if not content:
        return None
    return Answer(content=content, model=bound.model)
