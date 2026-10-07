import { test, expect, type Page } from '@playwright/test';
import { appOriginOf, isEnvironmentNoise, apiLogin, recordUnknownElements, unknownElements } from './helpers';

// 管理后台的统计页（平台总览、交付流水线、用量、性能……，原来是同一页看板上的几屏）。
//
// **这条用例是一次真事故的回执。** 看板前端曾经指着一条服务端已经没有的路由
// （`/admin/feedback/stats`，被拆成了一分类一条的 `/admin/stats/{…}`）。
// 那条路径落进 `/admin/feedback/{id}` 被当成一个 uuid 解析，真环境回 **400**，页面上
// 是「加载失败」—— 而预览的假后端**替那条老路留了一个别名**，于是预览一切正常；
// 没有一条测试碰过这一页，CI 全绿，dev 是坏的。
//
// 所以这里断言的第一件事是**打的是哪条接口**（不是「页面渲染没抛错」），第二件事是
// 屏幕上真的出现了数（不是那句「数据加载失败」）。这两件都只有对着真后端才成立 ——
// 假后端上它一直是绿的，那正是它当时没拦住事故的原因。
//
// 管理端要求后端把 alice 放进管理员名单（`PLATFORM_ADMIN_HANDLES`，见
// playwright.config.ts 里后端 webServer 的 env）。没有它这一页回 403。

test.describe.configure({ timeout: 180_000 });

/** 浏览器控制台里的话也算断言的一部分（同 `feedback-flows.spec.ts` 的理由）。 */
const consoleNoise: string[] = [];

/** 这一页自己招来的报错。环境噪声（外部源、种子缺的头像）的判据和反馈那条共用。 */
function unexpectedNoise(allowed500Path?: string): string[] {
  const appOrigin = appOriginOf(test.info().project.use.baseURL);
  return consoleNoise.filter((entry) => {
    // The error-state test deliberately injects one 500; allow only that request's
    // browser console line, while keeping all unrelated console errors actionable.
    if (allowed500Path && entry.includes(allowed500Path) && entry.includes('500')) return false;
    return !isEnvironmentNoise(entry, appOrigin);
  });
}

test.beforeEach(async ({ page }) => {
  consoleNoise.length = 0;
  await recordUnknownElements(page);
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      consoleNoise.push(`[console] ${msg.text()} @ ${msg.location().url || '?'}`);
    }
  });
  page.on('pageerror', (err) => consoleNoise.push(`[pageerror] ${err.message}`));
});

