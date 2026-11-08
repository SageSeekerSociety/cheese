import sys, asyncio, time, json, os, glob, shutil
from playwright.async_api import async_playwright
label, title = sys.argv[1], sys.argv[2]
out = f'/tmp/r15vid/{label}'
async def main():
    shutil.rmtree(out + '-raw', ignore_errors=True)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path='/tmp/pw/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell', args=['--no-sandbox'])
        ctx = await b.new_context(viewport={'width': 1100, 'height': 720}, locale='zh-CN', record_video_dir=out + '-raw', record_video_size={'width': 1100, 'height': 720})
        pg = await ctx.new_page()
        log = []; t0 = time.time()
        def mark(s): log.append(f'{time.time()-t0:5.2f}s {s}')
        pg.on('request', lambda r: mark(f'request {r.method} {r.url.split("47815")[1]}') if '/say' in r.url else None)
        pg.on('response', lambda r: mark(f'response {r.status} {r.url.split("47815")[1]}') if '/say' in r.url else None)
        errs = []; pg.on('pageerror', lambda e: errs.append(str(e)))
        import urllib.parse; url = 'http://127.0.0.1:47815/?v=' + urllib.parse.quote(title)
        await pg.goto(url, wait_until='load')
        await pg.wait_for_selector('text=旧的一句', timeout=30000); mark('page shows 旧的一句')
        await pg.wait_for_timeout(2000)
        box = pg.locator('textarea, [contenteditable="true"]').last
        await box.click(); await pg.keyboard.type('补一句', delay=250); mark('typed 补一句')
        await pg.wait_for_timeout(800)
        await pg.keyboard.press('Enter'); mark('pressed Enter')
        await pg.wait_for_timeout(700)
        users_temp = await pg.inner_text('#acc-users'); mark(f'while waiting: users={users_temp}')
        await pg.screenshot(path=f'{out}-temp.png')
        await pg.wait_for_function("document.querySelector('#acc-users') && !document.querySelector('#acc-users').innerText.includes('补一句')", timeout=15000)
        await pg.wait_for_timeout(300)
        users_after = await pg.inner_text('#acc-users'); verdict = await pg.inner_text('#acc-verdict'); mark(f'after 500: users={users_after} verdict={verdict}')
        await pg.screenshot(path=f'{out}-after.png')
        await pg.wait_for_timeout(3000)
        await ctx.close(); await b.close()
        vid = glob.glob(out + '-raw/*.webm')[0]; shutil.move(vid, f'{out}.webm')
        print(json.dumps({'label': label, 'url': url, 'log': log, 'errors': errs}, ensure_ascii=False, indent=1))
asyncio.run(main())
