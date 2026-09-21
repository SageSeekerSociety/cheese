import { test, expect } from '@playwright/test';
import { login, openFirstProject } from './helpers';

test.describe('Topics and chat', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('creating a topic adds it to the sidebar as the active topic', async ({ page }) => {
    const rows = await openFirstProject(page);
    const before = await rows.count();

    // One click creates the room. A room is a group chat: the project's
    // default teammate is already seated, and any other teammate is invited
    // from the roster afterwards, the way a person is — there is no "whose
    // room is this" question to answer up front.
    await page.locator('[title="新建话题"]').click();

    await expect(rows).toHaveCount(before + 1);
    await expect(page.locator('.topic-row.is-active')).toHaveCount(1);
  });

  // 打开一个项目，第一屏是**看板**，不是任何一个聊天（见
  // frontend/src/views/workspace/WorkspaceEntry.vue）。单独钉一条，是因为这是每个
  // 人每天的第一眼：它改回去的时候，改的人应该在这里被拦下，而不是在别的测试的
  // beforeEach 里表现为一个看不懂的失败——那正是这条断言存在之前发生的事。
  test('opening a project lands on the board, not a chat', async ({ page }) => {
    await openFirstProject(page);
    await expect(page).toHaveURL(/\/projects\/[^/]+\/running(\?|$)/);
    await expect(page.locator('.board')).toBeVisible();
  });

  test('sending a chat message shows it in the conversation', async ({ page }) => {
    const rows = await openFirstProject(page);
    // 进项目落在看板，所以这里要显式打开一个话题。用已有的那个而不是新建一个：
    // 建话题由上面那条覆盖，这一条钉的是输入框到 WS 那条发送路径本身。
    await rows.first().click();
    const composer = page.locator('.composer-input textarea').first();
    await expect(composer).toBeEnabled({ timeout: 15_000 }); // enabled only once the topic's WS connects

    const message = `e2e message ${Date.now()}`;
    await composer.fill(message);
    await composer.press('Enter');

    await expect(page.getByTestId('chat-scroll').getByText(message)).toBeVisible();
  });
});
