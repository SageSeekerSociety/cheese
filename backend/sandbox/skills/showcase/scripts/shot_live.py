"""给运行中的页面截图 —— `cheese check` 管摆出来的 HTML 文件，这个管活着的页面。

用法:

    uv run --with playwright python scripts/shot_live.py <url> <out.png> [选项]

选项:
    --api URL      cheese 后端基址（登录拿 token 用），默认 http://localhost:8081
    --user NAME    用户名；给了就先用 API 登录，把 token 注入 localStorage 再截图
    --password PW  密码（配合 --user）
    --token TOK    已有访问 token 就直接给，跳过 API 登录
    --width PX     视口宽，默认 1360；--height PX 默认 860
    --wait MS      等页面稳定的毫秒数，默认 3500（SPA 首屏要这个时间）
    --click TEXT   页面稳定后点一颗带这个文字的控件（按钮/链接），再静一拍截图；
                   弹窗、页签这类要点才出来的界面用它。点不到不报错，只印一行提示
    --headless     后台跑、不弹窗口。**默认弹窗口（有头）**——要给人看着它跑
    --slow MS      每步慢放毫秒数；默认有头 250、无头 0
    --hold MS      截完图停住多久再关窗，给人看最后一屏；默认有头 2500、无头 0

浏览器优先用系统 Chrome（不下载任何东西）；没有 Chrome 退回 playwright 自带
chromium，首次缺失会自动装一次（约 150MB，只此一回）。
"""

import argparse
import asyncio
import json
import sys
import urllib.request


def login(api: str, user: str, password: str) -> tuple[str, dict]:
    req = urllib.request.Request(
        api.rstrip("/") + "/users/auth/login",
        data=json.dumps({"username": user, "password": password}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)["data"]
    token = data["accessToken"]
    who = data.get("user") or {"id": 0, "username": user, "nickname": user}
    return token, who


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("out")
    p.add_argument("--api", default="http://localhost:8081")
    p.add_argument("--user", default="")
    p.add_argument("--password", default="")
    p.add_argument("--token", default="")
    p.add_argument("--width", type=int, default=1360)
    p.add_argument("--height", type=int, default=860)
    p.add_argument("--wait", type=int, default=3500)
    p.add_argument("--click", default="")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--slow", type=int, default=None)
    p.add_argument("--hold", type=int, default=None)
    a = p.parse_args()

    # 默认有头：这个脚本是给人看着跑的（芝士改界面时，人要看它真在点那个页面）。
    visible = not a.headless
    slow = a.slow if a.slow is not None else (250 if visible else 0)
    hold = a.hold if a.hold is not None else (2500 if visible else 0)

    token, who = a.token, None
    if a.user and not token:
        token, who = login(a.api, a.user, a.password)
        print(f"[shot_live] 已登录 {a.user}")

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(
                channel="chrome", headless=not visible, slow_mo=slow
            )
        except Exception:
            try:
                browser = await pw.chromium.launch(headless=not visible, slow_mo=slow)
            except Exception:
                # ponytail: 无头机/第一次用才走到这；装一次后这行永远不触发
                print("[shot_live] 没有可用浏览器，装一次 chromium（只此一回）…")
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "playwright", "install", "chromium"
                )
                if await proc.wait() != 0:
                    print("[shot_live] chromium 装不上", file=sys.stderr)
                    return 1
                browser = await pw.chromium.launch(headless=not visible, slow_mo=slow)
        page = await browser.new_page(viewport={"width": a.width, "height": a.height})
        if token:
            who = who or {"id": 0, "username": "", "nickname": ""}
            # 登录取自 API，注入 localStorage 让 SPA 认为已登录（两层 HTTP 栈都读它）。
            # 值用 json.dumps 拼进脚本：add_init_script 没有传参那一说。
            seed = json.dumps({"tok": token, "u": who}, ensure_ascii=False)
            await page.add_init_script(
                "(() => { const d = " + seed + ";"
                " localStorage.setItem('accessToken', d.tok);"
                " localStorage.setItem('user', JSON.stringify(d.u));"
                " localStorage.setItem('cheesex.me', JSON.stringify("
                "{ id: String(d.u.id), handle: d.u.username, token: d.tok })); })()"
            )
        await page.goto(a.url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(a.wait)
        if a.click:
            try:
                await page.get_by_text(a.click, exact=True).first.click(timeout=5000)
                await page.wait_for_timeout(1600)
            except Exception as exc:
                print(f"[shot_live] 点不到「{a.click}」: {exc.__class__.__name__}")
        await page.screenshot(path=a.out)
        print(f"[shot_live] 截图 {a.out} | 最终 URL: {page.url}")
        if "login" in page.url and token:
            print("[shot_live] 被弹回登录页：token 没生效或已过期", file=sys.stderr)
        if hold:
            await page.wait_for_timeout(hold)
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
