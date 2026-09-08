"""Claude's RC v2 transport, scoped to a Cheese place.

Redis holds sessions and both event journals so a backend restart does not lose
an outstanding permission request. Worker credentials grant access to one
session and epoch; they cannot call the human control API.
"""

import asyncio
import hashlib
import json
import time
import uuid
from typing import cast

import jwt
from redis.asyncio import Redis

from app.core.config import settings
from app.core.errors import AuthenticationRequiredError, ConflictError, NotFoundError
from app.core.redis import get_redis_client

CONTROLS = (
    "initialize",
    "interrupt",
    "background_tasks",
    "stop_task",
    "set_model",
    "set_permission_mode",
    "set_max_thinking_tokens",
    "apply_flag_settings",
    "rename_session",
    "set_color",
    "file_suggestions",
    "read_file",
    "get_workspace_diff",
    "get_context_usage",
    "get_usage",
    "mcp_status",
    "mcp_authenticate",
    "mcp_oauth_callback_url",
    "mcp_reconnect",
)
WORKER_TTL = 3600
RETENTION = 7 * 86400

_CHECK_EPOCH = """
local raw = redis.call('GET', KEYS[1])
if not raw then return false end
local session = cjson.decode(raw)
if session.status ~= 'active' or session.epoch ~= tonumber(ARGV[1]) then
    return false
end
"""


def key(sid: str, suffix: str = "session") -> str:
    return f"cheese:rc:{sid}:{suffix}"


def text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


def store() -> "RemoteControl":
    redis = get_redis_client()
    if redis is None:
        raise RuntimeError("Remote Control requires Redis")
    return RemoteControl(redis)


