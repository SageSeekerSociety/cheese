"""Two filings of one task's acceptance card, the second while the first waits on a slow forge read."""
import json, sys, threading, time, urllib.request, pathlib
B = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:18181"
ROOM = "ae1a7cc2-e0eb-4962-b9da-02b45d76aa2d"
DELAY = float(sys.argv[2]) if len(sys.argv) > 2 else 5
def call(method, path, body=None):
    req = urllib.request.Request(B + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"authorization": f"Bearer {TOK}", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
def login():
    req = urllib.request.Request(B + "/users/auth/login", method="POST",
        data=json.dumps({"username": "alice", "password": "demo12345"}).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["data"]["accessToken"]
TOK = login()
title = sys.argv[1] if len(sys.argv) > 1 else "递卡并发演示"
s, t = call("POST", f"/topics/{ROOM}/tasks", {"title": title})
task = t["data"]["id"]; print("task", s, task, t["data"].get("branch_name"))
flag = pathlib.Path("/tmp/fb108-forge-delay"); flag.write_text(str(DELAY))
CARD = None
card = {"reviewer_handle": "alice", "change_subject": "docs: hand in the demo link", "deliver_url": "https://example.com/demo",
        "new_artifact": "并发演示链接 " + task[:8], "about": "演示同一任务两次递卡用的一个链接"}
out = {}
def file(i):
    t0 = time.time(); out[i] = (*call("POST", f"/topics/{task}/accept-card", card), round(time.time() - t0, 1))
a = threading.Thread(target=file, args=(1,)); a.start(); time.sleep(1)
b = threading.Thread(target=file, args=(2,)); b.start(); a.join(); b.join()
flag.unlink(missing_ok=True)
for i in (1, 2):
    s, body, secs = out[i]
    print(f"filing {i}: HTTP {s} in {secs}s ->", (body.get("data") or {}).get("id") or body.get("message"))
