/** Dispatch through the API, then verify that people can find and open each
 * independent task in the room overview and project board. */
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { closeSync, openSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { api, apiToken, apiLogin, openFirstProject } from './helpers';

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
  return (await api(page, 'post', `/topics/${roomId}/split`, {
    title, created_by: 'alice', reviewer_handle: 'alice',
  })) as {
    id: string;
    queued?: boolean;
  };
}

test.describe('房间里派出去的活', () => {
  test.beforeEach(async ({ page }) => {
    await apiLogin(page);
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
    // 默认折着，清单要点开才有。
    await progress.locator('.task-progress__head').click();
    await expect(progress.getByText(`第一件事 ${stamp}`)).toBeVisible();
    await expect(progress.getByText(`第二件事 ${stamp}`)).toBeVisible();
    // 每条活带一个状态圆点。不断言是哪个状态：这一条钉的是「有没有」，
    // 具体哪个状态由 lib/board.spec.ts 逐条钉。
    await expect(progress.locator('.task-row .board-dot')).toHaveCount(2);

    // 点条目就地展开这张卡 —— 总览里的一行必须是个入口，不然它只是一张表。
    // 卡不是地点：地址留在房间上，卡的 id 进 query（T6 起）。
    await progress.getByText(`第一件事 ${stamp}`).click();
    await expect(page).toHaveURL(new RegExp(`/topics/${roomId}\\?.*card=${first.id}`));
    await expect(page.locator('.panel-card')).toBeVisible();
  });

  test('侧栏上的项目名就是回看板的入口，进去是整个项目的视角', async ({ page }) => {
    const stamp = Date.now();
    const roomId = await freshRoom(page, `跨房间 ${stamp}`);
    await dispatch(page, roomId, `跨房间的活 ${stamp}`);

    // 从侧栏那个常驻入口进去，而不是直接敲地址：这一条要钉的一半正是「找得到」。
    // 看板就是项目首页，所以侧栏上点项目名就到，不再单占一行。
    await page.locator('.rail-header__home').click();
    // 路由名和路径仍是 running：改地址会打断所有已经发出去的链接，改的只是这块
    // 界面叫什么。
    await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}\/running/);

    const view = page.locator('.board');
    // 这一页只有一个标题，写在和侧栏对齐的那条页头上：它说这一页是看板，项目名在
    // 侧栏顶上。板里不再另起标题，列头自己已经说明了它是什么。
    await expect(page.locator('.project-page__title')).toHaveText('看板');
    await expect(view.locator('h1, h2')).toHaveCount(0);
    // 板是按列排的，列本身要在 —— 这一页从一张平表变成看板，列就是那个变化。
    // 最右边那一列是「做出了什么」：三列任务从左到右是一条流水线，产物接在后面。
    await expect(view.locator('.board-col')).not.toHaveCount(0);
    await expect(view.locator('.board-col--made')).toContainText('做出了什么');
    // 板要答的是「该谁动」，所以它得说出各列各有几件；一件都没有的时候要明说，
    // 否则一块空板读起来就是「这个项目没活」——而项目里可能有几百条。
    await expect(view).toContainText(/施工中|交付中|待处理|暂无任务/);
  });
});


