const { chromium } = require('playwright');
const [mode, prefix, inviteAgain] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ baseURL: 'http://127.0.0.1:13381', viewport: { width: 1280, height: 900 }, locale: 'zh-CN' });
  const page = await ctx.newPage();
  const login = await (await page.request.post('/api/users/auth/login', { data: { username: 'alice', password: 'demo12345' } })).json();
  await page.goto('/favicon.ico');
  await page.evaluate(({ t, u }) => { localStorage.setItem('accessToken', t); localStorage.setItem('user', JSON.stringify(u)); localStorage.setItem('cheese:locale', 'zh-CN'); }, { t: login.data.accessToken, u: login.data.user });
  for (const team of ['team-1', 'team-2']) {
    await page.goto(`/teams/${team}/members`);
    const sec = page.locator('section', { hasText: '已发送邀请' }).last();
    await sec.waitFor({ timeout: 10000 }).catch(() => {});
    await sec.scrollIntoViewIfNeeded().catch(() => {});
    await page.waitForTimeout(800);
    await page.screenshot({ path: `/tmp/inv6c4_shots/${mode}/${prefix}-${team}-sent-invitations.png` });
  }
  if (inviteAgain) {
    // Invite the same person once more through the dialog, on team 2.
    await page.goto('/teams/team-2/members');
    await page.getByRole('button', { name: /邀请成员/ }).click();
    await page.getByLabel('用户名或邮箱').fill(inviteAgain);
    await page.getByTestId('found-user').waitFor({ timeout: 10000 });
    await page.getByRole('button', { name: '邀请', exact: true }).click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `/tmp/inv6c4_shots/${mode}/${prefix}-invite-${inviteAgain}-once-more.png` });
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
