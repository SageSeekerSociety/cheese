/** 一个房间派出去的活，人在屏幕上找不找得到。
 *
 * 派活的入口本来就是 API（芝士自己拆的活占绝大多数），所以布景走 API；**断言全在
 * 界面上**，因为这里要钉的正是「派出去之后，人看不看得见、点不点得进去」。
 *
 * 这一层能钉的只有这些。阶段 5 另外两件事 —— 排队/出队的圆环、封口期提示 —— 在
 * e2e 里做不出可靠的布景，原因是环境本身：
 *
 *   - 槽位是**正在跑的轮次**占住的，不是「活还开着」占住的。e2e 环境没有 agent
 *     镜像，kickoff 三秒就失败、槽位自己放开，队列根本排不起来，写出来就是一条
 *     看运气的测试。排队/出队钉在 tests/integration/test_task_threads.py —— 那里
 *     能直接摆出 residency，是确定的。
 *   - 封口要 `x-cheese-token`（每轮次令牌）。这是对的：封口是芝士的动作，浏览器
 *     里的人本来就不该能递卡。封口期提示钉在 TaskProgress.seal.spec.ts 和
 *     tests/integration/test_tree_sealing_and_quick_check.py。
 */
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { api, login, openFirstProject } from './helpers';

function projectIdOf(page: Page): string {
  const id = page.url().match(/\/projects\/([0-9a-f-]{36})/)?.[1];
  if (!id) throw new Error(`当前页不是项目工作台：${page.url()}`);
  return id;
}

/** 一间空房间。每条用例一间，互不干扰。 */
async function freshRoom(page: Page, title: string) {
  const project_id = projectIdOf(page);
  const room = (await api(page, 'post', '/topics', { project_id, title })) as { id: string };
  return room.id;
}

async function dispatch(page: Page, roomId: string, title: string) {
  return (await api(page, 'post', `/topics/${roomId}/split`, { title, created_by: 'alice' })) as {
    id: string;
    queued?: boolean;
  };
}

test.describe('房间里派出去的活', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
    await openFirstProject(page);
  });

  test('派出去的活出现在房间总览里，点一下就进它自己的页面', async ({ page }) => {
    const projectId = projectIdOf(page);
    const stamp = Date.now();
    const roomId = await freshRoom(page, `派活 ${stamp}`);
    const first = await dispatch(page, roomId, `第一件事 ${stamp}`);
    await dispatch(page, roomId, `第二件事 ${stamp}`);

    await page.goto(`/projects/${projectId}/topics/${roomId}?tab=overview`);
    const progress = page.locator('.task-progress');
    await expect(progress).toBeVisible();

    // 两条都在，而且总数说得出来 —— 折起来的时候这一行是唯一的线索。
    await expect(progress.locator('.task-progress__tally')).toContainText('2 件');
    await expect(progress.getByText(`第一件事 ${stamp}`)).toBeVisible();
    await expect(progress.getByText(`第二件事 ${stamp}`)).toBeVisible();
    // 每条活带一个状态圆点。不断言是哪个状态：这一条钉的是「有没有」，
    // 具体哪个状态由 lib/board.spec.ts 逐条钉。
    await expect(progress.locator('.task-row .board-dot')).toHaveCount(2);

    // 点条目进这条活自己的 chat 页 —— 总览里的一行必须是个入口，不然它只是一张表。
    await progress.getByText(`第一件事 ${stamp}`).click();
    await expect(page).toHaveURL(new RegExp(`/topics/${first.id}`));
  });

  test('侧栏的「看板」是一直在的入口，进去是整个项目的视角', async ({ page }) => {
    const stamp = Date.now();
    const roomId = await freshRoom(page, `跨房间 ${stamp}`);
    await dispatch(page, roomId, `跨房间的活 ${stamp}`);

    // 从侧栏那条常驻入口进去，而不是直接敲地址：这一条要钉的一半正是「找得到」。
    await page.locator('.pinned-row').filter({ hasText: '看板' }).first().click();
    // 路由名和路径仍是 running：改地址会打断所有已经发出去的链接，改的只是这块
    // 界面叫什么。
    await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}\/running/);

    const view = page.locator('.running-work');
    await expect(view.getByRole('heading', { name: '看板' })).toBeVisible();
    // 板要答的是「该谁动」，所以它得说出各列各有几件；一件都没有的时候要明说，
    // 否则一块空板读起来就是「这个项目没活」——而项目里可能有几百条。
    await expect(view).toContainText(/施工中|交付中|等你|暂无派出去的活/);
  });
});
