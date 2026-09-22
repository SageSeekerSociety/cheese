import { test, expect, type Page } from '@playwright/test';
import { login } from './helpers';

// 管理后台的看板（`/admin/dashboard`）。
//
// **这条用例是一次真事故的回执。** 看板前端曾经指着一条服务端已经没有的路由
// （`/admin/feedback/stats`，被拆成了 `/admin/stats/{feedback,usage,platform}`）。
// 那条路径落进 `/admin/feedback/{id}` 被当成一个 uuid 解析，真环境回 **400**，页面上
// 是「看板加载失败」—— 而预览的假后端**替那条老路留了一个别名**，于是预览一切正常；
// 没有一条测试碰过这一页，CI 全绿，dev 是坏的。
//
// 所以这里断言的第一件事是**打的是哪条接口**（不是「页面渲染没抛错」），第二件事是
// 屏幕上真的出现了数（不是那句「看板加载失败」）。这两件都只有对着真后端才成立 ——
// 假后端上它一直是绿的，那正是它当时没拦住事故的原因。
//
// 管理端要求后端把 alice 放进管理员名单（`PLATFORM_ADMIN_HANDLES`，见
// playwright.config.ts 里后端 webServer 的 env）。没有它这一页回 403。

test.describe.configure({ timeout: 180_000 });

/** 浏览器控制台里的话也算断言的一部分（同 `feedback-flows.spec.ts` 的理由）。 */
const consoleNoise: string[] = [];

/** 放行形状写死的两条：头像 404（头像表里 2/3/4 号有行、磁盘上没有文件，是环境的
 *  缺口，任何一页都会出现）和字体那条外部源的加载失败。**不写成「忽略所有 404」**：
 *  这一页自己发出的请求挂掉时必须红。 */
const ALLOWED = [
  /^\[console\] Failed to load resource: the server responded with a status of 404 \(Not Found\) @ https?:\/\/[^\s]+\/api\/avatars\/\d+$/,
  /^\[console\] Failed to load resource: net::ERR_[A-Z_]+ @ https?:\/\/cdn\.jsdelivr\.net\//,
];

function unexpectedNoise(): string[] {
  return consoleNoise.filter((entry) => !ALLOWED.some((re) => re.test(entry)));
}

test.beforeEach(({ page }) => {
  consoleNoise.length = 0;
  page.on('console', (msg) => {
    if (msg.type() === 'error' || msg.text().includes('Failed to resolve component')) {
      consoleNoise.push(`[console] ${msg.text()} @ ${msg.location().url || '?'}`);
    }
  });
  page.on('pageerror', (err) => consoleNoise.push(`[pageerror] ${err.message}`));
});

/** 记下这一页打过哪几条看板接口，连同它们的响应码。 */
function watchStats(page: Page) {
  const seen: { path: string; status: number }[] = [];
  page.on('response', (res) => {
    const path = new URL(res.url()).pathname;
    if (path.startsWith('/api/admin/stats/')) seen.push({ path, status: res.status() });
  });
  return seen;
}

test('看板打的是三条新接口，三类各自出数', async ({ page }) => {
  await login(page);
  const seen = watchStats(page);

  await page.goto('/admin/dashboard');

  // 1. 第一类：反馈。**这一条就是那次事故** —— 前端曾经指着 `/admin/feedback/stats`，
  //    而那条路径在服务端已经不存在，`stats` 会被当成一条反馈的 id 解析成 400。
  await expect
    .poll(() => seen.find((r) => r.path === '/api/admin/stats/feedback'), { timeout: 30_000 })
    .toBeTruthy();
  expect(seen.filter((r) => r.path.startsWith('/api/admin/stats/'))).not.toContainEqual(
    expect.objectContaining({ path: '/api/admin/feedback/stats' }),
  );

  // 屏幕上真的出数了，而不是那句错误文案。这一条比「请求成功」更接近用户看到的东西：
  // 请求 200 而页面仍然写「加载失败」是可能的（比如形状对不上）。
  await expect(page.getByRole('heading', { name: '看板' })).toBeVisible();
  await expect(page.getByText('看板加载失败')).toHaveCount(0);
  await expect(page.getByText('待分诊')).toBeVisible();

  // 2. 切到用量：打的是那一条用量接口，而且只有它。
  const before = seen.length;
  await page.getByRole('button', { name: '用量' }).click();
  await expect
    .poll(() => seen.slice(before).some((r) => r.path === '/api/admin/stats/usage'), { timeout: 30_000 })
    .toBe(true);
  await expect(page.getByText('最花 token 的项目')).toBeVisible();

  // 3. 切到平台：账号与机器在同一类里，机器那几个数是**存量**，那句话必须写着。
  await page.getByRole('button', { name: '平台' }).click();
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/platform'), { timeout: 30_000 })
    .toBe(true);
  await expect(page.getByText('机器（存量）')).toBeVisible();
  await expect(page.getByText(/不是在线数/)).toBeVisible();

  // 4. 整轮下来每一条只有 2xx。分开断言是因为「请求发出去了」和「服务端认这条路径」
  //    是两件事 —— 事故里前端确实发出去了，回来的是 400。
  expect(seen.filter((r) => r.status >= 400)).toEqual([]);

  // 5. 控制台干净（放行那两条环境缺口之外一句都不许有）。
  expect(unexpectedNoise()).toEqual([]);
});

test('切走再切回来不再打接口，也不会把上一类的内容画在当前这一类上', async ({ page }) => {
  await login(page);
  const seen = watchStats(page);

  await page.goto('/admin/dashboard');
  await expect.poll(() => seen.find((r) => r.path === '/api/admin/stats/feedback'), { timeout: 30_000 }).toBeTruthy();
  await expect(page.getByText('待分诊')).toBeVisible();

  await page.getByRole('button', { name: '用量' }).click();
  await expect.poll(() => seen.some((r) => r.path === '/api/admin/stats/usage'), { timeout: 30_000 }).toBe(true);
  // 切过去之后画的是用量那一块，反馈那块不再挂在屏幕上。
  await expect(page.getByText('待分诊')).toHaveCount(0);

  const before = seen.length;
  // `exact: true`：顶栏那颗「帮助与反馈」的可访问名字里也含「反馈」，而 Playwright 的
  // `name` 默认是**子串**匹配 —— 不加这一条，这一行会同时命中它和这一页的分类页签，
  // 报 strict mode 违规。
  await page.getByRole('button', { name: '反馈', exact: true }).click();
  await expect(page.getByText('待分诊')).toBeVisible();
  // 那一份已经在手上了：再拉一次只是重复读那两张最长的表。
  expect(seen.length).toBe(before);

  expect(unexpectedNoise()).toEqual([]);
});

test('后台能切到私密那一栏 —— 它就在 URL 里，也只在 URL 里', async ({ page }) => {
  await login(page);

  // 后端支持四个栏位（`tab=public|private|agent|security`），管理端有权限看私密。
  // 这一条守的是**地址**那一半：`?tab=private` 要能把那一栏拿出来。页面上有没有切它
  // 的控件是另一半（见话题文档的待办），两半都能单独坏。
  await page.goto('/admin/queue?tab=private');

  await expect(page.getByRole('heading', { name: '反馈队列' })).toBeVisible();
  await expect(page.getByText('队列加载失败')).toHaveCount(0);
});
