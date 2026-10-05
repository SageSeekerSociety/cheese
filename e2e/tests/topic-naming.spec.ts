import { test, expect, type Page } from '@playwright/test';
import { api, apiLogin, openFirstProject } from './helpers';

// 话题命名 (backend topic/naming.py), watched from the sidebar: an unnamed room
// gets a name from its first message, the name is re-checked once the room has
// said more, an automatic rename can be undone from the line that announces
// it, a name a person typed is final, and a project on manual naming is left
// alone. The model is stub-gateway.mjs, whose titles start with "E2E " — these
// specs check the flow, never the wording.

const activeTitle = (page: Page) => page.locator('.topic-row.is-active .topic-title .text-truncate');

async function newRoom(page: Page) {
  await page.locator('[title="新建频道"]').click();
  await expect(activeTitle(page)).toHaveText('新频道');
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
  });

  test('a row menu offers rename and archive, nothing else', async ({ page }) => {
    await openFirstProject(page);
    await newRoom(page);
    await say(page, '整理这周三份会议纪要');
    await expect(activeTitle(page)).toHaveText(/^E2E /, { timeout: 20_000 });

    await openRowMenu(page);
    await expect(menuItem(page, '重命名')).toBeVisible();
    await expect(menuItem(page, '归档')).toBeVisible();
    // Naming is the platform's job and a person's rename is final: there is no
    // asking for a name, and no handing the room back.
    await expect(
      page.locator('.v-overlay .v-list-item').filter({ hasText: /智能重命名|恢复自动命名/ })
    ).toHaveCount(0);
  });

  test('a name a person typed is kept and the platform leaves it alone', async ({ page }) => {
    await openFirstProject(page);
    await newRoom(page);
    await say(page, '整理这周三份会议纪要');
    await expect(activeTitle(page)).toHaveText(/^E2E /, { timeout: 20_000 });

    await openRowMenu(page);
    await menuItem(page, '重命名').click();
    const field = page.locator('.topic-row.is-active .rename-field input');
    await field.fill('会议纪要周报');
    await field.press('Enter');
    await expect(activeTitle(page)).toHaveText('会议纪要周报');
    await expect(activeTitle(page)).not.toHaveAttribute('title', /自动命名/);

    // More of the same room, said after the rename: what would trigger a
    // follow-up rename for a platform-named room.
    await say(page, '先看一下 nginx 的日志');
    await say(page, '其实问题在 Valkey 连接池');
    await say(page, '把结论写进实况文档');
    await page.waitForTimeout(5_000);
    await expect(activeTitle(page)).toHaveText('会议纪要周报');
  });

  test('a project on manual naming is left alone', async ({ page }) => {
    await openFirstProject(page);
    const projectId = projectIdOf(page);
    try {
      await page.goto(`/projects/${projectId}/settings/topic-naming`);
      const manual = page.getByRole('radio', { name: /手动命名/ });
      await manual.click();
      await expect(manual).toHaveAttribute('aria-checked', 'true');

      // Settings cover the app until closed; the rail underneath takes no clicks.
      await page.keyboard.press('Escape');
      await openFirstProject(page);
      await newRoom(page);
      await say(page, '帮我排查 dev 机器外网访问很慢');
      // Nothing to wait for but time: give naming the chance it would have had.
      await page.waitForTimeout(5_000);
      await expect(activeTitle(page)).toHaveText('新频道');
    } finally {
      await api(page, 'put', `/projects/${projectId}/topic-naming`, { mode: 'auto' });
    }
  });
});
