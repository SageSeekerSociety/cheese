import { test, expect, type Page } from '@playwright/test';
import { appOriginOf, isEnvironmentNoise, login } from './helpers';

// 管理后台的看板（`/admin/dashboard`）。
//
// **这条用例是一次真事故的回执。** 看板前端曾经指着一条服务端已经没有的路由
// （`/admin/feedback/stats`，被拆成了一分类一条的 `/admin/stats/{…}`）。
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

/** 这一页自己招来的报错。环境噪声（外部源、种子缺的头像）的判据和反馈那条共用。 */
function unexpectedNoise(): string[] {
  const appOrigin = appOriginOf(test.info().project.use.baseURL);
  return consoleNoise.filter((entry) => !isEnvironmentNoise(entry, appOrigin));
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

/** 记下这一页打过哪几条看板接口，连同它们的响应码与 query（窗口切换钉的是 query）。 */
function watchStats(page: Page) {
  const seen: { path: string; search: string; status: number }[] = [];
  page.on('response', (res) => {
    const url = new URL(res.url());
    if (url.pathname.startsWith('/api/admin/stats/')) {
      seen.push({ path: url.pathname, search: url.search, status: res.status() });
    }
  });
  return seen;
}

/** 分类开关里点某一个页签。**只在 `.ad__kinds` 里找**：两行页签并成一行之后，
 *  页签里的短值是 `aria-hidden` 的附属读数（accessible name 保持裸标签），所以
 *  `exact: true` 仍能唯一定位到那颗按钮。
 *  也写着同一批标签、也带 button 的可访问名字（role=tab 虽然换掉了隐式 role，但
 *  `getByRole('button', { name: '…' })` 在部分版本里仍会撞上邻近控件），收窄到开关
 *  这一层才能点到「切换分类」那个控件本身。 */
function kindTab(page: Page, label: string) {
  return page.locator('.ad__kinds').getByRole('button', { name: label, exact: true });
}

test('看板打的是各自那条新接口，各类各自出数', async ({ page }) => {
  await login(page);
  const seen = watchStats(page);

  await page.goto('/admin/dashboard');

  // 1. 默认落**交付**（`/admin/stats/pipeline`），不是老路由。
  //    **这一条就是那次事故** —— 前端曾经指着 `/admin/feedback/stats`，
  //    而那条路径在服务端已经不存在，`stats` 会被当成一条反馈的 id 解析成 400。
  await expect
    .poll(() => seen.find((r) => r.path === '/api/admin/stats/pipeline'), { timeout: 30_000 })
    .toBeTruthy();
  expect(seen.filter((r) => r.path.startsWith('/api/admin/stats/'))).not.toContainEqual(
    expect.objectContaining({ path: '/api/admin/feedback/stats' }),
  );

  // 屏幕上真的出数了，而不是那句错误文案。这一条比「请求成功」更接近用户看到的东西：
  // 请求 200 而页面仍然写「加载失败」是可能的（比如形状对不上）。
  await expect(page.getByRole('heading', { name: '看板' })).toBeVisible();
  await expect(page.getByText('看板加载失败')).toHaveCount(0);
  // 限定 `.ad__kpis`：顶上摘要条也写着同一个词（见下一个用例的说明）。
  await expect(page.locator('.ad__kpis').getByText('等你处理')).toBeVisible();

  // 2. 切到反馈：打的是那一条反馈接口，待分诊那一张卡出数。
  await kindTab(page, '反馈').click();
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/feedback'), { timeout: 30_000 })
    .toBe(true);
  await expect(page.locator('.ad__kpis').getByText('待分诊')).toBeVisible();

  // 3. 切到用量：打的是那一条用量接口。
  const before = seen.length;
  await kindTab(page, '用量').click();
  await expect
    .poll(() => seen.slice(before).some((r) => r.path === '/api/admin/stats/usage'), { timeout: 30_000 })
    .toBe(true);
  await expect(page.getByText('最花 token 的项目')).toBeVisible();

  // 4. 切到平台：账号与机器在同一类里，机器那几个数是**存量**，那句话必须写着。
  await kindTab(page, '平台').click();
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/platform'), { timeout: 30_000 })
    .toBe(true);
  await expect(page.getByText('机器（存量）')).toBeVisible();
  await expect(page.getByText(/不是在线数/)).toBeVisible();

  // 5. 整轮下来每一条只有 2xx。分开断言是因为「请求发出去了」和「服务端认这条路径」
  //    是两件事 —— 事故里前端确实发出去了，回来的是 400。
  expect(seen.filter((r) => r.status >= 400)).toEqual([]);

  // 6. 控制台干净（放行那两条环境缺口之外一句都不许有）。
  expect(unexpectedNoise(), '浏览器控制台不该有报错').toEqual([]);
});

test('切走再切回来不再打接口，也不会把上一类的内容画在当前这一类上', async ({ page }) => {
  await login(page);
  const seen = watchStats(page);

  await page.goto('/admin/dashboard');
  await expect.poll(() => seen.find((r) => r.path === '/api/admin/stats/pipeline'), { timeout: 30_000 }).toBeTruthy();
  // **限定在 `.ad__kpis`（明细那一行）里找**：顶上那条分类导轨（`.ad__kinds`）里
  // 常驻着同一批短语（那是它的 value/hint），而摘要是**跨块**的导航，不算「上一类的内容」。
  // 不加这一层限定，`getByText('等你处理')` 会命中两处、报 strict mode 违规；而下面那条
  // 「切走之后数 0」也会永远不成立 —— 摘要条本来就不跟着分类消失。
  const pipelineKpi = page.locator('.ad__kpis').getByText('等你处理');
  await expect(pipelineKpi).toBeVisible();

  await kindTab(page, '用量').click();
  await expect.poll(() => seen.some((r) => r.path === '/api/admin/stats/usage'), { timeout: 30_000 }).toBe(true);
  // 切过去之后画的是用量那一块，交付那块的**明细**不再挂在屏幕上（摘要条还在，见上）。
  await expect(pipelineKpi).toHaveCount(0);
  await expect(page.getByText('最花 token 的项目')).toBeVisible();

  const before = seen.length;
  await kindTab(page, '交付').click();
  await expect(pipelineKpi).toBeVisible();
  // 那一份已经在手上了：再拉一次只是重复读那两张最长的表。
  expect(seen.length).toBe(before);

  expect(unexpectedNoise(), '浏览器控制台不该有报错').toEqual([]);
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

test('切窗口（7→30 天）后，已加载的类带 days=30 重拉、新切的类按 30 天拉', async ({ page }) => {
  await login(page);
  const seen = watchStats(page);

  await page.goto('/admin/dashboard');
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/pipeline'), { timeout: 30_000 })
    .toBe(true);

  // 切 30 天：此刻唯一已加载的窗口类（pipeline）带着 days=30 重拉。
  await page.getByRole('button', { name: '30 天' }).click();
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/pipeline' && r.search === '?days=30'), {
      timeout: 30_000,
    })
    .toBe(true);

  // 切到用量：按新窗口拉（days=30），KPI 标签跟着窗口变。
  await kindTab(page, '用量').click();
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/usage' && r.search === '?days=30'), {
      timeout: 30_000,
    })
    .toBe(true);
  await expect(page.locator('.ad__kpis').getByText('窗口内 token')).toBeVisible();

  // 整轮每一条都是 2xx（「请求发出去了」和「服务端认这条参数」是两件事）。
  expect(seen.filter((r) => r.status >= 400)).toEqual([]);
  expect(unexpectedNoise(), '浏览器控制台不该有报错').toEqual([]);
});

