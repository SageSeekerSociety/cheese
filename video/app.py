"""FB-105 临时验收入口（不进产品）。

GET  /                 页面
POST /api/run?side=X   X = before（main 树）| after（PR 分支树）；新起一次运行
GET  /api/run/<id>     这一次运行的状态、启动环境、Claude Code 输出、上游日志
假上游在同一进程的另一个端口；每次运行用 ANTHROPIC_CUSTOM_HEADERS 带上自己的
请求 ID，上游按这个 ID 分开记日志。"""
import json, os, subprocess, sys, threading, uuid, time, http.server, shutil, pathlib, datetime

ROOT = pathlib.Path("/tmp/fb105app")
RUNS = ROOT / "runs"; RUNS.mkdir(exist_ok=True)
TREES = {"before": "/tmp/fb105-main", "after": os.environ["FB105_BRANCH_TREE"]}
VENV_PY = os.environ["FB105_BRANCH_TREE"] + "/backend/.venv/bin/python"
TEST_SRC = os.environ["FB105_BRANCH_TREE"] + "/backend/tests/integration/test_gateway_usage.py"
SNIPPET = "/tmp/fb105ev/dump_snippet.py"
UP_PORT = int(os.environ.get("FB105_UP_PORT", "18916"))
APP_PORT = int(os.environ.get("FB105_APP_PORT", "18920"))
CLAUDE = os.environ["CHEESE_TEST_CLAUDE"]
lock = threading.Lock()
state = {}  # run id -> dict

def now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S UTC")

# ---------------- fake upstream ----------------
sys.path.insert(0, "/tmp/fb105ev/srv")
def sse(events):
    return b"".join(f"event: {n}\ndata: {json.dumps(d)}\n\n".encode() for n, d in events)
def _start(model): return ("message_start", {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None, "usage": {"input_tokens": 1, "output_tokens": 1}}})
def _end(reason): return [("message_delta", {"type": "message_delta", "delta": {"stop_reason": reason}, "usage": {"output_tokens": 1}}), ("message_stop", {"type": "message_stop"})]
def text_reply(model, text):
    return sse([_start(model), ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0})] + _end("end_turn"))
def tool_reply(model):
    inp = json.dumps({"description": "probe", "prompt": "Say OK.", "subagent_type": "third-model", "run_in_background": False})
    return sse([_start(model), ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Agent", "input": {}}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": inp}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0})] + _end("tool_use"))

