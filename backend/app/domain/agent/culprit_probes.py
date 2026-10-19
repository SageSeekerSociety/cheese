"""Run culprit probes through a project's recorded execution capability."""

import base64
import json
from pathlib import Path
import shlex
import uuid

from sqlalchemy import select

from app.domain.agent import execution
from app.domain.agent_session.models import AgentSession
from app.domain.conversation.models import Conversation
from app.domain.review.landing_forge import ProbeVerdict


class PlatformProbes:
    def __init__(self, sessions, call=execution.call):
        self.sessions = sessions
        self.call = call

    async def _target(self, project_id, session_id=None):
        async with self.sessions() as db:
            query = (
                select(AgentSession)
                .join(Conversation, Conversation.id == AgentSession.conversation_id)
                .where(
                    Conversation.project_id == project_id,
                    AgentSession.work_lease.is_not(None),
                )
            )
            if session_id is not None:
                query = query.where(AgentSession.id == session_id)
            row = await db.scalar(
                query.order_by(AgentSession.created_at.desc(), AgentSession.id).limit(1)
            )
            if (
                row is None
                or not row.work_lease
                or row.work_lease.get("status", "ready") != "ready"
            ):
                return None
            return row.id, dict(row.work_lease)

    async def start(self, project_id, sha, tests, nonce) -> str | None:
        target = await self._target(project_id)
        if target is None:
            return None
        session_id, lease = target
        source = Path(__file__).with_name("culprit_probe_script.py").read_bytes()
        run_id = f"{session_id.hex}:{nonce}"
        result = await self.call(
            lease,
            "control",
            {
                "subtype": "shell",
                "operation": "start",
                "command_id": "culprit-" + nonce,
                "kind": "sh",
                "body": shlex.join(["python3", "-", sha, json.dumps(tests), nonce]),
                "stdin": base64.b64encode(source).decode(),
            },
        )
        return run_id if result.get("started") else None

    async def verdict(self, project_id, run_id) -> ProbeVerdict:
        session, nonce = run_id.split(":", 1)
        target = await self._target(project_id, uuid.UUID(hex=session))
        if target is None:
            return "unknown"
        _, lease = target
        result = await self.call(
            lease,
            "control",
            {
                "subtype": "shell",
                "operation": "read",
                "command_id": "culprit-" + nonce,
                "out": 0,
                "err": 0,
                "wait": 0,
            },
        )
        if result.get("lost"):
            return "unknown"
        if "exit" not in result:
            return "running"
        if result["exit"] == 0:
            return "pass"
        if result["exit"] == 1:
            return "fail"
        return "unknown"
