#!/usr/bin/env python3
"""The platform API client `seat_tps.py` and `wake_orchestrator.py` share.

Each script used to carry its own copy of `call`, `unwrap` and `login`, byte for byte,
plus the same `API`/`USER` lines. The only difference was `call`'s default timeout (60
against 30). A change to one copy — a retry on 429, a request header, a field the login
answer moved — left the other talking to the same platform with the old client, and each
script's `--self-test` stubbed only its own.

`call` keeps the `timeout` parameter: it is per caller, not per platform, and the
throughput endpoint aggregates enough that `seat_tps.py` asks for 60 where the default
stays 30. Credentials come from the environment, never from argv (ps shows it) and never
from a file: `OKCHEESE_PASS` is read by the caller, `OKCHEESE_USER` (default: the dev
account) and `OKCHEESE_API` are read here at import.
"""
import json
import os
import urllib.request

API = os.environ.get("OKCHEESE_API", "https://okcheese.com/api")
USER = os.environ.get("OKCHEESE_USER", "wangchangxin")


def call(method, path, token=None, body=None, timeout=30):
    """One platform call. Raises OSError/HTTPError; returns the parsed JSON body."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def unwrap(x):
    """The payload of a platform response: `{"data": …}`, however many times nested."""
    while isinstance(x, dict) and "data" in x:
        x = x["data"]
    return x


def login(user, password, timeout=30):
    return unwrap(call("POST", "/users/auth/login",
                       body={"username": user, "password": password},
                       timeout=timeout))["accessToken"]
