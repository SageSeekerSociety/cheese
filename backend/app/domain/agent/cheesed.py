"""cheesed — the compute-node daemon (design v2 R2 / §5).

Runs on a compute node (our pool, a lab's self-hosted box, a competition machine).
The backend's RemoteCheesedProvider POSTs a turn here; cheesed runs `claude` in a
LOCAL container on this node and streams the AgentEvents back as NDJSON. The
container's platform actions (cheese) call back to the BACKEND over the network
(cheese_api in the request) authenticated by the per-turn scoped token the backend
minted — so the node never holds the signing secret, and a turn can run anywhere.

Node-local config (env):
  CHEESED_IMAGE      sandbox image (default cheesex-agent-sandbox:latest)
  CHEESED_SHIM       path to the claude-sbx shim on this node
  CHEESED_WORKSPACE  scratch workspace root on this node
Run: uvicorn app.domain.agent.cheesed:app --port 8100
"""

import os
import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.domain.agent.service import AgentService, event_to_dict

_IMAGE = os.environ.get("CHEESED_IMAGE", "cheesex-agent-sandbox:latest")
_SHIM = str(Path(os.environ.get("CHEESED_SHIM", "./sandbox/claude-sbx")).resolve())
# Absolute: SBX_WORKTREE is a docker bind-mount source — a relative path makes
# docker mount the wrong thing (the agent's writes then nest oddly).
_WORKSPACE = str(
    Path(os.environ.get("CHEESED_WORKSPACE", "./.cheesed-workspaces")).resolve()
)

_SANDBOX_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Grep", "Glob",
    "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
]

app = FastAPI(title="cheesed", version="0.1.0")


class RunTurn(BaseModel):
    project_id: str
    topic_id: str
    prompt: str
    system_prompt: str
    resume_session_id: str | None = None
    model: str
    env: dict[str, str]  # model-provider env (ANTHROPIC_*), from the AIPool
    cheese_api: str  # backend address the container calls back to
    cheese_token: str  # per-turn scoped token minted by the backend
    author: str = "cheese"
    turn_id: str | None = None
    memory_scope: str | None = None
    owner: str | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy", "image": _IMAGE}


@app.post("/run-turn")
async def run_turn(req: RunTurn) -> StreamingResponse:
    node_ws = Path(_WORKSPACE) / req.project_id / req.topic_id
    node_ws.mkdir(parents=True, exist_ok=True)
    session_dir = Path(_WORKSPACE) / req.project_id / f"{req.topic_id}.session"
    session_dir.mkdir(parents=True, exist_ok=True)

    env = {
        "SBX_IMAGE": _IMAGE,
        "SBX_CONTAINER": f"cheesed-sbx-{req.topic_id.replace('-', '')[:12]}",
        "SBX_WORKTREE": str(node_ws),
        "SBX_SESSION": str(session_dir),
        "CHEESE_API": req.cheese_api,
        "CHEESE_PROJECT": req.project_id,
        "CHEESE_TOPIC": req.topic_id,
        "CHEESE_AUTHOR": req.author,
        "CHEESE_TOKEN": req.cheese_token,
    }
    if req.turn_id:
        env["CHEESE_TURN"] = req.turn_id
    if req.memory_scope:
        env["CHEESE_MEMORY_SCOPE"] = req.memory_scope
    if req.owner:
        env["CHEESE_OWNER"] = req.owner
    sandbox = {"cli_path": _SHIM, "allowed_tools": _SANDBOX_TOOLS, "env": env}

    agent = AgentService(model=req.model, env=req.env)

    async def stream():
        import json

        async for event in agent.stream_reply(
            prompt=req.prompt,
            system_prompt=req.system_prompt,
            cwd=str(node_ws),
            resume_session_id=req.resume_session_id,
            sandbox=sandbox,
            model=req.model,
            env=req.env,
        ):
            yield json.dumps(event_to_dict(event)) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/files/{project_id}/{topic_id}")
async def list_files(project_id: str, topic_id: str) -> dict:
    """List the topic's node-local worktree files (R9 workspace read-back)."""
    tree = Path(_WORKSPACE) / project_id / topic_id
    files = []
    if tree.is_dir():
        for p in sorted(tree.rglob("*")):
            if p.is_dir() or ".git" in p.parts or ".jj" in p.parts:
                continue
            files.append({"path": str(p.relative_to(tree)), "bytes": p.stat().st_size})
    return {"data": files}


@app.get("/file/{project_id}/{topic_id}")
async def read_file(project_id: str, topic_id: str, path: str) -> dict:
    """Read one node-local worktree file. Path is confined to the worktree."""
    tree = (Path(_WORKSPACE) / project_id / topic_id).resolve()
    target = (tree / path).resolve()
    if not str(target).startswith(str(tree)) or not target.is_file():
        return {"data": None}
    return {"data": target.read_text(encoding="utf-8", errors="replace")}


@app.post("/teardown/{topic_id}")
async def teardown(topic_id: str) -> dict:
    """Best-effort: drop this topic's node-local scratch workspace."""
    for suffix in ("", ".session"):
        for base in Path(_WORKSPACE).glob(f"*/{topic_id}{suffix}"):
            shutil.rmtree(base, ignore_errors=True)
    return {"ok": True, "topic_id": topic_id}
