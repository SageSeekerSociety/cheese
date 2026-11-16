import { test, expect } from '@playwright/test';
import { apiLogin, openFirstProject } from './helpers';

// 平板横放那一档：960（Vuetify 的 mdAndUp）到 1180（COMPACT_DESKTOP_MAX_WIDTH）为止。
//
// 这一档里常驻的二级侧栏（280px）把正文挤得太窄，所以侧栏默认收起，rail 顶上那颗
// 开关开合它（记住了人的选择）。
//
// 右侧面板按主区（对话加面板）的宽度分三档（`useWorkspaceLayout.ts` 的 `panelMode`）：
// 1000 起并排、默认开着；840 到 1000 并排、默认收着，页头「概览」打开；更窄放不下两栏，
// 面板浮在对话上。并排时「概览」记住这个人的选择。
//
// 这些差别**只在真浏览器里有**（宽度是算出来的布局，不是布尔 prop），所以守在这里：
// vitest 用的是 happy-dom 的固定宽度，量不到「同一份 DOM 在两个窗口宽下不一样」。
//
// **样本宽度在 2026-11-16 整体左移了 64px**（1024 → 960）。面板三档看的是**主区**宽度，
// 而主区原来是「窗口 − 左边那一列 64px 方块栏」。那一列被拿掉之后（#5277），平板横放这
// 一档里主区就等于窗口宽度，同样一条断言要落进同一档，窗口就得窄掉那 64px。三档本身
// 没动：1000、840 这两条线是从内容需求推出来的（并排时对话至少留 480、面板至少 360），
// #5277 没有改变这个需求，变的只是「多宽的窗口能凑出这么宽的主区」。
//
// 副作用是**浮层那一档（主区 < 840）今天已经不可能靠缩窗口达到了**：平板横放里主区
// 等于窗口（≥ 960），宽档里主区 = 窗口 − 侧栏（1181 时是 900）；两头都够不着 840。
// 它还是在的，只是换成两条真路径进得去——把侧栏拖宽（`cheesex.sidebarWidth` 到 480，
// 1181 时主区剩 700），或者打开个人芝士（它自己占掉右边 380）。下面那条浮层用例走的是
// 拖宽侧栏这条，因为它是纯粹布局的一件事，不用把助手的面板状态也拉进来。
//
// 为什么先开到 1280、进屋、再缩到样本宽度：话题列表长在二级侧栏里，而那一档默认收着，
// `openFirstProject` 要等的行在收起状态里不可见。先在宽档进屋（侧栏常驻、行可见），
// 再缩窗口——这也正是一条真路径：人横过来/缩窗口那一刻布局就换档。

