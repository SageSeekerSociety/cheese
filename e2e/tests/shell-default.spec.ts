import { expect, test } from '@playwright/test';
import { login } from './helpers';

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
const PINNED = ['全局', '资料库', '成员'];

// 项目名旁边那个 ⋯ 菜单里的页：不占竖线，但一次点击可达。
const MENU = ['看板', '日历'];

test('没声明壳的项目：第一屏还是看板，侧栏就是今天这一格，菜单里几页都在', async ({ page }) => {
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

  // ⋯ 菜单里那一组是**壳说了算**的：这一版前端认得的页里没摆上侧栏的，全在这里。
  // 少了谁，就说明有页在加壳之后掉出了导航。
  //
  // 点的是那个 chevron (`.rail-header__more`)，不是整条 `.rail-header`：这一条里
  // 现在有**两个**按钮，而名字那个 (`.rail-header__home`) 是 `flex: 1 1 auto`
  // （TopicSidebar.vue），占掉整条几乎全部宽度。点整条的中心落在它身上，于是这一
  // 下不是开菜单，是回首页——那样下面这条断言拿到的是空菜单，看起来像「菜单里的页
  // 全掉了」，其实一次都没打开过。
  await page.locator('.rail-header__more').click();
  // 菜单项**没有 role**：Vuetify 3 的 `v-list-item` 渲染成不带 role 的 `<div>`，
  // 外层 `v-list` 才是 `role="listbox"`，条目既不 `menuitem` 也不 `option`（对着
  // vuetify@3.9.3 实测：整份 DOM 里 `[role="menuitem"]` 是 0 个）。所以按 role 找
  // 永远匹配不到，只能按类名找——和侧栏那几格同一条来源。
  //
  // 末一行「项目设置」不由壳决定，但它在这个菜单里，一起钉住：壳加一页、少一页都
  // 应该在这里看得见，而不是悄悄换掉最后一行。
  const menu = page.locator('.v-overlay-container .v-list-item-title');
  await expect(menu).toHaveText([...MENU, '项目设置']);
  await page.keyboard.press('Escape');

  // 留一张图给这次验收：屏幕上就是上面断言的那一屏。
  await page.screenshot({ path: 'shell-default-desktop.png', fullPage: false });
});

// 顶栏那个「反馈」入口：它得是右边这一簇里唯一有**可见轮廓**的东西，而且和语言开关
// 同高。两个数都不是审美偏好，是修掉之后的读数：
//
//  · 高度。这一簇里两个控件的高度来自两处互不相干的规则：按钮那侧是 Vuetify 的
//    `:size` 给出的**内联** height（CSS 里的 `min-height` 压不过它），语言开关那侧
//    是这条系统栏里的一条规则。两边各改各的，量出来就差着（历史读数 28 对 26、24
//    对 26），而旁边那行注释写的正是「这条系统栏里的东西高度必须一致」。所以现在是
//    **两边都钉死 24**：`AppBar.vue` 里给语言开关补了 `height: 24px`，按钮继续靠
//    `:size="24"`。注释与代码不一致，只有真量一次才发现。
//  · 描边。`variant="outlined"` 画的是 `1px solid currentColor`，也就是这个按钮本来
//    就在用的 `--muted`。这条断言同时钉两件事：它**有**边（以前 `variant="text"` 一条
//    线都没有，而它右边的语言开关有一圈带描边的 chip，于是同一簇里唯一没有形状的控件
//    恰恰是「给平台提意见」这个），以及这圈边**不是琥珀色**（`--accent` 当线色在浅色
//    下只有 2.65:1，设计系统 §7.3 明令不许）。
//
// 它现在是一个**菜单**（「帮助与反馈」），不再是一条直达链接 —— 底下有反馈中心 /
// 我的反馈 / 管理后台三个目的地，而其中两个以前只能二级跳。改形状最容易弄丢的是
// 「点得开、项都在」，所以这一条同时钉住：尺寸与语言开关同高、字装得下、点开之后
// 三项都在、管理员那一项按身份出现。
test('顶栏的帮助与反馈：和语言开关同高、字装得下、点开有三项', async ({ page }) => {
  await login(page);

  const entry = page.locator('.help-entry');
  const box = await entry.evaluate((el) => {
    const s = getComputedStyle(el);
    const lang = document.querySelector('.language-toggle');
    return {
      height: el.getBoundingClientRect().height,
      langHeight: lang ? lang.getBoundingClientRect().height : null,
      borderWidth: s.borderTopWidth,
      borderStyle: s.borderTopStyle,
      borderColor: s.borderTopColor,
      color: s.color,
      tag: el.tagName,
      label: el.getAttribute('aria-label'),
      // 「那五个字放得下吗」。高度那条拦的是「矮了一档」，拦不住「宽度被钉死、字溢出」
      // —— 真发生过：`:size="24"` 对数字给的是一个**方格**（Vuetify 的 `useSize` 同时
      // 下发内联的 width 与 height），带文字的按钮被压成正方形、字压到隔壁那颗语言
      // 开关上，而只量高度的话两边都是 24、一切正常。
      contentRight: (() => {
        const c = el.querySelector('.v-btn__content');
        return c ? c.getBoundingClientRect().right : null;
      })(),
      right: el.getBoundingClientRect().right,
      contentScrollW: (() => {
        const c = el.querySelector('.v-btn__content');
        return c ? c.scrollWidth : null;
      })(),
      contentClientW: (() => {
        const c = el.querySelector('.v-btn__content');
        return c ? c.clientWidth : null;
      })(),
    };
  });

  // 同一档高度（「这条系统栏里的东西高度必须一致」那句注释要成立就得靠这个）。判据写
  // 「两个数相等」而不是「等于 24」：两边的高度来自两处不同的规则，该钉的是「它们一样」。
  expect(box.langHeight).not.toBeNull();
  expect(box.height).toBeCloseTo(box.langHeight as number, 0);
  // 有形状：一整圈实线，而且跟着文字色走（不是一个写死的色值）
  expect(box.borderStyle).toBe('solid');
  expect(box.borderWidth).toBe('1px');
  expect(box.borderColor).toBe(box.color);
  // 那圈边不是琥珀：`--accent` 不许当线色，这个入口也一个琥珀都不该加
  expect(box.color).not.toBe('rgb(245, 127, 23)');
  expect(box.tag).toBe('BUTTON');

  // 那五个字在框里（+1px 亚像素容差），而且没有被自己的盒子裁掉。
  expect(box.contentRight).not.toBeNull();
  expect(box.contentRight as number).toBeLessThanOrEqual(box.right + 1);
  expect(box.contentScrollW as number).toBeLessThanOrEqual((box.contentClientW as number) + 1);

  // 点开：三个目的地都从二级跳变成一级。alice 在 e2e 里是平台管理员（见
  // playwright.config.ts 的 PLATFORM_ADMIN_HANDLES），所以管理后台那一项也该在。
  await entry.click();
  const menu = page.locator('.v-overlay__content').filter({ hasText: '反馈中心' });
  await expect(menu.getByRole('link', { name: '反馈中心' })).toBeVisible();
  await expect(menu.getByRole('link', { name: '我的反馈' })).toBeVisible();
  await expect(menu.getByRole('link', { name: '管理后台' })).toBeVisible();

  // 可访问名字**带着未读状态**：色点对读屏和色觉障碍读者不成立，所以状态同时进名字。
  expect(box.label === '帮助与反馈' || box.label === '帮助与反馈，有未读更新').toBe(true);
});
