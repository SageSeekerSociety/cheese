import { expect, test } from '@playwright/test';
import { login } from './helpers';

// `default` 壳的验收：没声明壳的项目必须和今天逐屏一样。
//
// 壳最容易出的错不是崩，是**悄悄换了一屏**——第一屏换了地方，或者侧栏少了一格。
// 那种错单测问不出来（单测问的是函数返回什么），只有真开一个项目、看屏幕上还剩下
// 什么才发现。所以这一条走真浏览器：登录 → 点开第一个项目（种子里那三个都没声明
// 壳）→ 第一屏落在哪、侧栏有哪几行、有没有多出一块「更多」。
//
// 等号右边是**今天**的样子，不是壳的某种理想形态：这七格、这个顺序、这一屏，就是
// 加壳之前每个人打开项目看到的。改这一条等于改「老项目长什么样」，要单独想清楚。

// 项目侧栏顶部那七格页 + 全局，按今天渲染出来的顺序。
const PINNED = ['全局', '总览', '看板', '日历', '资料库', '导出与发布', 'AI 队友', '成员'];

test('没声明壳的项目：第一屏还是看板，七格一格不少、顺序照旧、没有「更多」', async ({ page }) => {
  await login(page);

  // 从 rail 点进第一个项目。`--tile` 才是一个项目；不带 `--tile` 的第一格是首页。
  //
  // 带着重试：rail 先画浏览器缓存里那份清单，服务端那份到货后再重画一遍，两次画的
  // 是不同的 DOM 节点。点正好落在重画中间就会点到已经不在了的那个节点上（Playwright
  // 会一直重试到超时，看起来像"点不动"）。所以点不动就再点一次，而不是把超时调到
  // 比重画更长——那只是把同一场比赛的起跑线往后挪。
  await page.locator('.app-rail-item--tile').first().waitFor();
  await expect(async () => {
    await page.locator('.app-rail-item--tile').first().click({ timeout: 2_000 });
  }).toPass({ timeout: 30_000 });

  // 第一屏 = default 壳的 home = 看板（路由名 workspace-running）。中转地址
  // `/projects/:id` 自己什么都不画，它只是去第一屏路上的一瞬。
  await page.waitForURL(/\/projects\/[^/]+\/running$/);

  // 侧栏是常驻的，板块页上也在。等它画出来再数格子。
  const pinned = page.locator('.pinned-row');
  await expect(pinned).toHaveCount(PINNED.length);
  await expect(pinned.locator('.v-list-item-title')).toHaveText(PINNED);

  // 「更多」是壳把某几格默认收起来时才出现的那一块。default 什么都不收，所以它
  // 不该在屏幕上——出现了就说明有格子在加壳之后掉进了备用区。
  await expect(page.getByText('更多', { exact: true })).toHaveCount(0);

  // 留一张图给这次验收：屏幕上就是上面断言的那一屏。
  await page.screenshot({ path: 'shell-default-desktop.png', fullPage: false });
});
