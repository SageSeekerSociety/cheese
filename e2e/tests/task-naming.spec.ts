import { test, expect, type Page } from '@playwright/test';
import { api, apiLogin, openFirstProject } from './helpers';

// 任务命名 (backend room_task/naming.py), watched from the sidebar: a task
// opened without a title gets a name from its first message, a name a person
// typed is final, and a project on manual naming is left alone. The model is
// stub-gateway.mjs, whose titles start with "E2E " — these specs check the
// flow, never the wording.

const activeTitle = (page: Page) => page.locator('.rail-task--selected .rail-task__title');

function projectIdOf(page: Page): string {
  const id = /\/projects\/([^/]+)/.exec(page.url())?.[1];
  if (!id) throw new Error(`not on a project page: ${page.url()}`);
  return id;
}

/** A task with no title, in a channel of its own, opened. */
async function newTask(page: Page) {
  const projectId = projectIdOf(page);
  const room = (await api(page, 'post', '/topics', {
    project_id: projectId,
    title: `命名 ${Date.now()}`,
  })) as { id: string };
  const task = (await api(page, 'post', `/topics/${room.id}/tasks`, {})) as { id: string };
  await page.goto(`/projects/${projectId}/topics/${room.id}/tasks/${task.id}`);
  await expect(activeTitle(page)).toHaveText('新任务');
  const composer = page.locator('.composer-input textarea').first();
  await expect(composer).toBeEnabled({ timeout: 15_000 });
  return task.id;
}

async function say(page: Page, text: string) {
  const composer = page.locator('.composer-input textarea').first();
  await composer.fill(text);
  await composer.press('Enter');
  await expect(page.getByTestId('chat-scroll').getByText(text)).toBeVisible();
}

test.describe('Task naming', () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
  });

  test('a task opened without a title is named from its first message', async ({ page }) => {
    await openFirstProject(page);
    await newTask(page);
    await say(page, '帮我排查 dev 机器外网访问很慢');
    await expect(activeTitle(page)).toHaveText(/^E2E /, { timeout: 20_000 });
    // The rename is quiet: nothing about it is said in the task.
    await expect(page.getByTestId('chat-scroll').getByText('标题自动更新为')).toHaveCount(0);
  });

  test('a name a person typed is kept and the platform leaves it alone', async ({ page }) => {
    await openFirstProject(page);
    const taskId = await newTask(page);
    await say(page, '整理这周三份会议纪要');
    await expect(activeTitle(page)).toHaveText(/^E2E /, { timeout: 20_000 });

    await api(page, 'post', `/topics/${taskId}/title`, { title: '会议纪要周报' });
    await expect(activeTitle(page)).toHaveText('会议纪要周报');

    // More said after the rename: what would bring a platform-named task up
    // for another look.
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
      await page.goto(`/projects/${projectId}/settings/task-naming`);
      const manual = page.getByRole('radio', { name: /手动命名/ });
      await manual.click();
      await expect(manual).toHaveAttribute('aria-checked', 'true');

      // Settings cover the app until closed; the rail underneath takes no clicks.
      await page.keyboard.press('Escape');
      await openFirstProject(page);
      await newTask(page);
      await say(page, '帮我排查 dev 机器外网访问很慢');
      // Nothing to wait for but time: give naming the chance it would have had.
      await page.waitForTimeout(5_000);
      await expect(activeTitle(page)).toHaveText('新任务');
    } finally {
      await api(page, 'put', `/projects/${projectId}/task-naming`, { mode: 'auto' });
    }
  });
});