test.afterEach(async ({ page }) => {
  expect(await unknownElements(page), '页面上不该有没注册的组件').toEqual([]);
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

/** 侧栏里点某一页。**按链接找**：组名（「用量与成本」）是不可点的小标题，`exact`
 *  防止「用量」撞上它；用侧栏而不是 `page.goto`，切页才是单页应用里的一次路由跳转，
 *  store 里已拉过的那一类还在。 */
function sideLink(page: Page, label: string) {
  return page.getByRole('link', { name: label, exact: true });
}

test('统计页打的是各自那条接口，各页各自出数', async ({ page }) => {
  await apiLogin(page);
  const seen = watchStats(page);

  await page.goto('/admin/pipeline');

  // 1. 交付流水线打的是 `/admin/stats/pipeline`，不是老路由。
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
  await expect(page.getByRole('heading', { name: '交付流水线' })).toBeVisible();
  await expect(page.getByText('数据加载失败')).toHaveCount(0);
  await expect(page.locator('.ad__kpis').getByText('等你处理')).toBeVisible();

  // 2. 切到反馈趋势：打的是那一条反馈接口，待分诊那一张卡出数。
  await sideLink(page, '反馈趋势').click();
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/feedback'), { timeout: 30_000 })
    .toBe(true);
  await expect(page.locator('.ad__kpis').getByText('待分诊')).toBeVisible();

  // 3. 切到用量：打的是那一条用量接口。
  const before = seen.length;
  await sideLink(page, '用量').click();
  await expect
    .poll(() => seen.slice(before).some((r) => r.path === '/api/admin/stats/usage'), { timeout: 30_000 })
    .toBe(true);
  await expect(page.getByText('最花 token 的项目')).toBeVisible();

  // 4. 切到平台总览：账号与机器在同一类里，机器那几个数是**存量**，那句话必须写着。
  await sideLink(page, '平台总览').click();
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

test('切走再切回来不再打接口，也不会把上一页的内容画在当前这一页上', async ({ page }) => {
  await apiLogin(page);
  const seen = watchStats(page);

  await page.goto('/admin/pipeline');
  await expect.poll(() => seen.find((r) => r.path === '/api/admin/stats/pipeline'), { timeout: 30_000 }).toBeTruthy();
  const pipelineKpi = page.locator('.ad__kpis').getByText('等你处理');
  await expect(pipelineKpi).toBeVisible();

  await sideLink(page, '用量').click();
  await expect.poll(() => seen.some((r) => r.path === '/api/admin/stats/usage'), { timeout: 30_000 }).toBe(true);
  // 切过去之后画的是用量那一页，交付那页的数不再挂在屏幕上。
  await expect(pipelineKpi).toHaveCount(0);
  await expect(page.getByText('最花 token 的项目')).toBeVisible();

  const before = seen.length;
  await sideLink(page, '交付流水线').click();
  await expect(pipelineKpi).toBeVisible();
  // 那一份已经在手上了：再拉一次只是重复读那两张最长的表。
  expect(seen.length).toBe(before);

  expect(unexpectedNoise(), '浏览器控制台不该有报错').toEqual([]);
});

test('后台能切到私密那一栏 —— 它就在 URL 里，也只在 URL 里', async ({ page }) => {
  await apiLogin(page);

  // 后端支持四个栏位（`tab=public|private|agent|security`），管理端有权限看私密。
  // 这一条守的是**地址**那一半：`?tab=private` 要能把那一栏拿出来。页面上有没有切它
  // 的控件是另一半（见话题文档的待办），两半都能单独坏。
  await page.goto('/admin/queue?tab=private');

  await expect(page.getByRole('heading', { name: '反馈', exact: true })).toBeVisible();
  await expect(page.getByText('队列加载失败')).toHaveCount(0);
});

test('切窗口（7→30 天）后，已加载的类带 days=30 重拉、新切的类按 30 天拉', async ({ page }) => {
  await apiLogin(page);
  const seen = watchStats(page);

  await page.goto('/admin/pipeline');
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

  // 切到用量：按新窗口拉（days=30，几页共用一个窗口），KPI 标签跟着窗口变。
  await sideLink(page, '用量').click();
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

test('统计拉取失败：错误块显示服务端原话，「重试」真重拉', async ({ page }) => {
  await apiLogin(page);

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

  await page.goto('/admin/pipeline');
  await expect(page.getByText('数据加载失败')).toBeVisible();
  await expect(page.getByText('数据库连接池满了')).toBeVisible();

  await page.getByRole('button', { name: '重试' }).click();
  // 第二次放行之后交付那一屏正常渲染，错误块整个消失。
  await expect(page.locator('.ad__kpis').getByText('等你处理')).toBeVisible();
  await expect(page.getByText('数据加载失败')).toHaveCount(0);

  expect(unexpectedNoise('/api/admin/stats/pipeline?days=7'), '除测试注入的 500 外，浏览器控制台不该有报错').toEqual([]);
});

test('下钻：用量横条指向项目页，性能表 chevron 展开分钟级 spark', async ({ page }) => {
  await apiLogin(page);
  const seen = watchStats(page);

  // CI starts from an empty usage database. Keep the real endpoint, response envelope,
  // aggregation, and all other fields; add one display row only when the real query
  // has no project to drill into.
  await page.route('**/api/admin/stats/usage**', async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    if (response.ok() && payload.data.top_projects.length === 0) {
      payload.data.top_projects = [{
        project_id: '00000000-0000-4000-8000-000000000001',
        name: 'CI drill-down project',
        tokens: 1,
        cost_usd: 0,
      }];
    }
    await route.fulfill({ response, json: payload });
  });

  await page.goto('/admin/pipeline');
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/pipeline'), { timeout: 30_000 })
    .toBe(true);

  // top_projects：整行是指向 /projects/{project_id} 的链接（project_id 一直在响应里）。
  await sideLink(page, '用量').click();
  await expect
    .poll(() => seen.some((r) => r.path === '/api/admin/stats/usage'), { timeout: 30_000 })
    .toBe(true);
  const projectLink = page.locator('.abr a[href*="/projects/"]').first();
  await expect(projectLink).toBeVisible();
  await expect(projectLink).toHaveAttribute('href', /\/projects\/[0-9a-f-]{36}/);

  // 性能：第一行 chevron 展开这条路由的分钟级 spark（响应里一直回、此前没人读）。
  await sideLink(page, '性能').click();
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
