// 用法：node repro.cjs <端口> <标签>。真实运行的应用（本地 vite，其余接口全部反代到 okcheese.com 生产后端，
// 开发号登录）；只把芝士对话 /api/assistant/conversations* 与 /api/assistant/routines 换成页面内的可控替身。
const { chromium } = require('playwright');
const [port, label] = process.argv.slice(2);
const DELAY = 8000; // 人为延迟：回答第一段之后停 8 秒再写完
// 截图遮罩：中间那一栏是开发号自己的待办（内部项目内容），仓库公开，截图时用灰块盖住；只盖不改页面。
async function centre(page) {
  await page.evaluate(() => {
    document.querySelectorAll('[data-shot-mask]').forEach((e) => e.removeAttribute('data-shot-mask'));
    let e = document.elementFromPoint(700, 450);
    while (e && e.parentElement) { const r = e.parentElement.getBoundingClientRect(); if (r.width > 760 || r.left < 300) break; e = e.parentElement; }
    if (e) e.setAttribute('data-shot-mask', '');
  });
  return [page.locator('[data-shot-mask]')];
}
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' });
  const page = await ctx.newPage();
  const base = `http://127.0.0.1:${port}`;
  await page.goto(base + '/');
  const login = await page.evaluate(async (cred) => {
    const r = await fetch('/api/users/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cred) });
    return (await r.json()).data;
  }, { username: process.env.U, password: process.env.P });
  await ctx.route('**/api/users/auth/refresh', (r) => r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ code: 200, message: 'ok', data: login }) }));
  await page.evaluate((d) => { localStorage.accessToken = d.accessToken; localStorage.user = JSON.stringify(d.user); localStorage.setItem('cheesex.agentDocked', 'true') }, login);
  await ctx.addInitScript(({ DELAY }) => {
    const real = window.fetch.bind(window);
    const env = (data) => new Response(JSON.stringify({ code: 200, message: 'ok', data }), { status: 200, headers: { 'content-type': 'application/json' } });
    const Q = { role: 'user', text: '帮我把这周还没做完的任务列一下', at: '2026-11-07T15:00:00Z', seq: 1 };
    const A1 = '这周还没做完的有三件：', A2 = '一、首页侧栏头像；二、采纳卡片宽度；三、文档浮动条。';
    let t0 = null; // 回答开始的时刻：第一次有人读这个回答
    const finished = () => t0 !== null && Date.now() - t0 >= DELAY;
    window.__answerReads = 0;
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      const method = (init?.method ?? 'GET').toUpperCase();
      if (/\/api\/assistant\/routines$/.test(url)) return env({ limit: 20, routines: [{ id: 'r1', title: '盘点这周的任务', note: '盘点这周还没做完的任务', when: '等这周的任务盘点完', state: 'active', paused: '', nextRunAt: null, once: true, conversationId: 'c1', missing: [], trigger: '', spec: {}, timezone: 'Asia/Shanghai', lastRun: null, weekCredits: 0 }] });
      if (/\/api\/assistant\/conversations(\?.*)?$/.test(url) && method === 'GET') return env({ conversations: [{ id: 'c1', title: '这周的任务', lastActiveAt: '2026-11-07T15:00:00Z', questions: 1 }], next: null });
      if (/\/api\/assistant\/conversations\/c1$/.test(url)) return env(finished()
        ? { id: 'c1', title: '这周的任务', lastActiveAt: '2026-11-07T15:00:00Z', questions: 1, messages: [Q, { role: 'assistant', text: A1 + A2, at: '2026-11-07T15:00:09Z', seq: 2 }], answering: null }
        : { id: 'c1', title: '这周的任务', lastActiveAt: '2026-11-07T15:00:00Z', questions: 1, messages: [Q], answering: 'q1' });
      if (/\/api\/assistant\/conversations\/c1\/(looking|heard)/.test(url)) return env({});
      if (/\/api\/assistant\/conversations\/c1\/answers\/q1/.test(url)) {
        window.__answerReads++;
        if (t0 === null) t0 = Date.now();
        const enc = new TextEncoder();
        const frame = (ev, data, id) => enc.encode(`id: ${id}\nevent: ${ev}\ndata: ${JSON.stringify(data)}\n\n`);
        const body = new ReadableStream({ async start(c) {
          c.enqueue(frame('delta', { text: A1, at: 0 }, '1-0'));
          const wait = t0 + DELAY - Date.now();
          if (wait > 0) await new Promise((r) => setTimeout(r, wait));
          c.enqueue(frame('delta', { text: A2, at: A1.length }, '2-0'));
          c.enqueue(frame('done', { stopped: false }, '3-0'));
          c.close();
        } });
        return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
      }
      if (url.includes('/api/assistant/conversations')) return env({});
      return real(input, init);
    };
  }, { DELAY });
  await page.goto(base + '/users/settings/assistant');
  await page.waitForLoadState('networkidle').catch(() => {});
  // 1. 设置页「例行事务」里点「查看对话」：面板打开 c1，c1 正在答 q1，面板接上这条回答
  const view = page.getByRole('button', { name: '查看对话' }).first();
  await view.click();
  await page.getByText('这周还没做完的有三件：').first().waitFor({ timeout: 15000 });
  // 2. 回答还在写：再点一次「查看对话」（重开同一段对话）
  await page.waitForTimeout(500);
  await view.click({ force: true });
  await page.waitForTimeout(800);
  // 3. 点设置页左上角的返回，回到应用，看面板
  await page.mouse.click(44, 24);
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `/tmp/shots/${label}-1-answering.png`, mask: await centre(page), maskColor: '#d8d8d8' });
  // 3. 等回答写完
  await page.waitForTimeout(DELAY + 2000);
  const turns = await page.locator('.ap__turn, [class*="turn"]').evaluateAll((els) => els.filter((e) => e.textContent.includes('文档浮动条')).length).catch(() => -1);
  const count = await page.evaluate(() => (document.body.innerText.match(/一、首页侧栏头像/g) || []).length);
  const reads = await page.evaluate(() => window.__answerReads);
  await page.screenshot({ path: `/tmp/shots/${label}-2-done.png`, mask: await centre(page), maskColor: '#d8d8d8' });
  console.log(JSON.stringify({ label, port, answerShownTimes: count, answerStreamReads: reads }));
  await b.close();
})().catch((e) => { console.error(e); process.exit(1) });
