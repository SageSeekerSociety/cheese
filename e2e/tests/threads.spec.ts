import { test, expect, type Page } from '@playwright/test';
import { api, apiLogin } from './helpers';

// 支线：主线上叫芝士，回答不落在主线上，落在那条消息下面的支线里。组件测试钉住了
// 每一块自己的规矩（悬停条上的那颗、打开就记已读、输入框先带上 @），这里钉的是接缝：
// 发出去的那条消息下面真的长出那一行，点开真的是它的支线，芝士那一轮真的在支线里。

async function openChannel(page: Page) {
  const projects = (await api(page, 'get', '/projects')).data as { id: string }[];
  const project = projects[0];
  expect(project, 'alice 名下没有项目').toBeTruthy();
  const room = (await api(page, 'post', '/topics', {
    project_id: project.id,
    title: `支线 ${Date.now()}`,
  })) as { id: string };
  await page.goto(`/projects/${project.id}/topics/${room.id}`);
  const composer = page.locator('.composer-input textarea').first();
  await expect(composer).toBeEnabled({ timeout: 30_000 });
  return composer;
}

test.describe('支线', () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test('主线上叫芝士：它在这条消息的支线里回答', async ({ page }) => {
    const composer = await openChannel(page);
    const button = page.locator('.summon-btn');
    await expect(button).toBeEnabled({ timeout: 15_000 });

    const text = `e2e thread question ${Date.now()}`;
    await composer.fill(text);
    await button.click();
    await composer.press('Enter');

    const main = page.getByTestId('chat-scroll').first();
    const asked = main.locator('.im-text', { hasText: text }).last();
    await expect(asked).toBeVisible({ timeout: 15_000 });

    // 消息下面那一行：还在回答时写「正在回复」，有了回复写几条回复。点它打开支线。
    const line = main.locator('[data-testid="thread-replying"], [data-testid="thread-line"]').last();
    await expect(line).toBeVisible({ timeout: 15_000 });
    await line.click();

    const pane = page.getByTestId('thread-pane');
    await expect(pane).toBeVisible({ timeout: 15_000 });
    await expect(pane.locator('.thread-root', { hasText: text })).toBeVisible();

    // 芝士那一轮留下的东西（回答，或者这一轮为什么没成的那一行）落在支线里。
    await expect(pane.locator('[data-row-id]').first()).toBeVisible({ timeout: 60_000 });
  });

  test('悬停一条消息「在支线中回复」，回复挂在它下面', async ({ page }) => {
    const composer = await openChannel(page);
    const text = `e2e thread root ${Date.now()}`;
    await composer.fill(text);
    await composer.press('Enter');

    const main = page.getByTestId('chat-scroll').first();
    const row = main.locator('.im-text', { hasText: text }).last();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.hover();
    await page.getByTestId('reply-in-thread').click();

    const pane = page.getByTestId('thread-pane');
    await expect(pane).toBeVisible({ timeout: 15_000 });
    const reply = `e2e thread reply ${Date.now()}`;
    const inThread = pane.locator('.composer-input textarea');
    await expect(inThread).toBeEnabled({ timeout: 20_000 });
    await inThread.fill(reply);
    await inThread.press('Enter');
    await expect(pane.locator('.im-text', { hasText: reply })).toBeVisible({ timeout: 15_000 });

    // 主线上那条消息下面写着一条回复；回复本身不在主线上。
    await expect(main.locator('[data-testid="thread-line"]').last()).toContainText('1', { timeout: 15_000 });
    await expect(main.locator('.im-text', { hasText: reply })).toHaveCount(0);
  });
});
