/** 平台发的那一条变更提醒，人在「待办」里读得到，点得进它说的那个频道。
 *
 * 它曾两次写进库却没有任何界面读得到：先是收件箱查询只放行决策请求和验收卡，后来
 * 是看板拆掉、读它的那一叠跟着走了。这一条从写入那一步开始走完整条路：走 `cheese
 * notify` 用的那个接口写一条，在「待办」里看到它，点进它说的频道，读过标掉。
 */
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { api, apiLogin, openFirstProject } from './helpers';

function projectIdOf(page: Page): string {
  const id = page.url().match(/\/projects\/([0-9a-f-]{36})/)?.[1];
  if (!id) throw new Error(`当前页不是项目工作台：${page.url()}`);
  return id;
}

test.describe('变更提醒', () => {
  // 这几条落在「第一个项目」上，所有 e2e 用例共用的那一个（alice 的种子项目）。写下
  // 的未读提醒会出现在别的用例打开的「待办」里，所以每条读完清干净。
  let projectId = '';

  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
    await openFirstProject(page);
    projectId = projectIdOf(page);
  });

  test.afterEach(async ({ page }) => {
    // beforeEach 半路挂掉时没有项目可清，别让收尾再报一条把人引开。
    if (!projectId) return;
    await api(page, 'post', `/projects/${projectId}/alerts/read-all`);
  });

  test('写一条出来，「待办」里读得到，点进它说的那个频道', async ({ page }) => {
    const stamp = Date.now();

    // 通知指向一个频道：通知只是提醒，东西在频道里，所以先开一间当落点。
    const topic = (await api(page, 'post', '/topics', {
      project_id: projectId,
      title: `提醒落点 ${stamp}`,
    })) as { id: string };

    const title = `变更提醒 ${stamp}`;
    const body = `修了预览的转圈 ${stamp}`;
    // 平台自己那条写入路径：芝士干活时用的就是它。
    await api(page, 'post', `/projects/${projectId}/alerts`, {
      level: 'light',
      kind: 'change_alert',
      title,
      body,
      target_handle: 'alice',
      topic_id: topic.id,
    });

    await page.goto('/inbox');
    const row = page.locator('.inbox-item', { hasText: title });
    await expect(row).toContainText('变更提醒');
    await expect(row).toContainText(body);

    await row.getByText(title).click();
    await expect(page).toHaveURL(
      new RegExp(`/projects/${projectId}/topics/${topic.id}`),
    );
  });

  test('读过就标掉，不再留在「待办」里', async ({ page }) => {
    const stamp = Date.now();
    const title = `标掉的提醒 ${stamp}`;
    await api(page, 'post', `/projects/${projectId}/alerts`, {
      level: 'light',
      kind: 'change_alert',
      title,
      body: '看一眼就够',
      target_handle: 'alice',
    });

    await page.goto('/inbox');
    const row = page.locator('.inbox-item', { hasText: title });
    await row.getByRole('button', { name: '标为已读' }).click();
    await expect(page.getByText(title)).toHaveCount(0);

    // 不是只从这一页上摘掉：重新读一遍，它也不在了。
    await page.reload();
    await expect(page.getByRole('heading', { name: '等你处理' })).toBeVisible();
    await expect(page.getByText(title)).toHaveCount(0);
  });

  test('一个项目里不止一条时，一下「全部标为已读」收走整队', async ({ page }) => {
    const stamp = Date.now();
    const first = `整队提醒甲 ${stamp}`;
    const second = `整队提醒乙 ${stamp}`;
    for (const title of [first, second]) {
      await api(page, 'post', `/projects/${projectId}/alerts`, {
        level: 'light',
        kind: 'change_alert',
        title,
        body: '一起收',
        target_handle: 'alice',
      });
    }

    await page.goto('/inbox');
    await expect(page.getByText(second)).toBeVisible();
    const group = page.locator('.inbox__project', { hasText: second });
    await group.getByRole('button', { name: '全部标为已读' }).click();

    // 一百条不该要一百下：这一下把两条一起收掉。
    await expect(page.getByText(first)).toHaveCount(0);
    await expect(page.getByText(second)).toHaveCount(0);
  });
});