class RemoteControl:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def create(self, claims: dict, body: dict) -> dict:
        sid = "cse_" + uuid.uuid4().hex
        session = {
            "id": sid,
            "project_id": claims["p"],
            "topic_id": claims["t"],
            "expires_at": claims["exp"],
            "title": body.get("title", "Cheese"),
            "config": body.get("config", {}),
            "tags": body.get("tags", []),
            "status": "active",
            "environment_kind": "bridge",
            "epoch": 0,
            "created_at": time.time(),
            "last_seen": 0,
        }
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(key(sid), json.dumps(session), ex=RETENTION)
            pipe.set(key(claims["t"], "current"), sid, ex=RETENTION)
            await pipe.execute()
        return session

    async def get(self, sid: str) -> dict:
        raw = await self.redis.get(key(sid))
        if raw is None:
            raise NotFoundError("RC session not found")
        return json.loads(raw)

    async def current(self, topic_id: str) -> dict | None:
        sid = await self.redis.get(key(topic_id, "current"))
        return await self.get(text(sid)) if sid else None

    async def update(
        self, sid: str, changes: dict, *, epoch: int | None = None
    ) -> dict:
        # Merge in Redis: heartbeat and title writes arrive on different workers.
        raw = await self.redis.eval(
            "local s=redis.call('GET',KEYS[1]); if not s then return false end; "
            "local v=cjson.decode(s); "
            "if ARGV[3]~='' and (v.status~='active' or v.epoch~=tonumber(ARGV[3])) "
            "then return false end; "
            "for k,x in pairs(cjson.decode(ARGV[1])) do v[k]=x end; "
            "local out=cjson.encode(v); "
            "redis.call('SET',KEYS[1],out,'EX',ARGV[2]); return out",
            1,
            key(sid),
            json.dumps(changes),
            RETENTION,
            epoch if epoch is not None else "",
        )
        if not raw:
            if epoch is not None:
                raise AuthenticationRequiredError("RC worker is no longer current")
            raise NotFoundError("RC session not found")
        session = json.loads(raw)
        await self.redis.eval(
            "if redis.call('GET',KEYS[1])==ARGV[1] then "
            "return redis.call('EXPIRE',KEYS[1],ARGV[2]) end; return 0",
            1,
            key(session["topic_id"], "current"),
            sid,
            RETENTION,
        )
        return session

    async def bridge(self, session: dict) -> dict:
        now = int(time.time())
        expires = min(now + WORKER_TTL, session["expires_at"])
        if expires <= now:
            raise AuthenticationRequiredError("RC launch credential expired")
        epoch = await self.redis.eval(
            "local s=redis.call('GET',KEYS[1]); if not s then return false end; "
            "local v=cjson.decode(s); v.epoch=v.epoch+1; v.status='active'; "
            "redis.call('SET',KEYS[1],cjson.encode(v),'EX',ARGV[1]); return v.epoch",
            1,
            key(session["id"]),
            RETENTION,
        )
        if not epoch:
            raise NotFoundError("RC session not found")
        token = jwt.encode(
            {
                "sub": session["id"],
                "aud": "cheese-rc-worker",
                "epoch": epoch,
                "exp": expires,
            },
            settings.jwt_secret,
            algorithm="HS256",
        )
        return {
            "worker_jwt": token,
            "worker_epoch": epoch,
            "api_base_url": settings.connector_public_base.rstrip("/"),
            "expires_in": expires - now,
        }

    async def authenticate_worker(self, sid: str, token: str) -> dict:
        try:
            claims = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=["HS256"],
                audience="cheese-rc-worker",
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationRequiredError("Invalid RC worker credential") from exc
        session = await self.get(sid)
        if (
            claims.get("sub") != sid
            or claims.get("epoch") != session["epoch"]
            or session["status"] != "active"
        ):
            raise AuthenticationRequiredError("RC worker is no longer current")
        return session

    async def enqueue(self, sid: str, payload: dict, actor: str) -> dict:
        """A retry with the same id returns the original command, never replays it."""
        request_id = payload.get("request_id") or payload.get("response", {}).get(
            "request_id"
        )
        identity = payload.get("uuid") or request_id
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        command = {
            "payload": payload,
            "actor": actor,
            "status": "queued",
            "digest": digest,
        }
        raw = await self.redis.eval(
            "local old=redis.call('GET',KEYS[1]); if old then return old end; "
            "local p=cjson.decode(ARGV[1]).payload; "
            "if p.type=='control_response' and "
            "redis.call('HEXISTS',KEYS[5],p.response.request_id)==0 "
            "then return false end; "
            "local n=redis.call('INCR',KEYS[3]); local v=cjson.decode(ARGV[1]); "
            "v.sequence_num=tostring(n); v.event_id=ARGV[2]; "
            "local out=cjson.encode(v); redis.call('SET',KEYS[1],out,'EX',ARGV[3]); "
            "redis.call('SET',KEYS[4],KEYS[1],'EX',ARGV[3]); "
            "redis.call('XADD',KEYS[2],tostring(n)..'-0','event',out); "
            "redis.call('EXPIRE',KEYS[2],ARGV[3]); "
            "redis.call('EXPIRE',KEYS[3],ARGV[3]); return out",
            5,
            key(sid, "command:" + str(identity)),
            key(sid, "in"),
            key(sid, "sequence"),
            key(sid, "event:" + (event_id := str(uuid.uuid4()))),
            key(sid, "pending"),
            json.dumps(command),
            event_id,
            RETENTION,
        )
        if not raw:
            raise ConflictError("This question is no longer pending")
        result = json.loads(raw)
        if result["digest"] != digest:
            raise ConflictError("This request id already has a different payload")
        return result

    async def command(self, sid: str, request_id: str) -> dict | None:
        raw = await self.redis.get(key(sid, "command:" + request_id))
        return json.loads(raw) if raw else None

    async def receive(self, sid: str, events: list[dict], *, epoch: int) -> None:
        """Commit deduplicated events and derived state under one epoch check."""
        prepared = []
        for event in events:
            payload = event["payload"]
            event_id = payload.get("uuid") or event.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise ValueError("Worker events require a uuid")
            kind = payload.get("type")
            if kind == "control_response":
                response = payload.get("response", {})
                if not isinstance(response, dict):
                    raise ValueError("Invalid control response")
                rid = response.get("request_id")
                if rid is not None and not isinstance(rid, str):
                    raise ValueError("Invalid response request id")
                for field in (
                    "pending_permission_requests",
                    "pending_user_dialog_requests",
                ):
                    pending = response.get(field, [])
                    if not isinstance(pending, list) or any(
                        not isinstance(p, dict)
                        or not isinstance(p.get("request_id"), str)
                        for p in pending
                    ):
                        raise ValueError("Invalid pending requests")
            elif kind in ("control_request", "control_cancel_request"):
                if not isinstance(payload.get("request_id"), str):
                    raise ValueError("Invalid control request id")
            if payload.get("task_id") is not None and not isinstance(
                payload["task_id"], str
            ):
                raise ValueError("Invalid task id")
            item: dict = {
                "id": event_id,
                "payload": payload,
                "encoded": json.dumps(payload),
            }
            if kind == "control_response":
                item["initialize"] = json.dumps(response.get("response", {}))
                item["pending"] = [
                    {"id": p["request_id"], "encoded": json.dumps(p)}
                    for field in (
                        "pending_permission_requests",
                        "pending_user_dialog_requests",
                    )
                    for p in response.get(field, [])
                ]
            prepared.append(item)
        committed = await self.redis.eval(
            _CHECK_EPOCH
            + """
local prefix, ttl = ARGV[2], ARGV[3]
local function pending(id, encoded)
    local answer = redis.call('GET', prefix..'command:answer-'..id)
    if answer and cjson.decode(answer).status == 'processed' then return end
    redis.call('HSET', prefix..'pending', id, encoded)
    redis.call('EXPIRE', prefix..'pending', ttl)
end
for _, event in ipairs(cjson.decode(ARGV[4])) do
    local p = event.payload
    if redis.call('SET', prefix..'seen:'..event.id, '1', 'NX', 'EX', ttl) then
        local encoded = event.encoded
        redis.call('XADD', prefix..'out', 'MAXLEN', '~', 4096, '*', 'event', encoded)
        redis.call('EXPIRE', prefix..'out', ttl)
        if p.type == 'control_response' then
            local response = p.response or {}
            local rid = response.request_id
            if rid and rid ~= cjson.null then
                if string.find(rid, 'cheese-initialize-', 1, true) == 1 then
                    redis.call('HSET', prefix..'state', 'initialize', event.initialize)
                    redis.call('EXPIRE', prefix..'state', ttl)
                    for _, request in ipairs(event.pending) do
                        pending(request.id, request.encoded)
                    end
                end
                redis.call('SET', prefix..'result:'..rid, encoded, 'EX', ttl)
                local command = redis.call('GET', prefix..'command:'..rid)
                if command then
                    local v = cjson.decode(command)
                    v.status = 'completed'
                    redis.call('SET', prefix..'command:'..rid,
                               cjson.encode(v), 'EX', ttl)
                end
            end
        elseif p.type == 'control_request' then
            pending(p.request_id, encoded)
        elseif p.type == 'control_cancel_request' then
            redis.call('HDEL', prefix..'pending', p.request_id)
        elseif p.type == 'system'
            and (p.subtype == 'init' or p.subtype == 'status') then
            redis.call('HSET', prefix..'state', p.subtype, encoded)
            redis.call('EXPIRE', prefix..'state', ttl)
        elseif p.type == 'system' and p.task_id and p.task_id ~= cjson.null then
            local old = redis.call('HGET', prefix..'tasks', p.task_id)
            local task = old and cjson.decode(old) or {}
            for k, v in pairs(p) do task[k] = v end
            redis.call('HSET', prefix..'tasks', p.task_id, cjson.encode(task))
            redis.call('EXPIRE', prefix..'tasks', ttl)
        end
    end
end
session.last_seen = tonumber(ARGV[5])
redis.call('SET', KEYS[1], cjson.encode(session), 'EX', ttl)
if redis.call('GET', KEYS[2]) == session.id then redis.call('EXPIRE', KEYS[2], ttl) end
return 1
""",
            2,
            key(sid),
            key((await self.get(sid))["topic_id"], "current"),
            epoch,
            key(sid, ""),
            RETENTION,
            json.dumps(prepared),
            time.time(),
        )
        if not committed:
            raise AuthenticationRequiredError("RC worker is no longer current")

    async def delivery(self, sid: str, updates: list[dict], *, epoch: int) -> None:
        """Keep answers pending until the worker acknowledges processing them."""
        if any(
            not isinstance(update, dict)
            or not isinstance(update.get("event_id"), str)
            or update.get("status") not in ("received", "processed")
            for update in updates
        ):
            raise ValueError("Invalid RC delivery update")
        committed = await self.redis.eval(
            _CHECK_EPOCH
            + """
local prefix, ttl = ARGV[2], ARGV[3]
for _, update in ipairs(cjson.decode(ARGV[4])) do
    local command_key = redis.call('GET', prefix..'event:'..update.event_id)
    local raw = command_key and redis.call('GET', command_key)
    if raw then
        local command = cjson.decode(raw)
        if command.status ~= 'completed' and command.status ~= 'processed' then
            command.status = update.status
            redis.call('SET', command_key, cjson.encode(command), 'EX', ttl)
        end
        if update.status == 'processed'
            and command.payload.type == 'control_response' then
            redis.call('HDEL', prefix..'pending', command.payload.response.request_id)
        end
    end
end
return 1
""",
            1,
            key(sid),
            epoch,
            key(sid, ""),
            RETENTION,
            json.dumps(updates),
        )
        if not committed:
            raise AuthenticationRequiredError("RC worker is no longer current")

    async def result(self, sid: str, request_id: str, wait_s: float = 0) -> dict | None:
        deadline = time.monotonic() + wait_s
        while True:
            raw = await self.redis.get(key(sid, "result:" + request_id))
            if raw:
                return json.loads(raw)
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(0.2)

    async def snapshot(self, session: dict) -> dict:
        sid = session["id"]
        groups = {}
        for name in ("pending", "tasks", "state"):
            rows = await self.redis.hgetall(key(sid, name))
            groups[name] = {text(k): json.loads(v) for k, v in rows.items()}
        return {
            "id": sid,
            "title": session["title"],
            "status": session["status"],
            "connected": session["status"] == "active"
            and time.time() - session["last_seen"] < 90,
            "controls": CONTROLS,
            **groups,
        }

    async def journal(self, sid: str, cursor: str) -> list[dict]:
        rows = cast(
            list[tuple[bytes, dict[bytes, bytes]]],
            await self.redis.xrange(key(sid, "out"), min="(" + cursor, count=200),
        )
        return [
            {"cursor": text(i), "payload": json.loads(v[b"event"])} for i, v in rows
        ]

    async def worker_stream(self, session: dict, token: str, cursor: str):
        sid = session["id"]
        # The SSE cursor proves receipt, not processing. Revisit answers on each
        # connection until processed; persisted command state filters controls.
        cursor = "0-0"
        yield ": connected\n\n"
        while True:
            try:
                await self.authenticate_worker(sid, token)
            except (AuthenticationRequiredError, NotFoundError):
                return
            rows = cast(
                list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]],
                await self.redis.xread(
                    # Redis 8's default socket timeout is shorter than a long
                    # SSE heartbeat interval. Leave room for the read to finish.
                    {key(sid, "in"): cursor},
                    block=1000,
                    count=100,
                ),
            )
            try:
                await self.authenticate_worker(sid, token)
            except (AuthenticationRequiredError, NotFoundError):
                return
            if not rows:
                yield ": heartbeat\n\n"
                continue
            for _, entries in rows:
                for index, values in entries:
                    cursor = text(index)
                    command = json.loads(values[b"event"])
                    offered = await self.redis.eval(
                        _CHECK_EPOCH
                        + """
local command_key = redis.call('GET', KEYS[2])
local raw = command_key and redis.call('GET', command_key)
if not raw then return 'skip' end
local command = cjson.decode(raw)
local answer = command.payload.type == 'control_response'
if command.status == 'completed' or command.status == 'processed' then return 'skip' end
if not answer and command.status ~= 'queued' then return 'skip' end
if answer and redis.call('HEXISTS', KEYS[3],
                         command.payload.response.request_id) == 0 then
    return 'skip'
end
command.status = 'uncertain'
local out = cjson.encode(command)
redis.call('SET', command_key, out, 'EX', ARGV[2])
return out
""",
                        3,
                        key(sid),
                        key(sid, "event:" + command["event_id"]),
                        key(sid, "pending"),
                        session["epoch"],
                        RETENTION,
                    )
                    if not offered:
                        return
                    if text(offered) == "skip":
                        continue
                    command = json.loads(offered)
                    event = {
                        "event_id": command["event_id"],
                        "sequence_num": command["sequence_num"],
                        "source": "client",
                        "event_type": command["payload"]["type"],
                        "payload": command["payload"],
                    }
                    yield (
                        f"event: client_event\nid: {event['sequence_num']}\n"
                        f"data: {json.dumps(event)}\n\n"
                    )