test('同名文件按任务打开，切换来源后草稿仍在', async ({ page }, testInfo) => {
  // This case also clones and pushes two worktrees before exercising the UI.
  test.setTimeout(120_000);
  await apiLogin(page);
  await openFirstProject(page);
  const project = projectIdOf(page);
  const room = await freshRoom(page, `文件来源 ${Date.now()}`);
  const first = await dispatch(page, room, '调整登录样式');
  const second = await dispatch(page, room, '修复登录校验');
  // The fixture commits through the native CLI and serves file requests through
  // an enrolled device, leaving the browser and backend paths unmodified.
  const log = openSync(testInfo.outputPath('machine.log'), 'w');
  const machine = spawn('uv', ['run', 'python', '-m', 'scripts.e2e_task_machine'], {
    cwd: '../backend', stdio: ['pipe', 'pipe', log],
  });
  closeSync(log);
  const exited = once(machine, 'exit');
  try {
    const ready = new Promise<void>((resolve, reject) => {
      machine.once('error', reject);
      machine.once('exit', code => reject(new Error(`Task machine exited: ${code}; see machine.log`)));
      machine.stdout.on('data', chunk => { if (chunk.toString().includes('ready\n')) resolve(); });
    });
    machine.stdin.end(JSON.stringify({
      api: `http://127.0.0.1:${process.env.E2E_BACKEND_PORT ?? '8081'}`,
      token: await apiToken(page), project, room, home: testInfo.outputPath('machine'),
      tasks: [{ id: first.id, content: 'first task\n' }, { id: second.id, content: 'second task\n' }],
    }));
    await ready;
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`/projects/${project}/topics/${room}?tab=changes`);
    const panel = page.locator('.panel-changes');
    const firstGroup = panel.getByRole('article', { name: '调整登录样式' });
    const secondGroup = panel.getByRole('article', { name: '修复登录校验' });
    // 每个任务自带的改动清单默认收起：这一行先只有标题、状态和文件数。
    await expect(firstGroup.getByRole('button', { name: /src\/login.txt/ })).toHaveCount(0);
    await firstGroup.getByRole('button', { name: /展开/ }).click();
    await secondGroup.getByRole('button', { name: /展开/ }).click();
    await expect(firstGroup.getByRole('button', { name: /src\/login.txt/ })).toBeVisible();
    await expect(secondGroup.getByRole('button', { name: /src\/login.txt/ })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('task-files-overview.png'), fullPage: true });
    await firstGroup.getByRole('button', { name: /src\/login.txt/ }).click();
    await expect(panel.locator('.source-heading')).toContainText('调整登录样式');
    await panel.getByRole('button', { name: '编辑', exact: true }).click();
    await panel.locator('.monaco-editor .view-lines').click();
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.type('my unsaved draft');
    await expect(panel.getByRole('button', { name: '保存', exact: true })).toBeEnabled();
    await panel.getByRole('button', { name: '切换来源' }).click();
    await page.getByRole('listbox', { name: '文件来源' }).getByText('修复登录校验', { exact: true }).click();
    await expect(panel.locator('.source-heading')).toContainText('修复登录校验');
    await expect(panel).toContainText('second task');
    await panel.getByRole('button', { name: '切换来源' }).click();
    await page.getByRole('listbox', { name: '文件来源' }).getByText('调整登录样式', { exact: true }).click();
    await expect(panel.locator('.monaco-editor')).toContainText('my unsaved draft');
    await expect(panel.getByRole('button', { name: '保存', exact: true })).toBeEnabled();
    await page.emulateMedia({ colorScheme: 'dark' });
    // The evidence is this editor panel; a full-page capture can stall in Chromium.
    await panel.screenshot({ path: testInfo.outputPath('task-files-draft-dark.png') });
    await panel.getByRole('button', { name: '保存', exact: true }).click();
    await expect(panel.getByRole('button', { name: '保存', exact: true })).toBeDisabled();
    const saved = await api(page, 'get', `/projects/${project}/file?path=src/login.txt&topic=${room}&task=${first.id}`);
    expect(saved.content).toBe('my unsaved draft');
    const untouched = await api(page, 'get', `/projects/${project}/file?path=src/login.txt&topic=${room}&task=${second.id}`);
    expect(untouched.content).toBe('second task\n');
    await panel.getByRole('button', { name: '切换来源' }).click();
    await page.getByRole('listbox', { name: '文件来源' }).getByText('项目当前代码', { exact: true }).click();
    await expect(panel.locator('.source-heading')).toContainText('项目当前代码');
    await expect(panel.locator('.source-status')).toHaveText('只读');
    await expect(panel.getByRole('button', { name: '保存', exact: true })).toHaveCount(0);
  } finally {
    if (machine.exitCode === null) machine.kill();
    await exited;
  }
});
