// args: <user> <path> <out.png> [textToWaitFor] [clickText]
import { chromium } from 'playwright'
const [user, path, out, wait, click] = process.argv.slice(2)
const F = 'http://127.0.0.1:13100'
const browser = await chromium.launch({ executablePath: '/tmp/fb108-pw/browsers/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell' })
const page = await browser.newPage({ viewport: { width: 1680, height: 900 }, locale: 'zh-CN' })
await page.goto(F + '/login')
await page.evaluate(async (u) => {
  const r = await fetch('/api/users/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: u, password: 'demo12345' }) })
  const d = (await r.json()).data
  localStorage.setItem('accessToken', d.accessToken); localStorage.setItem('user', JSON.stringify(d.user))
}, user)
await page.goto(F + path); await page.waitForTimeout(6000)
const agree = page.getByText('同意并继续'); if (await agree.count()) { await agree.click(); await page.waitForTimeout(2000); await page.goto(F + path); await page.waitForTimeout(6000) }
if (wait) await page.waitForFunction((t) => document.body.innerText.includes(t), wait, { timeout: 60000 }).catch(() => console.log('WAIT TIMEOUT', wait))
if (click) { await page.getByText(click, { exact: false }).first().click().catch((e) => console.log('CLICK FAIL', click)); await page.waitForTimeout(4000) }
await page.waitForTimeout(1500)
await page.screenshot({ path: out })
console.log(out, '|', (await page.evaluate(() => document.body.innerText)).replace(/\s+/g, ' ').slice(0, 600))
await browser.close()
