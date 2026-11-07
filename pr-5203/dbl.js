const { chromium } = require('playwright');
const mode = process.argv[2], rounds = Number(process.argv[3] || 5);
const uid = mode === 'before' ? 6 : 7, q = mode === 'before' ? 'frank' : 'grace';
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ baseURL: 'http://127.0.0.1:13381', viewport: { width: 1280, height: 900 }, locale: 'zh-CN' });
  const page = await ctx.newPage();
  const login = await (await page.request.post('/api/users/auth/login', { data: { username: 'alice', password: 'demo12345' } })).json();
  const H = { Authorization: `Bearer ${login.data.accessToken}` };
  await page.goto('/favicon.ico');
  await page.evaluate(({ t, u }) => { localStorage.setItem('accessToken', t); localStorage.setItem('user', JSON.stringify(u)); localStorage.setItem('cheese:locale', 'zh-CN'); }, { t: login.data.accessToken, u: login.data.user });
  let posts = 0;
  page.on('request', (r) => { if (r.method() === 'POST' && /\/api\/teams\/1\/invitations$/.test(r.url())) posts++; });
  const pend = async () => (await (await page.request.get('/api/teams/1/invitations?status=PENDING', { headers: H })).json()).data.invitations.filter((i) => i.user.id === uid);
  let dup = 0;
  for (let k = 1; k <= rounds; k++) {
    for (const i of await pend()) await page.request.delete(`/api/teams/1/invitations/${i.id}`, { headers: H });
    await page.goto('/teams/team-1/members');
    await page.getByRole('button', { name: /邀请成员/ }).click();
    await page.getByLabel('用户名或邮箱').fill(q);
    await page.getByTestId('found-user').waitFor({ timeout: 10000 });
    const p0 = posts;
    await page.getByRole('button', { name: '邀请', exact: true }).dblclick();
    await page.waitForTimeout(2500);
    const rows = (await pend()).length;
    if (rows > 1) dup++;
    console.log(`round ${k}: POSTs sent=${posts - p0} pending rows for ${q}=${rows}`);
    if (k === rounds) await page.screenshot({ path: `/tmp/inv6c4_shots/${mode}/dblclick-no-delay-last-round.png`, fullPage: true });
  }
  console.log(`rounds with two pending invitations: ${dup}/${rounds}`);
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
