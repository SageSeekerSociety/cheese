import { test, expect, type Page } from '@playwright/test';
import { apiLogin, openFirstProject } from './helpers';

/** 对话那一块：读者看消息的那一栏。 */
const conversation = (page: Page) => page.getByTestId('chat-scroll').first();

/**
 * 面板：页头「概览」那颗按钮（`data-panel-toggle`，页头那一组的第一颗；按下 = 那一处开着、显示的是
 * 概览）说它开合的那几块（`aria-controls`）里看得见的那一块。宽档里它是
 * 布局区里工作面板的格子，窄档里是压在对话上的那张浮层，开关指着的始终是读者看到的那一块。
 */
async function panel(page: Page) {
  const ids = ((await page.locator('[data-panel-toggle]').getAttribute('aria-controls')) ?? '').split(/\s+/).filter(Boolean);
  expect(ids.length, '「概览」得说它开合的是哪一块').toBeGreaterThan(0);
  return page.locator(ids.map((id) => `[id="${id}"]`).join(', '));
}
async function panelShown(page: Page) {
  const blocks = await panel(page);
  const n = await blocks.count();
  for (let i = 0; i < n; i++) if (await blocks.nth(i).isVisible()) return true;
  return false;
}

/** 面板并排开在对话右边（外壳布局区里的两格），不是压在对话上的浮层。 */
async function besideConversation(page: Page) {
  const chat = await conversation(page).boundingBox();
  const blocks = await panel(page);
  const boxes = await blocks.evaluateAll((els) =>
    els.map((el) => el.getBoundingClientRect()).filter((r) => r.width > 0 && r.height > 0).map((r) => r.x)
  );
  return !!chat && boxes.length > 0 && boxes.every((x) => x >= chat.x + chat.width - 1);
}

// 平板横放那一档：960（Vuetify 的 mdAndUp）到 1180（COMPACT_DESKTOP_MAX_WIDTH）为止。
//
// 这一档里常驻的二级侧栏（280px）把正文挤得太窄，所以侧栏默认收起，rail 顶上那颗
// 开关开合它（记住了人的选择）。
//
// 右侧面板按主区（对话加面板）的宽度分档（`useWorkspaceLayout.ts` 的 `panelMode`）：
// 840 起并排，更窄放不下两栏，面板浮在对话上。并排时进来开不开：任务页 1000 起开着、840 到
// 1000 收着；频道页的概览是一栏窄的目录，放得下对话加这一栏（`CHANNEL_OVERVIEW_FITS`）就开着，
// 所以并排时都开着。频道页上并排时开或关只算这一页：离开再进来又照宽度来。
//
// 这些差别**只在真浏览器里有**（宽度是算出来的布局，不是布尔 prop），所以守在这里：
// vitest 用的是 happy-dom 的固定宽度，量不到「同一份 DOM 在两个窗口宽下不一样」。
//
// 左边那一列 64px 方块栏 2026-11-16 拿掉了，主区就是窗口减去常驻侧栏：平板横放收起侧栏
// 时主区等于窗口宽。所以 980 那几条的主区是 980：不到 1000，频道页的概览照样开着。
//
// 为什么先开到 1280、进屋、再缩到平板宽：话题列表长在二级侧栏里，而那一档默认收着，
// `openFirstProject` 要等的行在收起状态里不可见。先在宽档进屋（侧栏常驻、行可见），
// 再缩窗口——这也正是一条真路径：人横过来/缩窗口那一刻布局就换档。

