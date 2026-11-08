import { chromium } from 'playwright'
const F = 'http://127.0.0.1:13100', PID = '4eb748cb-c2f4-4423-a793-74dfded56910', ROOM = 'ae1a7cc2-e0eb-4962-b9da-02b45d76aa2d'
const shots = [
  ['/inbox', '/tmp/fb108-demo/inbox-before-and-after.png'],
  [`/projects/${PID}/topics/${ROOM}/tasks/78067966-7dd4-4b2b-8302-2a686f69c362`, '/tmp/fb108-demo/before-task-two-cards.png'],
  [`/projects/${PID}/topics/${ROOM}/tasks/d336cf17-0f1e-4aff-93f2-5ec8e4500699`, '/tmp/fb108-demo/after-task-one-card.png'],
]
const browser = await chromium.launch({ executablePath: '/tmp/fb108-pw/browsers/chromium_headless_shell-1254/chrome-headless-shell-linux64/chrome-headless-shell' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN' })
await page.goto(F + '/login')
await page.evaluate(async () => {
  const r = await fetch('/api/users/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'alice', password: 'demo12345' }) })
  const d = (await r.json()).data
  localStorage.setItem('accessToken', d.accessToken); localStorage.setItem('user', JSON.stringify(d.user))
})
for (const [path, out] of shots) {
  await page.goto(F + path); await page.waitForTimeout(9000)
  const agree = page.getByText('同意并继续'); if (await agree.count()) { await agree.click(); await page.waitForTimeout(2000); await page.reload(); await page.waitForTimeout(9000) }
  await page.screenshot({ path: out }); console.log('shot', out)
}
await browser.close()
