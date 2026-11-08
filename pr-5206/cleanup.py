"""Close every open task in the demo room so a fresh run is the only thing on screen."""
import json, urllib.request
B = "http://127.0.0.1:18181"; ROOM = "ae1a7cc2-e0eb-4962-b9da-02b45d76aa2d"; PID = "4eb748cb-c2f4-4423-a793-74dfded56910"
def call(m, p, body=None, tok=None):
    h = {"content-type": "application/json"}
    if tok: h["authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(B + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
tok = call("POST", "/users/auth/login", {"username": "alice", "password": "demo12345"})[1]["data"]["accessToken"]
s, d = call("GET", f"/projects/{PID}/tasks?status=open&limit=100", tok=tok)
for t in d["data"]["data"]:
    if t["room_id"] == ROOM:
        print(t["title"], call("POST", f"/topics/{t['id']}/close", {"conclusion": "演示数据清理"}, tok=tok)[0])
