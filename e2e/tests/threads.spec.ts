import { test, expect, type Page } from '@playwright/test';
import { api, apiLogin } from './helpers';

// 支线：主线上叫芝士，回答落在主线上；支线由人在一条消息下面开。组件测试钉住了每一块
// 自己的规矩（悬停条上的那颗、打开就记已读、输入框先带上 @），这里钉的是接缝：主线上
// 叫芝士不长出支线那一行，点「在支线中回复」真的开出这条消息的支线。

async function openChannel(page: Page) {
  const projects = (await api(page, 'get', '/projects')).data as { id: string }[];
  const project = projects[0];
  expect(project, 'alice 名下没有项目').toBeTruthy();
  const room = (await api(page, 'post', '/topics', {
    project_id: project.id,
    title: `支线 ${Date.now()}`,
  })) as { id: string };
  await page.goto(`/projects/${project.id}/topics/${room.id}`);
  const composer = page.locator('.composer-input .ProseMirror').first();
  await expect(composer).toBeEnabled({ timeout: 30_000 });
  return composer;
}

test.describe('支线', () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test('主线上叫芝士：它在主线上回答，消息下面不长出支线', async ({ page }) => {
    const composer = await openChannel(page);
    const button = page.locator('.summon__btn');
    await expect(button).toBeEnabled({ timeout: 15_000 });

    const text = `e2e main line question ${Date.now()}`;
    await composer.fill(text);
    await button.click();
    await composer.press('Enter');

    const main = page.getByTestId('chat-scroll').first();
    const asked = main.locator('.im-text', { hasText: text }).last();
    await expect(asked).toBeVisible({ timeout: 15_000 });

    // 芝士那一轮留下的东西（回答，或者这一轮为什么没成的那一行；e2e 里没有工作电脑，多半
    // 是后一种）落在主线上，排在问的那条后面。
    await expect(main.locator('[data-row-id]').last()).not.toContainText(text, { timeout: 60_000 });
    await expect(
      main.locator('[data-testid="thread-replying"], [data-testid="thread-line"], [data-testid="thread-failed"]')
    ).toHaveCount(0);
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
    const inThread = pane.locator('.composer-input .ProseMirror').first();
    await expect(inThread).toBeEnabled({ timeout: 20_000 });
    await inThread.fill(reply);
    await inThread.press('Enter');
    await expect(pane.locator('.im-text', { hasText: reply })).toBeVisible({ timeout: 15_000 });

    // 主线上那条消息下面写着一条回复；回复本身不在主线上。
    await expect(main.locator('[data-testid="thread-line"]').last()).toContainText('1', { timeout: 15_000 });
    await expect(main.locator('.im-text', { hasText: reply })).toHaveCount(0);
  });
});
