const { chromium } = require('playwright');
const fs = require('fs');
const mode = process.argv[2]; // before | after
const out = `/tmp/inv6c4_shots/${mode}`;
fs.mkdirSync(out, { recursive: true });
const BASE = 'http://127.0.0.1:13381';
const uiUser = mode === 'before' ? { q: 'frank', id: 6 } : { q: 'grace', id: 7 };
const apiUser = mode === 'before' ? 7 : 6;
const log = [];
const say = (s) => { console.log(s); log.push(s); };
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ baseURL: BASE, viewport: { width: 1280, height: 900 }, locale: 'zh-CN' });
  const page = await ctx.newPage();
  const login = await (await page.request.post('/api/users/auth/login', { data: { username: 'alice', password: 'demo12345' } })).json();
  const token = login.data.accessToken;
  const H = { Authorization: `Bearer ${token}` };
  const pending = (await (await page.request.get('/api/users/me/consents', { headers: H })).json()).data.pending;
  if (pending.length) await page.request.post('/api/users/me/consents', { headers: H, data: { documents: Object.fromEntries(pending.map((p) => [p.document, p.version])) } });
  await page.request.put('/api/users/me/language', { headers: H, data: { language: 'zh-CN' } });
  await page.goto('/favicon.ico');
  await page.evaluate(({ t, u }) => { localStorage.setItem('accessToken', t); localStorage.setItem('user', JSON.stringify(u)); localStorage.setItem('cheese:locale', 'zh-CN'); }, { t: token, u: login.data.user });

  // Artificial delay on the invite POST for team 1 (real backend behind it).
  let posts = 0;
  await page.route(/\/api\/teams\/1\/invitations$/, async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    posts += 1;
    const n = posts;
    say(`[route] POST #${n} to /teams/1/invitations at ${new Date().toISOString()}`);
    if (mode === 'after' && n === 1) {
      await new Promise((r) => setTimeout(r, 1500));
      say('[route] POST #1 answered 503 by the browser (stand-in failure, never reached the backend)');
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 503, message: 'Service Unavailable', error: { name: 'ServiceUnavailable', message: 'Service Unavailable', data: null, retryable: true } }) });
    }
    // Sent to the real backend at once; only the answer is held back 3 s, as a slow network would.
    const resp = await route.fetch();
    say(`[route] POST #${n} real backend answered ${resp.status()}; held 3 s before the page sees it`);
    await new Promise((r) => setTimeout(r, 3000));
    return route.fulfill({ response: resp });
  });

  await page.goto('/teams/team-1/members');
  await page.getByRole('button', { name: /邀请成员/ }).click();
  await page.getByLabel('用户名或邮箱').fill(uiUser.q);
  await page.getByTestId('found-user').waitFor({ timeout: 10000 });
  const submit = page.getByRole('button', { name: '邀请', exact: true });

  if (mode === 'after') {
    await submit.click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${out}/1-first-attempt-in-flight.png` });
    await page.waitForTimeout(2500);
    say(`[ui] after the 503: button disabled=${await submit.isDisabled()}`);
    await page.screenshot({ path: `${out}/2-after-failure-retry-available.png` });
  }

  const before = posts;
  // A quick double click, as people do on a button that does not react.
  await submit.dblclick();
  await page.waitForTimeout(400);
  say(`[ui] 400ms after double-clicking 邀请: button disabled=${await submit.isDisabled()}`);
  await page.screenshot({ path: `${out}/${mode === 'after' ? '3' : '1'}-second-click-while-pending.png` });
  // Click again twice and press Enter in the field, while the first is still pending.
  await submit.click({ force: true, timeout: 1000 }).catch(() => {});
  await submit.click({ force: true, timeout: 1000 }).catch(() => {});
  await page.getByLabel('用户名或邮箱').press('Enter').catch(() => {});
  await page.waitForTimeout(5000);
  say(`[ui] POSTs sent by the dialog in this burst: ${posts - before}`);

  await page.goto('/teams/team-1/members');
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${out}/${mode === 'after' ? '4' : '2'}-team-1-invitations.png`, fullPage: true });

  await page.goto('/teams/team-2/members');
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${out}/${mode === 'after' ? '5' : '3'}-team-2-invitations.png`, fullPage: true });

  const list = async (tid) => (await (await page.request.get(`/api/teams/${tid}/invitations?status=PENDING`, { headers: H })).json()).data.invitations.map((i) => `${i.id}:${i.user.username}`);
  say(`[api] pending invitations team 1: ${JSON.stringify(await list(1))}`);
  say(`[api] pending invitations team 2: ${JSON.stringify(await list(2))}`);
  fs.writeFileSync(`${out}/log.txt`, log.join('\n') + '\n');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
