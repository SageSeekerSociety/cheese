// 真浏览器走一遍：alice 交文件 → 页面内退出 → 不刷新页面登录 bobby → 交文件；记录每次上传进了哪个团队。
import { chromium } from 'playwright'
import { writeFileSync } from 'node:fs'
const TAG = process.argv[2] || 'run'
const BASE = 'http://127.0.0.1:23000'
const shot = (p, n) => p.screenshot({ path: `/tmp/f5194/shots/${TAG}-${n}.png` })
const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' })
const log = []
p.on('request', r => { const u = r.url(); if (u.includes('/api/') && /teams\/by-handle|librar/.test(u) && r.method() !== 'GET' || /teams\/by-handle/.test(u)) log.push(`${r.method()} ${u.replace(BASE, '')}`) })
async function signIn(user) {
  await p.fill('input[name=username]', user); await p.fill('input[name=password]', 'demo12345')
  await p.getByRole('button', { name: '登录', exact: true }).click(); await p.waitForTimeout(2500)
  const agree = p.getByRole('button', { name: '同意并继续' }); if (await agree.isVisible()) { await agree.click(); await p.waitForTimeout(1500) }
}
async function attach(name, n) {
  await p.getByRole('button', { name: '问芝士' }).click(); await p.waitForTimeout(1200)
  const host = p.locator('[data-testid=agent-host]').first()
  await host.locator('input[type=file]').first().setInputFiles({ name, mimeType: 'text/plain', buffer: Buffer.from(`安全测试数据 ${name}\n`) })
  await p.waitForTimeout(3500); await shot(p, n)
}
await p.goto(`${BASE}/account/signin`, { waitUntil: 'networkidle' })
await signIn('alice')
await p.goto(`${BASE}/`, { waitUntil: 'networkidle' })        // 只有这一次整页加载
await p.evaluate(() => { window.__samePage = 'never-reloaded' })
log.push(`--- alice 交 alice-${TAG}.txt`)
await attach(`alice-${TAG}.txt`, '1-alice-attached')
log.push('--- 页面内退出登录')
await p.getByRole('button', { name: '问芝士' }).click().catch(() => {})
await p.getByRole('button', { name: 'Alice' }).last().click(); await p.waitForTimeout(600)
await p.getByText('退出登录', { exact: true }).first().click()
await p.getByRole('button', { name: 'Alice' }).waitFor({ state: 'detached', timeout: 20000 }); await p.waitForTimeout(1500)
await shot(p, '2-signed-out'); log.push(`url after sign-out: ${p.url().replace(BASE, '')}`)
if (!p.url().includes('signin')) {
  const entry = p.locator('a[href*="signin"], button:has-text("登录"), a:has-text("登录")').first()
  log.push(`sign-in entry: ${await entry.evaluate(e => e.outerHTML.slice(0, 120))}`)
  await entry.click(); await p.waitForURL(/signin/, { timeout: 10000 }); await p.waitForTimeout(800)
}
await shot(p, '2b-signin-page')
await signIn('bobby')
await p.getByRole('link', { name: '知是首页' }).first().click().catch(() => {}); await p.waitForTimeout(3000)
log.push(`url after bobby sign-in: ${p.url().replace(BASE, '')}`)
log.push(`--- bobby 交 bobby-${TAG}.txt（同一页面，没有刷新）`)
await attach(`bobby-${TAG}.txt`, '3-bobby-attached')
log.push(`same page (no reload): ${await p.evaluate(() => window.__samePage)}`)
// 以下可以整页加载了：分别看两人的资料库里有什么
await p.goto(`${BASE}/teams/bobby/library`, { waitUntil: 'networkidle' }); await p.waitForTimeout(1500); await shot(p, '4-bobby-library')
log.push(`bobby library has: ${(await p.locator('main').innerText()).split('\n').filter(l => /\.txt/.test(l)).join(', ')}`)
writeFileSync(`/tmp/f5194/${TAG}-log.txt`, log.join('\n') + '\n'); console.log(log.join('\n'))
await b.close()
