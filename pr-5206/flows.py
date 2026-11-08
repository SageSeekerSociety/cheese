"""Step-by-step flows on the PR-head backend (a5118f39): read then re-file, redeliver the original card, reject then re-file.

usage: python3 -I flows.py <step>   steps: a1 a2 a3 a4 b1 b2 b3 reset
Prints one JSON record per step and keeps task/card ids in flow-state.json.
"""
import json, sys, time, pathlib, urllib.request, datetime
B = "http://127.0.0.1:18381"          # 修后后端 = PR head a5118f39
PID = "4eb748cb-c2f4-4423-a793-74dfded56910"; ROOM = "ae1a7cc2-e0eb-4962-b9da-02b45d76aa2d"
STATE = pathlib.Path("/tmp/fb108-demo/flow-state.json")
LOG = pathlib.Path("/tmp/fb108-demo/flow-log.jsonl")
def now(): return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=8)).strftime("%H:%M:%S")
def call(m, p, body=None, tok=None):
    h = {"content-type": "application/json"}
    if tok: h["authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
def login(u): return call("POST", "/users/auth/login", {"username": u, "password": "demo12345"})[1]["data"]["accessToken"]
def cards(task, tok):
    s, d = call("GET", f"/topics/{task}/accept-card", tok=tok)
    return [{"card": c["id"][:8], "status": c["status"], "deliverable_url": (c.get("deliverable") or {}).get("url"), "created_at": c["created_at"][11:19]} for c in d["data"]["data"]]
def version(task, tok):
    s, d = call("GET", f"/projects/{PID}/tasks?status=open&limit=100", tok=tok)
    for t in d["data"]["data"]:
        if t["id"] == task: return (t.get("card") or {}).get("version")
def file(task, url, tok, name):
    t0 = time.time()
    s, d = call("POST", f"/topics/{task}/accept-card", {"reviewer_handle": "bobby", "change_subject": "docs: hand in the demo link",
        "deliver_url": url, "new_artifact": name, "about": "演示读卡、换交付物、退回重交用的一个链接"}, tok)
    return {"http": s, "secs": round(time.time() - t0, 1), "card": ((d.get("data") or {}).get("id") or "")[:8] or None, "message": d.get("message") if s >= 300 else None}
def notices(tok, n=3):
    s, d = call("GET", "/notifications?limit=10", tok=tok)
    rows = d["data"]["notifications"]
    return [{"type": r.get("type"), "content": (r.get("contextMetadata") or {}).get("content"), "event": (r.get("contextMetadata") or {}).get("eventType"),
             "at": datetime.datetime.fromtimestamp(r["createdAt"] / 1000, datetime.UTC).strftime("%H:%M:%S") + " UTC", "read": r.get("read")} for r in rows[:n]]
FLAG = pathlib.Path("/tmp/fb108-forge-delay")
FLAG.write_text("0")  # forge 替身：读分支差异不延迟、返回固定差异；本步结束删掉
import atexit; atexit.register(lambda: FLAG.unlink(missing_ok=True))
st = json.loads(STATE.read_text()) if STATE.exists() else {}
step = sys.argv[1]; alice = login("alice"); bob = login("bobby"); rec = {"step": step, "at": now() + "（北京）", "backend": "a5118f39", "forge": "替身，延迟 0 秒，返回固定差异"}
if step == "reset":
    STATE.unlink(missing_ok=True); LOG.unlink(missing_ok=True); print(json.dumps({"reset": True})); sys.exit()
if step in ("a1", "b1"):
    k = step[0]; title = ("读卡后再递 " if k == "a" else "退回后重交 ") + now()
    s, t = call("POST", f"/topics/{ROOM}/tasks", {"title": title}, alice); task = t["data"]["id"]; st[k] = task
    rec.update(task=task[:8], title=title, request="POST /topics/<任务>/accept-card（交 https://example.com/v1）", result=file(task, "https://example.com/v1", alice, f"演示链接 {task[:8]}"))
elif step == "a2":
    rec.update(task=st["a"][:8], request="（验收人 bobby 在产品页打开这条任务读卡；本步只读状态）")
elif step == "a3":
    rec.update(task=st["a"][:8], request="POST /topics/<任务>/accept-card（交付更新为 https://example.com/v2，再递一张）", result=file(st["a"], "https://example.com/v2", alice, f"演示链接二 {st['a'][:8]}"))
elif step == "a4":
    s, d = call("POST", f"/topics/{st['a']}/accept-card/redeliver", {"deliver_url": "https://example.com/v2"}, alice)
    rec.update(task=st["a"][:8], request="POST /topics/<任务>/accept-card/redeliver（原卡换成 https://example.com/v2）", result={"http": s, "card": ((d.get("data") or {}).get("id") or "")[:8] or None, "message": d.get("message") if s >= 300 else None})
elif step == "b2":
    cid = call("GET", f"/topics/{st['b']}/accept-card", tok=alice)[1]["data"]["data"][0]["id"]
    s, d = call("POST", f"/accept-cards/{cid}/reject", {"decided_by": "bobby", "note": "演示：退回重做"}, bob)
    rec.update(task=st["b"][:8], request=f"POST /accept-cards/{cid[:8]}…/reject（bobby 退回）", result={"http": s, "message": d.get("message") if s >= 300 else None})
elif step == "b3":
    rec.update(task=st["b"][:8], request="POST /topics/<任务>/accept-card（退回后再递，交 https://example.com/v2）", result=file(st["b"], "https://example.com/v2", alice, f"演示链接二 {st['b'][:8]}"))
k = "a" if step.startswith("a") else "b"
rec["cards_now"] = cards(st[k], alice); rec["inbox_version"] = version(st[k], bob)
if step in ("a4", "b2", "b3", "a1", "b1"): rec["bobby_notifications"] = notices(bob)
STATE.write_text(json.dumps(st)); LOG.open("a").write(json.dumps(rec, ensure_ascii=False) + "\n")
print(json.dumps(rec, ensure_ascii=False, indent=1))