test.describe('平板横放（960–1180）', () => {
  test('980：侧栏默认收起；频道的概览默认并排开着，收起只算这一页', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/channels\//);

    // 宽档（1280 > 1180）：侧栏常驻，rail 上没有它的开关。
    await expect(page.locator('[data-sidebar-toggle]')).toHaveCount(0);

    // 落到平板横放那一档。
    await page.setViewportSize({ width: 980, height: 900 });

    // 侧栏默认收起：rail 顶上那颗开关在，aria-expanded=false，抽屉不是 active。
    const sidebarToggle = page.locator('[data-sidebar-toggle]');
    await expect(sidebarToggle).toBeVisible();
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#secondary-sidebar')).not.toHaveClass(/v-navigation-drawer--active/);

    // 主区 980，不到 1000，但放得下对话加概览那一栏：概览并排开在对话右边，不是浮层。
    const panelToggle = page.locator('[data-panel-toggle]');
    await expect(panelToggle).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(() => panelShown(page)).toBe(true);
    await expect.poll(() => besideConversation(page)).toBe(true);
    await expect(page.locator('.panel-scrim')).toHaveCount(0);

    // 点「概览」收起，只剩对话。
    await panelToggle.click();
    await expect(panelToggle).toHaveAttribute('aria-pressed', 'false');
    await expect.poll(() => panelShown(page)).toBe(false);

    // 只算这一页：按地址再进这个频道，又照宽度来，开着。地址里不带 `?tab=`：那是地址点名要哪一格。
    await page.goto(new URL(page.url()).pathname);
    await expect(page.locator('[data-panel-toggle]')).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(() => panelShown(page)).toBe(true);

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
    await page.waitForURL(/\/channels\//);

    // 导航之后浮层自己收起。
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#secondary-sidebar')).not.toHaveClass(/v-navigation-drawer--active/);

    // 收起的是这一次，不是人的选择：开过一次这件事还记着，刷新回来浮层仍是开的。
    await page.reload();
    await expect(page.locator('[data-sidebar-toggle]')).toHaveAttribute('aria-expanded', 'true');
  });

  test('主区放不下两栏时面板浮在对话上：Esc 收起，焦点回到「概览」', async ({ page }) => {
    // 1181 起侧栏常驻。默认 280 宽的侧栏留下 901，够并排；人把侧栏拖到 360，主区只剩
    // 821，放不下两栏。侧栏宽度记在本机（composables/sidebarWidth.ts），开页之前写进去。
    await page.addInitScript(() => localStorage.setItem('cheesex.sidebarWidth', '360'));
    await page.setViewportSize({ width: 1181, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/channels\//);
    const main = await conversation(page).boundingBox();
    expect(main!.width, '这个用例要主区窄于 840').toBeLessThan(840);

    // 浮层默认收着。
    const panelToggle = page.locator('[data-panel-toggle]');
    await expect(panelToggle).toHaveAttribute('aria-pressed', 'false');
    await expect.poll(() => panelShown(page)).toBe(false);

    // 打开：浮在对话上，背后一层遮罩。
    await panelToggle.click();
    await expect.poll(() => panelShown(page)).toBe(true);
    await expect(page.locator('.panel-scrim')).toBeVisible();
    expect(await besideConversation(page), '窄档里面板该浮在对话上').toBe(false);

    // Esc 收起浮层，焦点回到打开它的那颗开关。
    await page.keyboard.press('Escape');
    await expect.poll(() => panelShown(page)).toBe(false);
    await expect(panelToggle).toBeFocused();
  });

  test('980：地址里点名的 tab 仍然把面板拉起来', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/channels\//);
    const topicUrl = page.url().split('?')[0];

    // 落到平板横放，读者把概览收起：只剩对话。
    await page.setViewportSize({ width: 980, height: 900 });
    const panelToggle = page.locator('[data-panel-toggle]');
    await expect(panelToggle).toHaveAttribute('aria-pressed', 'true');
    await panelToggle.click();
    await expect.poll(() => panelShown(page)).toBe(false);

    // 别人发来的链接（?tab=）是「有人打开了这一格」，面板得跟着开，页头上按下的是「讨论串」。
    await page.goto(`${topicUrl}?tab=threads`);
    await expect.poll(() => panelShown(page)).toBe(true);
    await expect(page.locator('[data-panel-thing="threads"]')).toHaveAttribute('aria-pressed', 'true');
  });

  test('1440：侧栏常驻，主区够宽，面板默认并排开着', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);

    // 侧栏常驻，rail 上不收它。
    await expect(page.locator('[data-sidebar-toggle]')).toHaveCount(0);

    await rows.first().click();
    await page.waitForURL(/\/channels\//);

    // 对话和面板并排；「概览」开着。
    await expect.poll(() => panelShown(page)).toBe(true);
    await expect.poll(() => besideConversation(page)).toBe(true);
    await expect(page.locator('[data-panel-toggle]')).toHaveAttribute('aria-pressed', 'true');
  });
});