test.describe('平板横放（960–1180）', () => {
  test('960：侧栏默认收起；面板默认收着，「概览」把它并排打开并记住', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/channels\//);

    // 宽档（1280 > 1180）：侧栏常驻，顶栏上没有它的开关。
    await expect(page.locator('[data-sidebar-toggle]')).toHaveCount(0);

    // 落到平板横放那一档。主区 959：并排放得下（≥ 840），但还不够宽（< 1000），默认收着。
    await page.setViewportSize({ width: 960, height: 900 });

    // 侧栏默认收起：rail 顶上那颗开关在，aria-expanded=false，抽屉不是 active。
    const sidebarToggle = page.locator('[data-sidebar-toggle]');
    await expect(sidebarToggle).toBeVisible();
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#secondary-sidebar')).not.toHaveClass(/v-navigation-drawer--active/);

    // 主区 959：不到 1000，面板默认收着，只剩对话。
    const panelToggle = page.locator('[data-panel-toggle]');
    await expect(panelToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#topic-panel')).toBeHidden();
    await expect(page.locator('.pane-resizer')).toHaveCount(0);

    // 点「概览」：面板并排开在对话右边（两栏中间那条可拖的分隔在），不是浮层。
    await panelToggle.click();
    await expect(panelToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#topic-panel')).toBeVisible();
    await expect(page.locator('.pane-resizer')).toBeVisible();
    await expect(page.locator('.panel-scrim')).toHaveCount(0);

    // 这是人的选择：刷新回来还开着。
    await page.reload();
    await expect(page.locator('[data-panel-toggle]')).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#topic-panel')).toBeVisible();

    // 二级侧栏那颗开关：点开，抽屉成 active；Esc 收起，焦点还回来。
    await sidebarToggle.click();
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('#secondary-sidebar')).toHaveClass(/v-navigation-drawer--active/);

    await page.keyboard.press('Escape');
    await expect(sidebarToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(sidebarToggle).toBeFocused();
  });

  test('960：从侧栏浮层点进一个房间就收起它，而且不把这次收起记成人的选择', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await apiLogin(page);
    await openFirstProject(page);

    // 落到平板横放那一档：侧栏默认收起，顶栏上那颗开关在，话题列表在浮层里。
    await page.setViewportSize({ width: 960, height: 900 });
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
    // 浮层那一档今天靠缩窗口进不去（见文件头：平板横放里主区等于窗口 ≥ 960，宽档里
    // 是窗口 − 侧栏 ≥ 900，两头都在 840 以上）。换成把侧栏拖到最宽：1181 − 480 = 701。
    // 这是同一件事，而且是条真路径——人把侧栏拉宽之后，主区就真的放不下两栏了。
    await page.setViewportSize({ width: 1181, height: 900 });
    await page.addInitScript(() => localStorage.setItem('cheesex.sidebarWidth', '480'));
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/channels\//);
    const main = await page.locator('.panes').first().boundingBox();
    expect(main!.width, '这个用例要主区窄于 840').toBeLessThan(840);

    // 浮层默认收着。
    const panelToggle = page.locator('[data-panel-toggle]');
    await expect(panelToggle).toHaveAttribute('aria-expanded', 'false');
    await expect(page.locator('#topic-panel')).toBeHidden();

    // 打开：浮在对话上，背后一层遮罩，没有两栏中间的分隔。
    await panelToggle.click();
    await expect(page.locator('#topic-panel')).toBeVisible();
    await expect(page.locator('.panel-scrim')).toBeVisible();
    await expect(page.locator('.pane-resizer')).toHaveCount(0);

    // Esc 收起浮层，焦点回到打开它的那颗开关。
    await page.keyboard.press('Escape');
    await expect(page.locator('#topic-panel')).toBeHidden();
    await expect(panelToggle).toBeFocused();
  });

  test('960：地址里点名的 tab 仍然把面板拉起来', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/channels\//);
    const topicUrl = page.url().split('?')[0];

    // 落到平板横放：地址里没有 ?tab=，面板收着。
    await page.setViewportSize({ width: 960, height: 900 });
    await expect(page.locator('#topic-panel')).toBeHidden();
    await expect(page.locator('[data-panel-toggle]')).toHaveAttribute('aria-expanded', 'false');

    // 别人发来的链接（?tab=）是「有人打开了这一格」，面板得跟着开。
    await page.goto(`${topicUrl}?tab=threads`);
    await expect(page.locator('#topic-panel')).toBeVisible();
    await expect(page.locator('[data-panel-toggle]')).toHaveAttribute('aria-expanded', 'true');
  });

  test('1440：侧栏常驻，主区够宽，面板默认并排开着', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);

    // 侧栏常驻，rail 上不收它。
    await expect(page.locator('[data-sidebar-toggle]')).toHaveCount(0);

    await rows.first().click();
    await page.waitForURL(/\/channels\//);

    // 对话和面板并排，中间那条分隔在；「概览」开着。
    await expect(page.locator('.pane-resizer')).toBeVisible();
    await expect(page.locator('#topic-panel')).toBeVisible();
    await expect(page.locator('[data-panel-toggle]')).toHaveAttribute('aria-expanded', 'true');
  });
});
