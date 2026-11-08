// 真浏览器打开隔离开发栈的频道首屏，截任务卡，读出两件任务的状态记号。
import { chromium } from '/tmp/pw/node_modules/playwright/index.mjs';
const [front, pid, room, out] = process.argv.slice(2);
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
page.on('pageerror', (e) => console.error('pageerror', e.message));
await page.goto(front + '/');
const login = await page.evaluate(async () => {
  const r = await fetch('/api/users/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username: 'alice', password: 'demo12345' }) });
  const j = await r.json();
  localStorage.setItem('accessToken', j.data.accessToken);
  localStorage.setItem('user', JSON.stringify(j.data.user));
  return r.status;
});
console.log('login', login);
// 只看首屏那一路：挡掉实时长连接，免得订阅快照之后把卡片重画成别的来源。
await page.routeWebSocket(/.*/, () => {});
const blocks = page.waitForResponse((r) => /\/api\/topics\/[^/]+\/blocks/.test(r.url()));
await page.goto(`${front}/projects/${pid}/topics/${room}`);
const b = await (await blocks).json();
for (const blk of b.data.data) for (const t of blk.tasks ?? []) if (t.title.startsWith('首屏标记'))
  console.log('blocks', t.title, t.presentation.phrase, 'session_running' in t ? t.session_running : '(缺)');
await page.getByText('首屏标记·会话在跑（模拟）').first().waitFor({ timeout: 30000 });
await page.waitForTimeout(1500);
for (let round = 0; round < 6; round++) {
  for (const name of ['Agree and continue', 'Later', 'Skip the tour']) {
    const b = page.getByText(name, { exact: true }).first();
    if (await b.isVisible().catch(() => false)) { await b.click().catch(() => {}); await page.waitForTimeout(1000); }
  }
}
// 收起右侧概览栏，给消息流里的任务卡腾出宽度（右栏用的是项目级列表，不是这次要看的入口）。
await page.getByText('Overview', { exact: true }).last().click().catch(() => {});
await page.waitForTimeout(1200);
const marks = await page.evaluate(() => [...document.querySelectorAll('.im-row')]
  .filter((el) => /首屏标记/.test(el.textContent || '')).map((el) => ({ text: (el.textContent || '').trim().slice(0, 60), icons: [...el.querySelectorAll('i.mdi, .mdi')].map((i) => [...i.classList].filter((c) => c.startsWith('mdi-')).join(' ')) })));
console.log(JSON.stringify(marks, null, 1));
const card = page.getByText('首屏标记·会话在跑（模拟）').first();
await card.scrollIntoViewIfNeeded();
await page.screenshot({ path: out });
const rows = page.locator('.im-row').filter({ hasText: '首屏标记' });
const a = await rows.first().boundingBox(); const z = await rows.last().boundingBox();
await page.screenshot({ path: out.replace('.png', '-cards.png'), clip: { x: a.x - 8, y: a.y - 8, width: Math.max(a.width, z.width) + 16, height: z.y + z.height - a.y + 16 } });
await browser.close();
