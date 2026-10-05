/** 资料库里的文档：点「新建文档」直接建好并打开，标题在页上起，正文多人一起写，
 * 回到资料库它就在列表里。在对话旁边打开的也是同一份。
 *
 * 只有真浏览器里成立的部分在这里：文档经协同服务打开、标题改了以后列表跟着、
 * 正文打下去存得回来。
 */
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { api, apiLogin, openFirstProject } from './helpers';

function projectIdOf(page: Page): string {
  const id = page.url().match(/\/projects\/([0-9a-f-]{36})/)?.[1];
  if (!id) throw new Error(`当前页不是项目工作台：${page.url()}`);
  return id;
}

const SHOTS = process.env.E2E_SHOTS_DIR;

test('新建文档直接打开，起了名、写了字，回到资料库就在列表里', async ({ page }) => {
  await apiLogin(page);
  await openFirstProject(page);
  const projectId = projectIdOf(page);

  await page.goto(`/projects/${projectId}/library`);
  await page.getByRole('button', { name: '新建文档' }).click();
  await expect(page).toHaveURL(/[?&]doc=[0-9a-f-]{36}/);
  const docId = new URL(page.url()).searchParams.get('doc')!;

  const title = `定价对比 ${Date.now()}`;
  const titleBox = page.getByRole('textbox', { name: '文档标题' });
  await titleBox.fill(title);
  await titleBox.press('Enter');

  const body = page.locator('.doc-prose');
  await expect(body).toBeVisible({ timeout: 30_000 });
  await body.click();
  await page.keyboard.type('三家都有年付折扣。');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/library-doc-page.png` });

  // 存回去的是这份文档：从接口读，读到的是刚打的字。
  await expect
    .poll(async () => ((await api(page, 'get', `/documents/${docId}`)) as { content?: string } | null)?.content ?? '', {
      timeout: 30_000,
    })
    .toContain('三家都有年付折扣');

  await page.goto(`/projects/${projectId}/library`);
  await expect(page.locator('.library-row__name', { hasText: title })).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/library-list.png` });

  // 在对话旁边打开的是同一份：页签写着它的名字，正文是刚才打的字。
  const room = (await api(page, 'post', '/topics', { project_id: projectId, title: `旁边开 ${Date.now()}` })) as {
    id: string;
  };
  await page.goto(`/projects/${projectId}/topics/${room.id}?tab=file:doc:${docId}`);
  await expect(page.locator('.tabbar__name', { hasText: title })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.work-panel .doc-prose').last()).toContainText('三家都有年付折扣', { timeout: 30_000 });
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/library-doc-beside-chat.png` });
});
