import { test, expect, type Page } from '@playwright/test';
import { api, apiLogin, openFirstProject } from './helpers';

// 话题命名 (backend topic/naming.py), watched from the sidebar: an unnamed room
// gets a name from its first message, the name is re-checked once the room has
// said more, an automatic rename can be undone from the line that announces
// it, a suggestion only lands on enter, and a project on manual naming is left
// alone. The model is stub-gateway.mjs, whose titles start with "E2E " — these
// specs check the flow, never the wording.

const activeTitle = (page: Page) => page.locator('.topic-row.is-active .topic-title .text-truncate');

async function newRoom(page: Page) {
  await page.locator('[title="新建话题"]').click();
  await expect(activeTitle(page)).toHaveText('新话题');
  const composer = page.locator('.composer-input textarea').first();
  await expect(composer).toBeEnabled({ timeout: 15_000 });
  return composer;
}

async function say(page: Page, text: string) {
  const composer = page.locator('.composer-input textarea').first();
  await composer.fill(text);
  await composer.press('Enter');
  await expect(page.getByTestId('chat-scroll').getByText(text)).toBeVisible();
}

async function openRowMenu(page: Page) {
  const row = page.locator('.topic-row.is-active');
  await row.hover();
  await row.locator('.row-actions__btn').click();
}

const menuItem = (page: Page, name: string) =>
  page.locator('.v-overlay .v-list-item').filter({ hasText: new RegExp(`^${name}$`) });

function projectIdOf(page: Page): string {
  const id = /\/projects\/([^/]+)/.exec(page.url())?.[1];
  if (!id) throw new Error(`not on a project page: ${page.url()}`);
  return id;
}

test.describe('Topic naming', () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test('an unnamed room is named from its first message', async ({ page }) => {
    await openFirstProject(page);
    await newRoom(page);
    await say(page, '帮我排查 dev 机器外网访问很慢');
    await expect(activeTitle(page)).toHaveText(/^E2E /, { timeout: 20_000 });
    await expect(activeTitle(page)).toHaveAttribute('title', /自动命名/);
  });

  test('a rename after more messages is announced and can be undone', async ({ page }) => {
    await openFirstProject(page);
    await newRoom(page);
    await say(page, '帮我排查 dev 机器外网访问很慢');
    await expect(activeTitle(page)).toHaveText(/^E2E /, { timeout: 20_000 });
    const first = (await activeTitle(page).textContent())?.trim() ?? '';

    // A third message from a person is when the opening name is checked.
    await say(page, '先看一下 nginx 的日志');
    await say(page, '其实问题在 Valkey 连接池');
    const line = page.getByTestId('chat-scroll').getByText('标题自动更新为');
    await expect(line).toBeVisible({ timeout: 20_000 });
    await expect(activeTitle(page)).not.toHaveText(first);

    await page.getByTestId('chat-scroll').getByRole('button', { name: '撤销' }).click();
    await expect(activeTitle(page)).toHaveText(first);
    // Undone means a person chose it: the room can be handed back.
    await openRowMenu(page);
    await expect(menuItem(page, '恢复自动命名')).toBeVisible();
  });

  test('a suggested name lands only when confirmed', async ({ page }) => {
    await openFirstProject(page);
    await newRoom(page);
    await say(page, '整理这周三份会议纪要');
    await expect(activeTitle(page)).toHaveText(/^E2E /, { timeout: 20_000 });

    await openRowMenu(page);
    await menuItem(page, '智能重命名').click();
    const field = page.locator('.topic-row.is-active .rename-field input');
    await expect(field).toHaveValue(/^E2E /, { timeout: 20_000 });
    await field.fill('会议纪要周报');
    await field.press('Enter');
    await expect(activeTitle(page)).toHaveText('会议纪要周报');
    await expect(activeTitle(page)).not.toHaveAttribute('title', /自动命名/);
  });

  test('a project on manual naming is left alone', async ({ page }) => {
    await openFirstProject(page);
    const projectId = projectIdOf(page);
    try {
      await page.goto(`/projects/${projectId}/settings`);
      const manual = page.getByRole('radio', { name: /手动命名/ });
      await manual.click();
      await expect(manual).toHaveAttribute('aria-checked', 'true');

      await openFirstProject(page);
      await newRoom(page);
      await say(page, '帮我排查 dev 机器外网访问很慢');
      // Nothing to wait for but time: give naming the chance it would have had.
      await page.waitForTimeout(5_000);
      await expect(activeTitle(page)).toHaveText('新话题');
    } finally {
      await api(page, 'put', `/projects/${projectId}/topic-naming`, { mode: 'auto' });
    }
  });
});
