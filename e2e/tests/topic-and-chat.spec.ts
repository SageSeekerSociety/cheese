import { test, expect } from '@playwright/test';
import { apiLogin, openFirstProject } from './helpers';

test.describe('Topics and chat', () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  // 新建频道在「浏览频道」那一页：先起名，建好就打开它，侧栏上多出这一行。
  test('creating a channel from 浏览频道 opens it in the sidebar', async ({ page }) => {
    const rows = await openFirstProject(page);
    const before = await rows.count();
    const projectId = /\/projects\/([^/]+)/.exec(page.url())?.[1];

    await page.goto(`/projects/${projectId}/channels`);
    await page.getByRole('button', { name: '新建频道' }).first().click();
    const name = `e2e 频道 ${Date.now()}`;
    await page.getByLabel('频道名称').fill(name);
    await page.locator('.v-overlay').getByRole('button', { name: '新建频道' }).click();

    await expect(page).toHaveURL(/\/topics\/[^/]+$/);
    await expect(rows).toHaveCount(before + 1);
    await expect(page.locator('.topic-row.is-active')).toContainText(name);
  });

  // 打开一个项目，第一屏是**项目总览**，不是任何一个聊天（见
  // frontend/src/views/workspace/WorkspaceEntry.vue）。单独钉一条，是因为这是每个
  // 人每天的第一眼：它改回去的时候，改的人应该在这里被拦下，而不是在别的测试的
  // beforeEach 里表现为一个看不懂的失败——那正是这条断言存在之前发生的事。
  test('opening a project lands on its overview, not a chat', async ({ page }) => {
    await openFirstProject(page);
    await expect(page).toHaveURL(/\/projects\/[^/]+\/overview(\?|$)/);
    await expect(page.getByTestId('overview-document')).toBeVisible();
  });

  test('sending a chat message shows it in the conversation', async ({ page }) => {
    const rows = await openFirstProject(page);
    // 进项目落在项目总览，所以这里要显式打开一个话题。用已有的那个而不是新建一个：
    // 建话题由上面那条覆盖，这一条钉的是输入框到 WS 那条发送路径本身。
    await rows.first().click();
    const composer = page.locator('.composer-input textarea').first();
    await expect(composer).toBeEnabled({ timeout: 15_000 }); // enabled only once the topic's WS connects

    const message = `e2e message ${Date.now()}`;
    await composer.fill(message);
    await composer.press('Enter');

    await expect(page.getByTestId('chat-scroll').getByText(message)).toBeVisible();
  });
  // 发出去的一句先作为「发送中」那一行立刻显示，落库后换成真的那一条。换的那一刻
  // 它不能挪：两行高度差一点，停在底部的人就看见整栏往上跳一下——这句话自己连着
  // 上面所有的消息一起。第二句是同一个人的续话（没有名字那一行），单独钉一次。
  test('a sent message stays where it is when it is delivered', async ({ page }) => {
    // Hold each send long enough to look at the pending row before the reply lands.
    await page.route('**/api/topics/*/messages', async (route) => {
      if (route.request().method() === 'POST') await new Promise((r) => setTimeout(r, 1500));
      await route.continue();
    });
    const rows = await openFirstProject(page);
    await rows.first().click();
    const composer = page.locator('.composer-input textarea').first();
    await expect(composer).toBeEnabled({ timeout: 15_000 });
    const pane = page.getByTestId('chat-scroll');

    for (const message of [`first ${Date.now()}`, `then ${Date.now()}`]) {
      await composer.fill(message);
      await composer.press('Enter');
      const row = pane.locator('.im-row', { hasText: message });
      const text = row.getByText(message, { exact: true });
      await expect(row).toHaveClass(/im-row--pending/);
      // Measure once the row's entrance motion has finished.
      await expect.poll(() => row.evaluate((el) => el.getAnimations({ subtree: true }).length)).toBe(0);
      const pending = await text.boundingBox();
      await expect(row).not.toHaveClass(/im-row--pending/, { timeout: 10_000 });
      await expect.poll(() => row.evaluate((el) => el.getAnimations({ subtree: true }).length)).toBe(0);
      const delivered = await text.boundingBox();
      expect(delivered!.y, `"${message}" moved when it was delivered`).toBeCloseTo(pending!.y, 0);
    }
  });

  // 进房间首屏就该是一屏历史，而不是一条飘在半空的消息。最新那一段几乎全是
  // `in_room:false` 的回合事件：只读一页（PAGE_SIZE=50 块）常常一行都画不出来，铺满之前
  // 就把骨架撤掉，看见的就是一两条、下面一大片空白，往上也翻不动——这一窗没长高，就没有
  // 下一次滚动事件。还欠着更早的历史（那条 loader 在）时，这一窗必须已经高过一屏、滚得动。
  test('entering a topic first paints a screenful of history', async ({ page }) => {
    const rows = await openFirstProject(page);
    await rows.first().click();
    const pane = page.getByTestId('chat-scroll');
    await expect(pane).toBeVisible();
    // 还有更早的历史才会画那条 loader；它在就说明这一窗不是全部。
    if (await page.getByTestId('chat-older-loader').count()) {
      const { sh, ch } = await pane.evaluate((el) => ({ sh: el.scrollHeight, ch: el.clientHeight }));
      expect(sh, '还有更早的历史时，首屏这一窗该已经铺满一屏').toBeGreaterThan(ch);
    }
  });
});