test('看板拉取失败：错误块显示服务端原话，「重试」真重拉', async ({ page }) => {
  await login(page);

  // 第一趟 pipeline 回 500（带服务端原话），之后放行。「错误块显示原话不改写」
  // 和「重试真重拉」是两条仓库口味，一起钉。
  let failedOnce = false;
  await page.route('**/api/admin/stats/pipeline**', async (route) => {
    if (!failedOnce) {
      failedOnce = true;
      await route.fulfill({
        status: 500,
        contentType: 'application/json',
        body: JSON.stringify({ code: 500, message: '数据库连接池满了', data: null }),
      });
    } else {
      await route.continue();
    }
  });

  await page.goto('/admin/dashboard');
  await expect(page.getByText('看板加载失败')).toBeVisible();
  await expect(page.getByText('数据库连接池满了')).toBeVisible();

  await page.getByRole('button', { name: '重试' }).click();
  // 第二次放行之后交付那一屏正常渲染，错误块整个消失。
  await expect(page.locator('.ad__kpis').getByText('等你处理')).toBeVisible();
  await expect(page.getByText('看板加载失败')).toHaveCount(0);

  expect(unexpectedNoise(), '浏览器控制台不该有报错').toEqual([]);
});

test('下钻：用量横条指向项目页，性能表 chevron 展开分钟级 spark', async ({ page }) => {
  await login(page);
  const seen = watchStats(page);

  await page.goto('/admin/dashboard');
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/pipeline'), { timeout: 30_000 })
    .toBe(true);

  // top_projects：整行是指向 /projects/{project_id} 的链接（project_id 一直在响应里）。
  await kindTab(page, '用量').click();
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/usage'), { timeout: 30_000 })
    .toBe(true);
  const projectLink = page.locator('.abr a[href*="/projects/"]').first();
  await expect(projectLink).toBeVisible();
  await expect(projectLink).toHaveAttribute('href', /\/projects\/[0-9a-f-]{36}/);

  // 性能：第一行 chevron 展开这条路由的分钟级 spark（响应里一直回、此前没人读）。
  await kindTab(page, '性能').click();
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/performance'), { timeout: 30_000 })
    .toBe(true);
  const toggle = page.locator('.ad__perf-toggle').first();
  await expect(toggle).toBeEnabled();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText('近 24 个分钟点的平均耗时')).toBeVisible();

  expect(seen.filter((r) => r.status >= 400)).toEqual([]);
  expect(unexpectedNoise(), '浏览器控制台不该有报错').toEqual([]);
});
