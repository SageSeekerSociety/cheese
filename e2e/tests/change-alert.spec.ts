/** 平台发的那一条变更提醒，人在项目首页读得到，点得进它说的那个话题。
 *
 * 它以前写进库没有任何界面读得到（收件箱查询只放行决策请求和验收卡），点它亮着
 * 项目角标、列表里却一条也没有。这一条从写入那一步开始走完整条路：走 `cheese
 * notify` 用的那个接口写一条，在项目首页看到它，点「去话题」落到那个话题上。
 */
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { api, login, openFirstProject } from './helpers';

function projectIdOf(page: Page): string {
  const id = page.url().match(/\/projects\/([0-9a-f-]{36})/)?.[1];
  if (!id) throw new Error(`当前页不是项目工作台：${page.url()}`);
  return id;
}

test.describe('变更提醒', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await openFirstProject(page);
  });

  test('写一条出来，项目首页读得到，点「去话题」落到它说的那个话题', async ({ page }) => {
    const projectId = projectIdOf(page);
    const stamp = Date.now();

    // 通知指向一个话题：通知只是提醒，东西在话题里，所以先开一间当落点。
    const topic = (await api(page, 'post', '/topics', {
      project_id: projectId,
      title: `提醒落点 ${stamp}`,
    })) as { id: string };

    const title = `变更提醒 ${stamp}`;
    const body = `修了预览的转圈 ${stamp}`;
    // 平台自己那条写入路径 —— 芝士干活时用的就是它。
    await api(page, 'post', `/projects/${projectId}/alerts`, {
      level: 'light',
      kind: 'change_alert',
      title,
      body,
      target_handle: 'alice',
      topic_id: topic.id,
    });

    await page.goto(`/projects/${projectId}/running`);
    const asked = page.locator('.asked');
    await expect(asked).toBeVisible();
    // 标题说的是摆着的那条：一条变更提醒不是「等你决定」的事。
    await expect(asked).toContainText('变更提醒');
    await expect(asked).toContainText(title);
    await expect(asked).toContainText(body);

    await asked.getByRole('button', { name: '去话题' }).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/topics/${topic.id}`),
    );
  });

  test('读过就收起来，角标跟着灭', async ({ page }) => {
    const projectId = projectIdOf(page);
    const stamp = Date.now();
    const title = `收起来的提醒 ${stamp}`;
    await api(page, 'post', `/projects/${projectId}/alerts`, {
      level: 'light',
      kind: 'change_alert',
      title,
      body: '看一眼就够',
      target_handle: 'alice',
    });

    // 没有话题的那一条给不出去处，所以不摆「去话题」。
    await page.goto(`/projects/${projectId}/running`);
    const asked = page.locator('.asked');
    await expect(asked).toContainText(title);
    await expect(asked.getByRole('button', { name: '去话题' })).toHaveCount(0);

    await asked.getByRole('button', { name: '知道了' }).click();
    await expect(asked).not.toContainText(title);
  });
});
