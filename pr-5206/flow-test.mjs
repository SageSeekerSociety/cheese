// Click the step buttons inside an iframe like the preview does, open the task as bobby, and shoot the console.
import { chromium } from 'playwright'
import http from 'node:http'
const parent = http.createServer((q, s) => { s.writeHead(200, { 'content-type': 'text/html' }); s.end(`<iframe src="http://127.0.0.1:13200${q.url}" style="width:1400px;height:2400px;border:0"></iframe>`) }).listen(13298)
const browser = await chromium.launch({ executablePath: '/tmp/fb108-pw/browsers/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell' })
const page = await browser.newPage({ viewport: { width: 1420, height: 2420 }, locale: 'zh-CN' })
await page.goto('http://127.0.0.1:13298/__fb108'); await page.waitForTimeout(2000)
const f = () => page.frames().find((x) => x.url().includes('13200'))
const step = async (s) => { await f().click(`button[data-step="${s}"]`); await page.waitForTimeout(800); await f().waitForFunction(() => !document.querySelector('#status').textContent, null, { timeout: 60000 }); await page.waitForTimeout(1600) }
for (const s of ['reset', 'a1']) await step(s)
// bobby reads A in a second tab through the entry link
const href = await f().getAttribute('#a2tip a', 'href'); console.log('bobby link', href)
const p2 = await browser.newPage({ viewport: { width: 1680, height: 900 }, locale: 'zh-CN' })
await p2.goto('http://127.0.0.1:13200' + href); await p2.waitForTimeout(12000)
const agree = p2.getByText('同意并继续'); if (await agree.count()) { await agree.click(); await p2.waitForTimeout(2000); await p2.goto('http://127.0.0.1:13200' + href); await p2.waitForTimeout(10000) }
await p2.getByText('改动', { exact: true }).first().click(); await p2.waitForTimeout(3000)
console.log('bobby sees:', (await p2.evaluate(() => document.body.innerText)).replace(/\s+/g, ' ').match(/(Bob|B Bob).{0,0}|第 \d 版|https:\/\/example\.com\/v\d/g))
await p2.screenshot({ path: '/tmp/fb108-demo/hands-on-a2-bobby-reads.png' }); await p2.close()
for (const s of ['a2', 'a3', 'a4', 'b1', 'b2', 'b3']) await step(s)
await page.screenshot({ path: '/tmp/fb108-demo/hands-on-console-flows.png' })
console.log((await f().textContent('#flows')).replace(/\s+/g, ' ').slice(0, 1500))
await browser.close(); parent.close()
