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
// 「今天」动过一次，就在本分支合并 main 的时候：#1330 把侧栏那条竖线收窄到只剩
// 资料库（看板就是首页，项目名那一行点下去就到），#1339 又把总览与导出与发布并进
// 首页、AI 队友不再是一页。所以下面从「七格」变成「全局 + 资料库」，⋯ 菜单里多了
// 日历与成员。这不是这一版壳把版面换掉，是 main 换了版面、壳的声明跟着换。

// 项目侧栏上常驻的行，按今天渲染出来的顺序：全局那一行 + 资料库。
const PINNED = ['全局', '资料库'];

// 项目名旁边那个 ⋯ 菜单里的页：不占竖线，但一次点击可达。
const MENU = ['看板', '日历', '成员'];

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
// 它还必须仍然是一条真 `<a href>`：键盘 Tab 到、中键开新标签、右键复制链接都要能用。
// 改样式最容易顺手弄丢的就是这个。
test('顶栏的反馈入口：和语言开关同高、有一圈中性描边，而且还是个链接', async ({ page }) => {
  await login(page);

  const box = await page.locator('.feedback-entry').evaluate((el) => {
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
      href: el.getAttribute('href'),
      icons: el.querySelectorAll('.v-icon').length,
    };
  });

  // 同一档高度（「这条系统栏里的东西高度必须一致」那句注释要成立就得靠这个）。
  // 判据写「两个数相等」而不是「等于 24」：两边的高度来自两处不同的规则，该钉的是
  // 「它们一样」这件事本身，不是一个我手抄下来的数字。0.5px 的容差是为了不跟亚像素
  // 较劲；要拦的回归也很明确——按钮那边 `:size` 回到 28 那一档（差 4px）、或者
  // 语言开关那侧的 `height` 被删掉变回内容撑（差 2px），两条都能红。
  expect(box.langHeight).not.toBeNull();
  expect(box.height).toBeCloseTo(box.langHeight as number, 0);
  // 有形状：一整圈实线，而且跟着文字色走（不是一个写死的色值）
  expect(box.borderStyle).toBe('solid');
  expect(box.borderWidth).toBe('1px');
  expect(box.borderColor).toBe(box.color);
  // 那圈边不是琥珀：`--accent` 不许当线色，这个入口也一个琥珀都不该加
  expect(box.color).not.toBe('rgb(245, 127, 23)');
  // 形状 + 字形 + 文案：这一版补的是第一件
  expect(box.icons).toBe(1);
  // 仍然是一条真链接
  expect(box.tag).toBe('A');
  expect(box.href).toContain('/feedback');
});
