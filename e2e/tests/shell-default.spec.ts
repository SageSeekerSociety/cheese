import { expect, test } from '@playwright/test';
import { apiLogin } from './helpers';

// `default` 壳的验收：没声明壳的项目必须和今天逐屏一样。
//
// 壳最容易出的错不是崩，是**悄悄换了一屏**——第一屏换了地方，或者侧栏少了一格。
// 那种错单测问不出来（单测问的是函数返回什么），只有真开一个项目、看屏幕上还剩下
// 什么才发现。所以这一条走真浏览器：登录 → 点开第一个项目（种子里那三个都没声明
// 壳）→ 第一屏落在哪、侧栏有哪几行、⋯ 菜单里有什么。
//
// 等号右边是**今天**的样子，不是壳的某种理想形态：这一格、这个顺序、这一屏，就是
// 此刻每个人打开项目看到的。改这一条等于改「老项目长什么样」，要单独想清楚。
//
// Membership stays visible so joining, transferring and leaving are discoverable.
const PINNED = ['全局', '资料库', '成员', '项目文档'];

// 项目名旁边那个 ⋯ 菜单里的页：不占竖线，但一次点击可达。看板不在这里——项目名
// 那一行就是它的入口。
const MENU = ['日历', '定时与触发'];

test('没声明壳的项目：第一屏还是看板，侧栏就是今天这一格，菜单里几页都在', async ({ page }) => {
  await apiLogin(page);

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

  // ⋯ 菜单里那一组是**壳说了算**的：这一版前端认得的页里没摆上侧栏的，全在这里。
  // 少了谁，就说明有页在加壳之后掉出了导航。
  await page.getByRole('button', { name: '项目菜单', exact: true }).click();
  // 菜单项**没有 role**：Vuetify 3 的 `v-list-item` 渲染成不带 role 的 `<div>`，
  // 外层 `v-list` 才是 `role="listbox"`，条目既不 `menuitem` 也不 `option`（对着
  // vuetify@3.9.3 实测：整份 DOM 里 `[role="menuitem"]` 是 0 个）。所以按 role 找
  // 永远匹配不到，只能按类名找——和侧栏那几格同一条来源。
  //
  // 所有者在这里还有项目设置；转让和退出项目只在成员页。
  const menu = page.locator('.v-overlay-container .v-list-item-title');
  await expect(menu).toHaveText([...MENU, '项目设置']);
  await page.keyboard.press('Escape');

  // 留一张图给这次验收：屏幕上就是上面断言的那一屏。
  await page.screenshot({ path: 'shell-default-desktop.png', fullPage: false });
});

// 顶栏右边这一簇（登录后是「帮助与反馈」和铃铛）：同一种形状 —— 同高、无描边、不带
// 琥珀。登录后语言开关不在这里（它在「我」的菜单里，和外观并排），所以这一簇只剩两颗。
//
//  · 高度。按钮那侧的高度来自两处互不相干的规则：铃铛是 Vuetify `:size` 给出的**内联**
//    height，「帮助与反馈」是它自己组件里的一条规则。两边各改各的，量出来就会差着（历史
//    读数 28 对 26、24 对 26），所以钉的是「两颗一样高」，不是某一个数。
//  · 字装得下。`:size="24"` 对数字给的是一个**方格**（Vuetify 的 `useSize` 同时下发内联
//    的 width 与 height），带文字的按钮被压成正方形、字溢到隔壁 —— 真发生过，而只量高度
//    的话一切正常。
//
// 它是一个**菜单**：底下有反馈中心 / 我的反馈 / 管理后台 / 了解知是。改形状最容易弄丢
// 的是「点得开、项都在」，所以这一条同时钉住点开之后各项都在、管理员那一项按身份出现。
test('顶栏的帮助与反馈：和铃铛同高、字装得下、点开各项都在', async ({ page }) => {
  await apiLogin(page);

  // 登录后语言在「我」的菜单里，顶栏不再有语言开关。
  await expect(page.locator('.app-system-bar .language-toggle')).toHaveCount(0);

  const entry = page.locator('.help-entry');
  const box = await entry.evaluate((el) => {
    const s = getComputedStyle(el);
    const bell = document.querySelector('.app-system-bar [aria-label="通知"]');
    const content = el.querySelector('.v-btn__content');
    return {
      height: el.getBoundingClientRect().height,
      bellHeight: bell ? bell.getBoundingClientRect().height : null,
      borderWidth: s.borderTopWidth,
      color: s.color,
      tag: el.tagName,
      label: el.getAttribute('aria-label'),
      right: el.getBoundingClientRect().right,
      contentRight: content ? content.getBoundingClientRect().right : null,
      contentScrollW: content ? content.scrollWidth : null,
      contentClientW: content ? content.clientWidth : null,
    };
  });

  expect(box.bellHeight).not.toBeNull();
  expect(box.height).toBeCloseTo(box.bellHeight as number, 0);
  // 和铃铛同一种形状：没有描边（Vuetify 的按钮样式是 solid、宽 0，所以量宽度）
  expect(box.borderWidth).toBe('0px');
  // 不带琥珀：它不是这一屏的主操作
  expect(box.color).not.toBe('rgb(245, 127, 23)');
  expect(box.tag).toBe('BUTTON');

  // 那五个字在框里（+1px 亚像素容差），而且没有被自己的盒子裁掉。
  expect(box.contentRight).not.toBeNull();
  expect(box.contentRight as number).toBeLessThanOrEqual(box.right + 1);
  expect(box.contentScrollW as number).toBeLessThanOrEqual((box.contentClientW as number) + 1);

  // 点开：各个目的地都是一级可达。alice 在 e2e 里是平台管理员（见
  // playwright.config.ts 的 PLATFORM_ADMIN_HANDLES），所以管理后台那一项也该在。
  await entry.click();
  const menu = page.locator('.v-overlay__content').filter({ hasText: '反馈中心' });
  await expect(menu.getByRole('link', { name: '反馈中心' })).toBeVisible();
  await expect(menu.getByRole('link', { name: '我的反馈' })).toBeVisible();
  await expect(menu.getByRole('link', { name: '管理后台' })).toBeVisible();
  await expect(menu.getByRole('link', { name: '了解知是' })).toBeVisible();

  // 可访问名字**带着未读状态**：色点对读屏和色觉障碍读者不成立，所以状态同时进名字。
  expect(box.label === '帮助与反馈' || box.label === '帮助与反馈，有未读更新').toBe(true);
});
