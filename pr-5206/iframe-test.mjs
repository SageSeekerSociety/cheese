// Drive the entry the way the platform preview shows it: inside an iframe.
import { chromium } from 'playwright'
import http from 'node:http'
const parent = http.createServer((q, s) => { s.writeHead(200, { 'content-type': 'text/html' }); s.end(`<iframe id=f src="http://127.0.0.1:13200${q.url}" style="width:1400px;height:860px;border:0"></iframe>`) }).listen(13299)
const browser = await chromium.launch({ executablePath: '/tmp/fb108-pw/browsers/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell' })
const page = await browser.newPage({ viewport: { width: 1420, height: 880 }, locale: 'zh-CN' })
// 1) root -> inbox
await page.goto('http://127.0.0.1:13299/'); await page.waitForTimeout(12000)
let f = page.frames().find((x) => x.url().includes('13200'))
console.log('root ->', f.url())
const agree = f.getByText('同意并继续'); if (await agree.count()) { await agree.click(); await page.waitForTimeout(3000) }
await f.goto('http://127.0.0.1:13200/'); await page.waitForTimeout(10000)
f = page.frames().find((x) => x.url().includes('13200'))
console.log('reload root ->', f.url(), (await f.evaluate(() => document.body.innerText)).includes('等你处理'))
// 2) console in iframe, click before, wait, click after
await f.goto('http://127.0.0.1:13200/__fb108'); await page.waitForTimeout(1500)
await f.fill('#delay', '3')
await f.getByText('改前代码：同时递两次卡').click(); await page.waitForTimeout(1000)
console.log('status:', await f.textContent('#status'))
await f.waitForFunction(() => !document.querySelector('#status').textContent, null, { timeout: 60000 })
await f.getByText('修后代码：同时递两次卡').click(); await page.waitForTimeout(1000)
await f.waitForFunction(() => !document.querySelector('#status').textContent, null, { timeout: 60000 })
console.log(await f.textContent('#runs'))
console.log('url still', f.url())
await browser.close(); parent.close()
