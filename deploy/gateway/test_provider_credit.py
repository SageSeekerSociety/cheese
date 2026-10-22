"""A row whose provider account is out of credit is taken out of rotation, the
caller is told what to do next, and the row is named for the administrators.

Drives the proxy's own /v1/messages endpoint, with the callbacks config.yaml
registers and the worker start-up hooks the Dockerfile sets, against a local
fake upstream. A model group is served the way deepseek-flash is: a RUC row
first (order 1) and a pay-per-use row behind it (order 3). Each refusal is the
one the provider was seen sending on the dev gateway when the platform's
account ran dry.
"""

import asyncio
import importlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import litellm
import litellm.proxy.proxy_server as proxy_server
import yaml
from litellm import Router

# The contract with the metering proxy (which reports the rows this header
# names) and with the backend (which reads the code to word the room's notice).
NAMED = "x-cheese-upstream-out-of-credit"
CODE = "cheese_upstream_credit_exhausted"

OUT_OF_CREDIT = {
    "deepseek": (400, {"error": {"message": "Insufficient Balance (request_id: 3f1c)", "type": "unknown_error",
                                 "param": None, "code": "invalid_request_error"}}),
    "zhipu": (429, {"type": "error", "error": {"type": "rate_limit_error", "code": "1113",
                                               "message": "[1113][余额不足或无可用资源包,请充值。][2026102214]"}}),
    "ark": (429, {"error": {"code": "SetLimitExceeded", "message": "Your account [2100] has reached the set usage "
                            "limit for this model, and the model service has been paused."}}),
    "moonshot": (429, {"error": {"message": "Your account org-1 <ak-1> is suspended due to insufficient balance, "
                                 "please recharge your account", "type": "exceeded_current_quota_error"}}),
    "opencode": (429, {"type": "error", "error": {"type": "FreeUsageLimitError", "message":
                                                  "Free usage limit reached. Subscribe to OpenCode Go to keep going"}}),
    "openai": (429, {"error": {"message": "You exceeded your current quota, please check your plan and billing "
                               "details.", "type": "insufficient_quota", "code": "insufficient_quota"}}),
    "anthropic": (400, {"type": "error", "error": {"type": "invalid_request_error", "message":
                                                   "Your credit balance is too low to access the Anthropic API."}}),
}
# Refusals that say nothing about the account: the row stays in rotation.
ORDINARY = {
    "busy": (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "Limit type: "
                                              "parallel_requests. Current limit: 3, Remaining: 0"}}),
    "image": (400, {"type": "error", "error": {"type": "invalid_request_error",
                                               "message": "Could not process image"}}),
    "gemini": (429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded for quota "
                               "metric 'Generate Content API requests per minute'"}}),
}
BUSY = ORDINARY["busy"]
OK = (200, {"id": "msg_1", "type": "message", "role": "assistant", "model": "m", "stop_reason": "end_turn",
            "stop_sequence": None, "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 1, "output_tokens": 1}})

# What each row answers, and how often it was asked.
ANSWERS: dict[str, tuple[int, dict]] = {}
CALLS: dict[str, int] = {}


async def serve(reader, writer):
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    row = lines[0].split(b" ")[1].decode().split("/")[1]
    length = int(next(line.split(b":", 1)[1] for line in lines if line.lower().startswith(b"content-length:")))
    await reader.readexactly(length)
    CALLS[row] = CALLS.get(row, 0) + 1
    status, body = ANSWERS[row]
    data = json.dumps(body, ensure_ascii=False).encode()
    writer.write(
        b"HTTP/1.1 %d X\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
        % (status, len(data), data)
    )
    await writer.drain()
    writer.close()


def rows(port, name, served_by):
    return [
        {
            "model_name": name,
            "litellm_params": {"model": f"anthropic/{row}", "api_base": f"http://127.0.0.1:{port}/{row}",
                               "api_key": "k", "order": order},
            "model_info": {"id": row},
        }
        for row, order in served_by
    ]


async def ask(client, model, stream=False):
    CALLS.clear()
    return await client.post("/v1/messages", json={
        "model": model, "max_tokens": 8, "stream": stream, "messages": [{"role": "user", "content": "hi"}],
    })


def the_gateway_as_configured():
    """The callbacks config.yaml registers and the Dockerfile's start-up hooks."""
    for hook in os.environ["LITELLM_WORKER_STARTUP_HOOKS"].split(","):
        module, name = hook.split(":")
        getattr(importlib.import_module(module), name)()
    config = yaml.safe_load(Path(__file__).with_name("config.yaml").read_text())
    callbacks = []
    for spec in config["litellm_settings"]["callbacks"]:
        module, name = spec.rsplit(".", 1)
        callbacks.append(getattr(importlib.import_module(module), name))
    litellm.callbacks = callbacks


async def main():
    the_gateway_as_configured()
    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    model_list = (
        rows(port, "flash", [("ruc", 1), ("official", 3)])
        + rows(port, "doubao", [("ruc2", 1), ("ark2", 3)])
        + rows(port, "kimi", [("moonshot2", 1)])
        + rows(port, "glm", [("zhipu2", 1), ("glm-api", 3)])
        + rows(port, "doubao2", [("switched-off", 1), ("ark3", 3)])
    )
    # A line switched off on the admin page takes no requests.
    model_list[-2]["model_info"]["blocked"] = True
    for name in [*OUT_OF_CREDIT, *ORDINARY]:
        model_list += rows(port, f"solo-{name}", [(name, 1)])
    # The router's own cooldown is 5 s; shorter here so the test can outwait it.
    router = Router(model_list=model_list, num_retries=0, cooldown_time=0.5)
    transport = httpx.ASGITransport(app=proxy_server.app)
    async with server, httpx.AsyncClient(transport=transport, base_url="http://gateway", timeout=30) as client:
        with patch.object(proxy_server, "llm_router", router), patch.object(proxy_server, "master_key", None):
            # RUC is at its parallel limit and the request overflows onto a row
            # whose account is empty: the caller is told to come back shortly
            # rather than handed the provider's 400, and the row is named.
            ANSWERS.update(ruc=BUSY, official=OUT_OF_CREDIT["deepseek"])
            response = await ask(client, "flash")
            assert response.status_code == 429, (response.status_code, response.text)
            assert int(response.headers["retry-after"]) >= 1, response.headers
            assert "Insufficient Balance" not in response.text, response.text
            assert "official" in response.headers.get(NAMED, ""), response.headers
            assert CALLS == {"ruc": 1, "official": 1}, CALLS
            print("PASS: overflow onto an empty account comes back as a retryable 429 naming the row")

            # RUC is still busy: the empty row is not asked again, nor named again.
            response = await ask(client, "flash")
            assert response.status_code == 429 and "official" not in CALLS, (response.status_code, CALLS)
            assert NAMED not in response.headers, response.headers
            # RUC frees a slot: the retry is answered, and the empty row is still
            # left alone well after the router's own cooldown would have ended.
            await asyncio.sleep(1.0)
            ANSWERS["ruc"] = OK
            response = await ask(client, "flash")
            assert response.status_code == 200 and CALLS == {"ruc": 1}, (response.status_code, CALLS)
            print("PASS: the empty row stays out of rotation past the router's cooldown; RUC answers the retry")

            # A provider that refuses with 429, reached as the order fallback,
            # stays out as long as one that refuses with 400.
            ANSWERS.update(ruc2=BUSY, ark2=OUT_OF_CREDIT["ark"])
            await ask(client, "doubao")
            await asyncio.sleep(1.0)
            await ask(client, "doubao")
            assert "ark2" not in CALLS, CALLS
            print("PASS: a 429 out-of-credit refusal from a fallback row keeps the row out as long as a 400 one")

            # An empty row walked past on the way to one that answers: the
            # caller gets the answer, the empty row is named for the admins and
            # left alone on the next request.
            ANSWERS.update(zhipu2=OUT_OF_CREDIT["zhipu"], **{"glm-api": OK})
            response = await ask(client, "glm")
            assert response.status_code == 200 and "zhipu2" in response.headers.get(NAMED, ""), (
                response.status_code, response.headers)
            await asyncio.sleep(1.0)
            response = await ask(client, "glm")
            assert response.status_code == 200 and CALLS == {"glm-api": 1}, CALLS
            assert NAMED not in response.headers, response.headers
            print("PASS: a request answered past an empty row names it, and the next one skips it")

            # Every provider's refusal, as the only row of a model: one 402 that
            # Claude Code does not retry, naming the row; and the next request,
            # streamed as Claude Code streams, is answered without asking it.
            for name, refusal in OUT_OF_CREDIT.items():
                ANSWERS[name] = refusal
                response = await ask(client, f"solo-{name}")
                assert response.status_code == 402 and CODE in response.text, (name, response.status_code,
                                                                               response.text)
                assert name in response.headers.get(NAMED, ""), (name, response.headers)
                response = await ask(client, f"solo-{name}", stream=True)
                assert response.status_code == 402 and CODE in response.text and CALLS == {}, (name, CALLS)
            print("PASS: a model whose every row is out of credit gets one non-retryable 402, for each provider")

            # A refusal that says nothing about the account is passed on as it
            # came, and the row is asked again once the router's cooldown ends.
            for name, refusal in ORDINARY.items():
                ANSWERS[name] = refusal
                response = await ask(client, f"solo-{name}")
                assert response.status_code == refusal[0] and NAMED not in response.headers, (name, response.text)
                await asyncio.sleep(1.0)
                await ask(client, f"solo-{name}")
                assert CALLS == {name: 1}, (name, CALLS)
            print("PASS: a rate limit, a bad request or a per-minute quota does not take the row out")

            # A model whose only line that takes requests is out of credit is
            # out of credit, however many switched-off lines it also has.
            ANSWERS["ark3"] = OUT_OF_CREDIT["ark"]
            for stream in (False, True):
                response = await ask(client, "doubao2", stream=stream)
                assert response.status_code == 402 and CODE in response.text, (response.status_code, response.text)
            print("PASS: a switched-off line does not count as one with credit")

            # After the long cooldown the account is asked again, so a top-up
            # is picked up without anyone touching the gateway.
            with patch.object(importlib.import_module("cheese_provider_credit"), "COOLDOWN_S", 0.5):
                ANSWERS["moonshot2"] = OUT_OF_CREDIT["moonshot"]
                assert (await ask(client, "kimi")).status_code == 402
                await asyncio.sleep(1.0)
                ANSWERS["moonshot2"] = OK
                response = await ask(client, "kimi")
                assert response.status_code == 200 and CALLS == {"moonshot2": 1}, (response.text, CALLS)
            print("PASS: once the cooldown ends the account is asked again")


async def with_redis_down():
    """The gateway's router keeps its cooldowns in Redis too. Redis being away
    costs the long cooldown its shared copy, never the request: an answer a
    later row gave still reaches the caller."""
    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    # Nothing listens on port 1.
    router = Router(
        model_list=rows(port, "flash", [("dry", 1), ("backup", 3)]),
        num_retries=0, cooldown_time=0.5, redis_host="127.0.0.1", redis_port=1,
    )
    transport = httpx.ASGITransport(app=proxy_server.app)
    async with server, httpx.AsyncClient(transport=transport, base_url="http://gateway", timeout=60) as client:
        with patch.object(proxy_server, "llm_router", router), patch.object(proxy_server, "master_key", None):
            ANSWERS.update(dry=OUT_OF_CREDIT["deepseek"], backup=OK)
            response = await ask(client, "flash")
            assert response.status_code == 200 and "dry" in response.headers.get(NAMED, ""), (
                response.status_code, response.text)
            await asyncio.sleep(1.0)
            response = await ask(client, "flash")
            assert response.status_code == 200 and CALLS == {"backup": 1}, (response.status_code, CALLS)
    print("PASS: with Redis down the answer still arrives and the empty row is still left alone")


asyncio.run(main())
asyncio.run(with_redis_down())