class Up(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b'{"data":[]}')
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", 0)))
        body = json.loads(raw or b"{}")
        rid = self.headers.get("x-fb105-run", "?")
        if "count_tokens" in self.path or not self.path.startswith("/v1/messages"):
            self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers(); self.wfile.write(b'{"input_tokens":1}'); return
        model = body.get("model", ""); msgs = body.get("messages", [])
        sysidx = [i for i, m in enumerate(msgs) if m.get("role") == "system"]
        e = {"t": now(), "run": rid, "model": model, "roles": [m.get("role") for m in msgs], "system_role_at": sysidx}
        if model == "third-model" and sysidx:
            e.update(status=400, reply="System messages are not allowed")
            payload = json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "System messages are not allowed"}}).encode()
            self._log(rid, e); self.send_response(400); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        has_result = any(isinstance(m.get("content"), list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"]) for m in msgs if m.get("role") == "user")
        if model == "third-model": out, what = text_reply(model, "OK from third-model"), "文本 OK from third-model"
        elif has_result: out, what = text_reply(model, "done"), "文本 done"
        elif body.get("tools"): out, what = tool_reply(model), "调用 Agent(subagent_type=third-model)"
        else: out, what = text_reply(model, "ok"), "文本 ok"
        e.update(status=200, reply=what); self._log(rid, e)
        self.send_response(200); self.send_header("content-type", "text/event-stream"); self.end_headers(); self.wfile.write(out)
    def _log(self, rid, e):
        with open(RUNS / f"{rid}.upstream.jsonl", "a") as f: f.write(json.dumps(e, ensure_ascii=False) + "\n")

# ---------------- one run ----------------
def run(rid, side):
    st = state[rid]; tree = TREES[side]; d = RUNS / rid; d.mkdir()
    try:
        with lock:
            st["head"] = subprocess.run(["git", "-C", tree, "log", "-1", "--format=%h %s"], capture_output=True, text=True).stdout.strip()
            st["stage"] = "在这棵树上实算启动环境（跑 _model_kwargs）"
            dump = d / "env.json"; test = pathlib.Path(tree) / "backend/tests/integration" / f"test_zz_fb105_{rid}.py"
            test.write_text(open(TEST_SRC).read() + open(SNIPPET).read())
            try:
                p = subprocess.run([VENV_PY, "-m", "pytest", f"{test.relative_to(pathlib.Path(tree)/'backend')}::test_zz_dump_launch_env", "-q", "-p", "no:cacheprovider"],
                                   cwd=f"{tree}/backend", env={**os.environ, "FB105_DUMP": str(dump)}, capture_output=True, text=True, timeout=300)
            finally:
                test.unlink(missing_ok=True)
            st["env_log"] = p.stdout[-600:]
            if p.returncode: raise RuntimeError("实算启动环境失败:\n" + p.stdout[-2000:])
            launch = json.loads(dump.read_text())["after"]
            envv = launch["env"]
            st["env"] = {k: envv.get(k) for k in ("ANTHROPIC_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_MODEL_CAPABILITIES")}
            st["env"]["CHEESE_MODEL_AGENTS"] = sorted(json.loads(envv["CHEESE_MODEL_AGENTS"]))
            work = d / "work"; (work / ".claude/agents").mkdir(parents=True); (d / "cfg").mkdir()
            for fn, txt in json.loads(envv["CHEESE_MODEL_AGENTS"]).items(): (work / ".claude/agents" / fn).write_text(txt)
            st["stage"] = "真 Claude Code 在跑"
            cenv = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "CLAUDE"))}
            cenv.update(HOME=str(d), CLAUDE_CONFIG_DIR=str(d / "cfg"), ANTHROPIC_BASE_URL=f"http://127.0.0.1:{UP_PORT}", ANTHROPIC_API_KEY="sk-fake",
                        ANTHROPIC_CUSTOM_HEADERS=f"x-fb105-run: {rid}", DISABLE_TELEMETRY="1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                        ANTHROPIC_MODEL=envv["ANTHROPIC_MODEL"], CLAUDE_CODE_SUBAGENT_MODEL=envv["CLAUDE_CODE_SUBAGENT_MODEL"])
            if envv.get("CLAUDE_CODE_MODEL_CAPABILITIES"): cenv["CLAUDE_CODE_MODEL_CAPABILITIES"] = envv["CLAUDE_CODE_MODEL_CAPABILITIES"]
            cmd = [CLAUDE, "-p", "Delegate to the third-model subagent and wait for its answer.", "--model", envv["ANTHROPIC_MODEL"],
                   "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose"]
            st["cmd"] = "claude -p … --model " + envv["ANTHROPIC_MODEL"] + " --output-format stream-json"
            st["version"] = subprocess.run([CLAUDE, "--version"], capture_output=True, text=True, env=cenv).stdout.strip()
            proc = subprocess.Popen(cmd, cwd=work, env=cenv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                st["out"].append(line.rstrip("\n")[:4000])
            st["exit"] = proc.wait(timeout=240)
        ups = [json.loads(l) for l in open(RUNS / f"{rid}.upstream.jsonl")] if (RUNS / f"{rid}.upstream.jsonl").exists() else []
        third = [u for u in ups if u["model"] == "third-model"]
        if third and all(u["status"] == 200 for u in third): st["verdict"] = "success"
        elif any(u["status"] == 400 for u in third): st["verdict"] = "failure"
        else: st["verdict"] = "inconclusive"
        st["stage"] = "结束"
    except Exception as ex:
        st["stage"] = "出错"; st["error"] = str(ex); st["verdict"] = "error"
    st["finished"] = now()

class App(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode(); self.send_response(code); self.send_header("content-type", "application/json; charset=utf-8"); self.send_header("cache-control", "no-store"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            b = (ROOT / "index.html").read_bytes(); self.send_response(200); self.send_header("content-type", "text/html; charset=utf-8"); self.send_header("cache-control", "no-store"); self.end_headers(); self.wfile.write(b); return
        if self.path.startswith("/api/run/"):
            rid = self.path.rsplit("/", 1)[1]; st = state.get(rid)
            if not st: return self._json({"error": "no such run"}, 404)
            f = RUNS / f"{rid}.upstream.jsonl"
            st = dict(st, upstream=[json.loads(l) for l in open(f)] if f.exists() else [])
            return self._json(st)
        if self.path == "/api/heads":
            return self._json({k: subprocess.run(["git", "-C", t, "log", "-1", "--format=%h"], capture_output=True, text=True).stdout.strip() for k, t in TREES.items()})
        if self.path == "/api/runs":
            return self._json([{k: v for k, v in s.items() if k in ("id", "side", "started", "verdict", "stage")} for s in state.values()])
        self._json({"error": "not found"}, 404)
    def do_POST(self):
        if self.path.startswith("/api/run?side="):
            side = self.path.split("=", 1)[1]
            if side not in TREES: return self._json({"error": "side must be before|after"}, 400)
            rid = uuid.uuid4().hex[:12]
            state[rid] = {"id": rid, "side": side, "started": now(), "stage": "排队", "out": [], "verdict": None}
            threading.Thread(target=run, args=(rid, side), daemon=True).start()
            return self._json({"id": rid})
        self._json({"error": "not found"}, 404)

threading.Thread(target=lambda: http.server.ThreadingHTTPServer(("127.0.0.1", UP_PORT), Up).serve_forever(), daemon=True).start()
http.server.ThreadingHTTPServer(("127.0.0.1", APP_PORT), App).serve_forever()
