// 在途换账号：alice 交文件时把她「查自己资料库」的请求扣住 → 页面内退出 → 登录 bobby、交文件 → 放行 alice 那次请求。
import { chromium } from 'playwright'
import { writeFileSync } from 'node:fs'
const TAG = process.argv[2] || 'run'
const BASE = 'http://127.0.0.1:23000'
const shot = (p, n) => p.screenshot({ path: `/tmp/f5194/shots/inflight-${TAG}-${n}.png` })
const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' })
const log = []
p.on('request', (r) => {
  const u = r.url().replace(BASE, '')
  if (/\/api\/teams\//.test(u) && !/\/my-teams/.test(u)) {
    const who = (r.headers()['authorization'] || '').slice(7)
    let as = '?'; try { as = JSON.parse(Buffer.from(who.split('.')[1], 'base64url').toString()).username ?? JSON.parse(Buffer.from(who.split('.')[1], 'base64url').toString()).sub } catch {}
    log.push(`${r.method()} ${u}  （凭据：用户 ${as}）`)
  }
})
p.on('response', (r) => { const u = r.url().replace(BASE, ''); if (/\/api\/teams\/\d+\/library$/.test(u) && r.request().method() === 'POST') log.push(`  → ${r.status()} ${u}`) })
let release
const held = new Promise((r) => (release = r))
await p.route('**/api/teams/by-handle/alice', async (route) => { await held; await route.continue() })
async function signIn(user) {
  await p.fill('input[name=username]', user); await p.fill('input[name=password]', 'demo12345')
  await p.getByRole('button', { name: '登录', exact: true }).click(); await p.waitForTimeout(2500)
  const agree = p.getByRole('button', { name: '同意并继续' }); if (await agree.isVisible()) { await agree.click(); await p.waitForTimeout(1500) }
}
async function attach(name) {
  await p.getByRole('button', { name: '问芝士' }).click(); await p.waitForTimeout(1200)
  await p.locator('[data-testid=agent-host]').first().locator('input[type=file]').first()
    .setInputFiles({ name, mimeType: 'text/plain', buffer: Buffer.from(`安全测试数据 ${name}\n`) })
}
await p.goto(`${BASE}/account/signin`, { waitUntil: 'networkidle' }); await signIn('alice')
await p.goto(`${BASE}/`, { waitUntil: 'networkidle' })
await p.evaluate(() => { window.__samePage = 'never-reloaded' })
log.push(`--- alice 交 alice-${TAG}.txt，扣住她查资料库的请求`)
await attach(`alice-${TAG}.txt`); await p.waitForTimeout(1500); await shot(p, '1-alice-pending')
log.push('--- 页面内退出登录，登录 bobby')
await p.getByRole('button', { name: '问芝士' }).click().catch(() => {})
await p.getByRole('button', { name: 'Alice' }).last().click(); await p.waitForTimeout(600)
await p.getByText('退出登录', { exact: true }).first().click()
await p.getByRole('button', { name: 'Alice' }).waitFor({ state: 'detached', timeout: 20000 }); await p.waitForTimeout(1500)
if (!p.url().includes('signin')) { await p.locator('a[href*="signin"]').first().click(); await p.waitForURL(/signin/, { timeout: 10000 }); await p.waitForTimeout(800) }
await signIn('bobby')
await p.getByRole('link', { name: '知是首页' }).first().click().catch(() => {}); await p.waitForTimeout(3000)
log.push(`--- bobby 交 bobby-${TAG}.txt`)
await attach(`bobby-${TAG}.txt`); await p.waitForTimeout(3500)
log.push('--- 放行 alice 那次查询')
release(); await p.waitForTimeout(5000); await shot(p, '2-after-release')
log.push(`same page (no reload): ${await p.evaluate(() => window.__samePage)}`)
await p.unroute('**/api/teams/by-handle/alice')
for (const u of ['alice', 'bobby']) {
  const tok = await p.evaluate(async (u) => { const r = await fetch('/api/users/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: u, password: 'demo12345' }) }); return (await r.json()).data.accessToken }, u)
  const team = await p.evaluate(async ([u, tok]) => (await (await fetch(`/api/teams/by-handle/${u}`, { headers: { authorization: `Bearer ${tok}` } })).json()).data.team.id, [u, tok])
  const files = await p.evaluate(async ([team, tok]) => JSON.stringify((await (await fetch(`/api/teams/${team}/library`, { headers: { authorization: `Bearer ${tok}` } })).json()).data), [team, tok])
  log.push(`${u} 的资料库（团队 ${team}）里的 ${TAG} 文件：${(files.match(new RegExp(`[a-z]+-${TAG}\\.txt`, 'g')) || []).join(', ') || '无'}`)
}
writeFileSync(`/tmp/f5194/inflight-${TAG}-log.txt`, log.join('\n') + '\n'); console.log(log.join('\n'))
await b.close()
