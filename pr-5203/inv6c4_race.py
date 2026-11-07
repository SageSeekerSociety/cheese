import asyncio, sys, httpx
B = "http://127.0.0.1:18381"
TEAM, USER, ROUNDS = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
async def main():
    async with httpx.AsyncClient(base_url=B, timeout=30) as c:
        t = (await c.post("/users/auth/login", json={"username": "alice", "password": "demo12345"})).json()["data"]["accessToken"]
        H = {"Authorization": f"Bearer {t}"}
        async def pend():
            r = (await c.get(f"/teams/{TEAM}/invitations", params={"status": "PENDING"}, headers=H)).json()["data"]["invitations"]
            return [i["id"] for i in r if i["user"]["id"] == USER]
        dup = 0
        for k in range(ROUNDS):
            for i in await pend():
                await c.delete(f"/teams/{TEAM}/invitations/{i}", headers=H)
            rs = await asyncio.gather(*[c.post(f"/teams/{TEAM}/invitations", json={"userId": USER, "role": "MEMBER"}, headers=H) for _ in range(2)])
            p = await pend()
            if len(p) > 1: dup += 1
            print(f"round {k+1}: statuses={[r.status_code for r in rs]} pending_rows={len(p)}")
        print(f"rounds with two pending invitations for the same person: {dup}/{ROUNDS}")
        # leave the last round's rows in place for the screenshot
asyncio.run(main())
