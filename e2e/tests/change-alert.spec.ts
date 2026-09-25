/** 平台发的那一条变更提醒，人在项目首页读得到，点得进它说的那个话题。
 *
 * 它以前写进库没有任何界面读得到（收件箱查询只放行决策请求和验收卡），点它亮着
 * 项目角标、列表里却一条也没有。这一条从写入那一步开始走完整条路：走 `cheese
 * notify` 用的那个接口写一条，在项目首页看到它，点「去话题」落到那个话题上。
 *
 * 2026-09-24 在本机这套栈上跑过：自己的后端 + 自己的前端（Playwright 的
 * webServer 从本工作树起）+ 一份迁移+种子好的库，真浏览器，2 passed。
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
  // 这几条落在「第一个项目」上 —— 所有 e2e 用例共用的那一个（alice 的种子项目），
  // 收件箱按项目 + 人读。写下的未读提醒会变成别人的前提：2026-09-24 上 CI，这里
  // 那条游到 room-work.spec.ts 去，「板里不另起标题」的断言就多出一个 h2 —— 它数
  // 的是 `.board` 里的标题，而这一叠正嵌在板容器里。所以每条读完清干净：点了「去
  // 话题」的那条没走完「收起来」，靠 afterEach 收尾（CI 上用例串行，前一条留下的
  // 东西后一条真的看得见）。
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

  test('写一条出来，项目首页读得到，点「去话题」落到它说的那个话题', async ({ page }) => {
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
    // 断言「这个标题不在了」，而不是「这一叠不在了」：答掉的最后一条会让整叠退场，
    // `.asked` 动画放完就从 DOM 里摘掉 —— 那时候 `not.toContainText` 两种情形都不
    // 满足（还在读得到 / 元素已经没了），会红在一个跟被测行为无关的时机上。
    await expect(page.getByText(title)).toHaveCount(0);
  });
});
