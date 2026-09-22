"""Check authenticated gateway readiness; generate only for an explicit model.

Run inside the gateway container with its existing credentials.
See deploy/gateway/README.md for the command.

Default checks do not establish provider quota or inference availability.
"""

import argparse
import http.client
import json
import os
import signal
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager


class ProbeFailure(Exception):
    def __init__(self, category, status=None, code=None, call_id=None):
        self.category, self.status, self.code = category, status, code
        self.call_id = call_id
        super().__init__(category)


def error_code(body):
    error = body.get("error", {}) if isinstance(body, dict) else {}
    code = error.get("code") if isinstance(error, dict) else None
    # Only documented machine codes, never provider messages or arbitrary strings.
    return str(code) if str(code) in {"1113", "insufficient_quota"} else None


def call_id_from(headers):
    value = headers.get("x-litellm-call-id", "")
    if not isinstance(value, str) or len(value) > 36:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def http_category(status, code=None):
    if code in {"1113", "insufficient_quota"}:
        return "provider_quota"
    if status in (401, 403):
        return "http_auth"
    if status == 429:
        return "rate_limit"
    return "http_error"


def classify(error):
    if isinstance(error, ProbeFailure):
        return error.category
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, socket.gaierror):
            return "dns"
        if isinstance(error, ssl.SSLError):
            return "tls"
        if isinstance(error, TimeoutError):
            return "timeout"
        if isinstance(error, http.client.IncompleteRead):
            return "stream_transport"
        if isinstance(error, (ConnectionError, OSError)) and not isinstance(
            error, urllib.error.URLError
        ):
            return "connection"
        error = getattr(error, "reason", None) or error.__cause__ or error.__context__
        if not isinstance(error, BaseException):
            break
    return "unknown_error"


@contextmanager
def deadline(seconds):
    def expired(*_):
        raise TimeoutError("probe deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def emit(phase, **fields):
    print(
        json.dumps({"unix_ms": round(time.time() * 1000), "phase": phase, **fields}),
        flush=True,
    )


def open_request(base, key, path, payload=None):
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    request = urllib.request.Request(
        base + path,
        headers=headers,
        data=None if payload is None else json.dumps(payload).encode(),
    )
    try:
        return urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        try:
            code = error_code(json.loads(error.read(65536)))
        except (ValueError, OSError):
            code = None
        finally:
            error.close()
        raise ProbeFailure(
            http_category(error.code, code),
            error.code,
            code,
            call_id_from(error.headers),
        ) from error


def read_json(response):
    raw = response.read(262145)
    if len(raw) > 262144:
        raise ProbeFailure("invalid_response")
    try:
        return json.loads(raw)
    except ValueError as error:
        raise ProbeFailure("invalid_response") from error


def readiness(base, key):
    with open_request(base, key, "/health/readiness") as response:
        state = read_json(response)
    if (
        not isinstance(state, dict)
        or state.get("db") != "connected"
        or state.get("status") != "healthy"
    ):
        raise ProbeFailure("gateway_dependency")
    with open_request(base, key, "/model/info") as response:
        catalog = read_json(response)
    rows = catalog.get("data") if isinstance(catalog, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ProbeFailure("invalid_catalog")
    models = {
        row["model_name"]
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("model_name"), str)
    }
    if not models:
        raise ProbeFailure("invalid_catalog")
    emit(
        "readiness",
        authenticated=True,
        database="connected",
        models=len(models),
        inference="not_checked",
    )
    return models


def stream_result(response):
    if "text/event-stream" not in (response.headers.get("Content-Type") or ""):
        raise ProbeFailure("invalid_response")
    data, size, started, stopped = [], 0, 0, False
    stop_reason, usage = None, {}
    while True:
        raw = response.readline(65537)
        if not raw:
            break
        size += len(raw)
        if size > 65536:
            raise ProbeFailure("invalid_response")
        if raw.strip():
            if raw.startswith(b"data:"):
                data.append(raw[5:].strip())
            continue
        if not data:
            size = 0
            continue
        try:
            event = json.loads(b"\n".join(data))
        except ValueError as error:
            raise ProbeFailure("invalid_response") from error
        data, size = [], 0
        if not isinstance(event, dict):
            raise ProbeFailure("invalid_response")
        kind = event.get("type")
        if kind == "error":
            code = error_code(event)
            raise ProbeFailure(
                "provider_quota" if code else "provider_error", code=code
            )
        if kind == "message_start":
            started += 1
            message = event.get("message", {})
            if not isinstance(message, dict):
                raise ProbeFailure("invalid_response")
            values = message.get("usage", {})
        else:
            values = event.get("usage", {})
        if not isinstance(values, dict):
            raise ProbeFailure("invalid_response")
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = values.get(name)
            if isinstance(value, int) and value >= 0:
                usage[name] = value
        if kind == "message_delta":
            delta = event.get("delta", {})
            if not isinstance(delta, dict):
                raise ProbeFailure("invalid_response")
            stop_reason = delta.get("stop_reason", stop_reason)
        if kind == "message_stop":
            stopped = True
            break
    if started != 1 or not stopped:
        raise ProbeFailure("stream_incomplete")
    if stop_reason == "max_tokens":
        raise ProbeFailure("output_limit")
    if stop_reason not in ("end_turn", "tool_use", "stop_sequence"):
        raise ProbeFailure("invalid_stop_reason")
    return stop_reason, usage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--model")
    args = parser.parse_args(argv)
    if bool(args.generate) != bool(args.model):
        parser.error("--generate and exactly one --model are required together")
    base = os.environ.get("GATEWAY_BASE", "http://127.0.0.1:4000").rstrip("/")
    key = os.environ.get("LITELLM_MASTER_KEY", "")
    started, phase, call_id = time.monotonic(), "readiness", None
    emit("start", generate=args.generate)
    try:
        if not key:
            raise ProbeFailure("missing_credential")
        with deadline(30):
            models = readiness(base, key)
        if args.generate:
            phase = "inference"
            if args.model not in models:
                raise ProbeFailure("unknown_model")
            payload = {
                "model": args.model,
                "messages": [
                    {
                        "role": "user",
                        "content": "What is 1+1? Reply only with the number.",
                    }
                ],
                "max_tokens": 512,
                "stream": True,
                "output_config": {"effort": "low"},
            }
            emit("inference_start", model=args.model, max_tokens=512)
            with (
                deadline(60),
                open_request(base, key, "/v1/messages", payload) as response,
            ):
                call_id = call_id_from(response.headers)
                stop, usage = stream_result(response)
            emit(
                "inference",
                model=args.model,
                call_id=call_id,
                stop_reason=stop,
                usage=usage,
            )
        emit("complete", elapsed_ms=round((time.monotonic() - started) * 1000))
        return 0
    except Exception as error:
        emit(
            "failed",
            stage=phase,
            category=classify(error),
            status=getattr(error, "status", None),
            code=getattr(error, "code", None),
            call_id=getattr(error, "call_id", None) or call_id,
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
