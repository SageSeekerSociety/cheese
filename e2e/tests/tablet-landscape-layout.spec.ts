import { test, expect } from '@playwright/test';
import { apiLogin, openFirstProject } from './helpers';

// 平板横放那一档：960（Vuetify 的 mdAndUp）到 1180（COMPACT_DESKTOP_MAX_WIDTH）为止。
//
// 这一档里常驻的二级侧栏（280px）把正文挤得太窄，所以两件事变了：
//   * 侧栏默认收起，rail 顶上那颗开关开合它（记住了人的选择）；
//   * 房间里只画对话，工作面板从右边按需拉出（地址里没有 ?tab= / ?card= 就是收着的）。
// 比 1180 宽就是今天的样子（侧栏常驻、对话和工作面板可拖 25–80% 分栏）。
//
// 这些差别**只在真浏览器里有**（宽度是算出来的布局，不是布尔 prop），所以守在这里：
// vitest 用的是 happy-dom 的固定宽度，量不到「同一份 DOM 在两个窗口宽下不一样」。
//
// 为什么先开到 1280、进屋、再缩到 1024：话题列表长在二级侧栏里，而那一档默认收着，
// `openFirstProject` 要等的行在收起状态里不可见。先在宽档进屋（侧栏常驻、行可见），
// 再缩窗口——这也正是一条真路径：人横过来/缩窗口那一刻布局就换档。

test.describe('平板横放（960–1180）', () => {
  test('1024：侧栏默认收起，面板按需从右边拉出，Esc 收起并把焦点还给开关', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/topics\//);

    // 宽档（1280 > 1180）：今天的样子——两栏并排、中间一条可拖的分隔；两颗开关都不在。
    await expect(page.locator('.pane-resizer')).toBeVisible();
    await expect(page.locator('[data-sidebar-toggle]')).toHaveCount(0);
    await expect(page.locator('[data-panel-toggle]')).toHaveCount(0);

    // 落到平板横放那一档。
    await page.setViewportSize({ width: 1024, height: 900 });

    // 侧栏默认收起：rail 顶上那颗开关在，aria-expanded=false，抽屉不是 active。
    const sidebarToggle = page.locator('[data-sidebar-toggle]');
    await expect(sidebarToggle).toBeVisible();
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#secondary-sidebar')).not.toHaveClass(/v-navigation-drawer--active/);

    // 房间里只剩对话：分隔线不在，面板是收着的一只浮层（visibility 藏掉了）。
    await expect(page.locator('.pane-resizer')).toHaveCount(0);
    const panelToggle = page.locator('[data-panel-toggle]');
    await expect(panelToggle).toBeVisible();
    await expect(panelToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#topic-panel')).toBeHidden();

    // 面板按需拉出（对话里点「查看改动」、点开一张卡走的也是同一条：地址一有 tab / card
    // 它就开）。
    await panelToggle.click();
    await expect(panelToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#topic-panel')).toBeVisible();

    // Esc 收起浮层，焦点回到打开它的那颗开关。
    await page.keyboard.press('Escape');
    await expect(page.locator('#topic-panel')).toBeHidden();
    await expect(panelToggle).toBeFocused();

    // 二级侧栏那颗开关：点开，抽屉成 active；Esc 收起，焦点还回来。
    await sidebarToggle.click();
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#secondary-sidebar')).toHaveClass(/v-navigation-drawer--active/);

    await page.keyboard.press('Escape');
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(sidebarToggle).toBeFocused();
  });

  test('1024：从侧栏浮层点进一个房间就收起它，而且不把这次收起记成人的选择', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await apiLogin(page);
    await openFirstProject(page);

    // 落到平板横放那一档：侧栏默认收起，rail 顶上那颗开关在，话题列表在浮层里。
    await page.setViewportSize({ width: 1024, height: 900 });
    const sidebarToggle = page.locator('[data-sidebar-toggle]');
    await expect(sidebarToggle).toBeVisible();

    // 打开浮层，从里面点进一个房间——「进房间」这是一次导航。
    await sidebarToggle.click();
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#secondary-sidebar')).toHaveClass(/v-navigation-drawer--active/);

    await page.locator('.topic-row').first().click();
    await page.waitForURL(/\/topics\//);

    // 导航之后浮层自己收起。
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#secondary-sidebar')).not.toHaveClass(/v-navigation-drawer--active/);

    // 收起的是这一次，不是人的选择：开过一次这件事还记着，刷新回来浮层仍是开的。
    await page.reload();
    await expect(page.locator('[data-sidebar-toggle]')).toHaveAttribute('aria-expanded', 'true');
  });

  test('1024：两层浮层都开着时，Esc 只关最后打开的那层，第二下才关另一层', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/topics\//);
    await page.setViewportSize({ width: 1024, height: 900 });

    const panelToggle = page.locator('[data-panel-toggle]');
    const sidebarToggle = page.locator('[data-sidebar-toggle]');

    // 先打开面板浮层，再打开侧栏浮层：两层同时开着，侧栏是后打开的那层。
    await panelToggle.click();
    await expect(page.locator('#topic-panel')).toBeVisible();
    await sidebarToggle.click();
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'true');

    // 一下 Esc 只关最上面那层（侧栏），面板还开着。
    await page.keyboard.press('Escape');
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#secondary-sidebar')).not.toHaveClass(/v-navigation-drawer--active/);
    await expect(page.locator('#topic-panel')).toBeVisible();
    await expect(panelToggle).toHaveAttribute('aria-expanded', 'true');

    // 第二下 Esc 才轮到压在下面的面板那层，焦点回到它的开关。
    await page.keyboard.press('Escape');
    await expect(page.locator('#topic-panel')).toBeHidden();
    await expect(panelToggle).toBeFocused();
  });

  test('1024：地址里点名的 tab 仍然把面板浮层拉起来', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/topics\//);
    const topicUrl = page.url().split('?')[0];

    // 落到平板横放：地址里没有 ?tab= / ?card=，浮层收着。
    await page.setViewportSize({ width: 1024, height: 900 });
    await expect(page.locator('#topic-panel')).toBeHidden();
    await expect(page.locator('[data-panel-toggle]')).toHaveAttribute('aria-expanded', 'false');

    // 别人发来的链接（?tab=）是「有人打开了这一格」，浮层得跟着开。
    await page.goto(`${topicUrl}?tab=changes`);
    await expect(page.locator('#topic-panel')).toBeVisible();
    await expect(page.locator('[data-panel-toggle]')).toHaveAttribute('aria-expanded', 'true');
  });

  test('1181 及以上回到今天的样子：侧栏常驻、两栏可拖', async ({ page }) => {
    await page.setViewportSize({ width: 1181, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);

    // 侧栏常驻，rail 上不收它；侧栏本身有右边缘的拖动抓手。
    await expect(page.locator('[data-sidebar-toggle]')).toHaveCount(0);

    await rows.first().click();
    await page.waitForURL(/\/topics\//);

    // 对话和工作面板还是并排的两栏，中间那条分隔在；面板不是浮层。
    await expect(page.locator('.pane-resizer')).toBeVisible();
    await expect(page.locator('[data-panel-toggle]')).toHaveCount(0);
    await expect(page.locator('#topic-panel')).toHaveCount(0);
  });
});
