"""Minimal CDP driver over websocket, for measuring page-open timings.

The sandbox's Chrome occasionally goes quiet while the renderer loads the app,
so every call retries on a fresh websocket instead of dying.
"""
import json
import time
import urllib.request

import websocket  # websocket-client


class Tab:
    def __init__(self, ws_url, timeout=30.0):
        self.ws_url = ws_url
        self.timeout = timeout
        self.ws = None
        self.i = 0
        self._connect()

    def _connect(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        self.ws = websocket.create_connection(
            self.ws_url, timeout=self.timeout, max_size=200 * 1024 * 1024
        )

    def _send_once(self, method, params):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def send(self, method, retries=4, **params):
        last = None
        for _ in range(retries):
            try:
                return self._send_once(method, params)
            except Exception as e:
                last = e
                time.sleep(0.5)
                self._connect()
        raise RuntimeError(f"{method} failed after {retries}: {last!r}")

    def eval(self, expr, retries=5, await_promise=False, timeout=60000):
        last = None
        for _ in range(retries):
            try:
                r = self._send_once(
                    "Runtime.evaluate",
                    {"expression": f"({expr})()", "awaitPromise": await_promise,
                     "returnByValue": True, "timeout": timeout},
                )
                if "exceptionDetails" in r:
                    raise RuntimeError(json.dumps(r["exceptionDetails"])[:400])
                return r["result"].get("value")
            except RuntimeError:
                raise
            except Exception as e:
                last = e
                time.sleep(0.5)
                self._connect()
        raise RuntimeError(f"eval failed after {retries}: {last!r}")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def new_tab(port=9333, url="about:blank"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/json/new?{url}", method="PUT")
    with urllib.request.urlopen(req, timeout=15) as r:
        info = json.load(r)
    return Tab(info["webSocketDebuggerUrl"])
