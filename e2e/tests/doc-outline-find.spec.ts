/** 文档大纲与文档内查找：只有真浏览器量得出的那部分 —— 顶栏的两个入口、浮层落在哪、
 * 高亮有没有真的画进正文、Esc 之后焦点还给了谁、关掉之后正文留没留痕。
 *
 * 匹配与抽取的逻辑本身（大小写、不重叠、跨标记、不跨段落、下标推进）由单测钉住：
 * frontend/src/lib/docOutline.spec.ts 与 frontend/src/lib/docFind.spec.ts；这一份只
 * 证明它们接到了界面上，而且在真排版里站得住。
 */
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { api, apiLogin, openFirstProject } from './helpers';

// 布景走 API：文档本来就是芝士写的，断言全在屏幕上量（同 doc-panel.spec.ts）。
function projectIdOf(page: Page): string {
  const id = page.url().match(/\/projects\/([0-9a-f-]{36})/)?.[1];
  if (!id) throw new Error(`当前页不是项目工作台：${page.url()}`);
  return id;
}

const HEADED_DOC = [
  '# 章程',
  '',
  '开头一段话，这里有一个词：部署。',
  '',
  '## 部署',
  '',
  '再说一次部署。',
  '',
  '### 收尾',
  '',
  '结束。',
].join('\n');

const PLAIN_DOC = ['只有一段普通的话，没有任何标题。', '', '这里也有一个词：部署。'].join('\n');

/** 开一篇带指定正文的文档，等顶栏与正文都挂上，交回正文的定位器。 */
async function openDoc(page: Page, content: string) {
  await apiLogin(page);
  await openFirstProject(page);
  const projectId = projectIdOf(page);

  const room = (await api(page, 'post', '/topics', {
    project_id: projectId,
    title: `大纲查找 ${Date.now()}`,
  })) as { id: string };
  // 频道没有自己的文档：实况文档是任务的。
  const task = (await api(page, 'post', `/topics/${room.id}/tasks`, { title: '文档' })) as { id: string };
  const roomDoc = (await api(page, 'get', `/topics/${task.id}/document`)) as { id: string };
  await api(page, 'put', `/documents/${roomDoc.id}`, {
    content,
    expected_version: 0,
  });

  await page.goto(`/projects/${projectId}/topics/${room.id}/tasks/${task.id}?tab=overview`);
  const prose = page.locator('.work-panel .doc-editor .doc-prose');
  await expect(prose).toBeVisible({ timeout: 30_000 });
  // 顶栏那两颗按钮在文档加载完（loading 落下去）之后才出现，等大纲那颗就够。
  await expect(page.getByRole('button', { name: '大纲', exact: true })).toBeVisible({ timeout: 30_000 });
  return prose;
}

test('顶栏的大纲列出正文标题，点一条带进视野后菜单收起', async ({ page }) => {
  await openDoc(page, HEADED_DOC);

  await page.getByRole('button', { name: '大纲', exact: true }).click();
  const outline = page.locator('.doc-outline');
  await expect(outline).toBeVisible();
  // 三条标题（h1–h3）都在，且按出现顺序。
  await expect(outline.locator('.v-list-item')).toHaveCount(3);
  await expect(outline.locator('.v-list-item').nth(0)).toContainText('章程');
  await expect(outline.locator('.v-list-item').nth(1)).toContainText('部署');
  await expect(outline.locator('.v-list-item').nth(2)).toContainText('收尾');

  // 点一条：把那一节带进视野（不报错），菜单收起来。
  await outline.locator('.v-list-item').nth(2).click();
  await expect(outline).toBeHidden();
});

test('没有标题的文档，大纲给一句空态', async ({ page }) => {
  await openDoc(page, PLAIN_DOC);
  await page.getByRole('button', { name: '大纲', exact: true }).click();
  await expect(page.locator('.doc-outline')).toBeVisible();
  await expect(page.getByText('这篇文档还没有标题')).toBeVisible();
});

test('文档内查找：高亮全部命中、计数、上下跳、Esc 关闭并把焦点还给正文', async ({ page }) => {
  const prose = await openDoc(page, HEADED_DOC);

  await page.getByRole('button', { name: '在文档中查找' }).click();
  const bar = page.locator('.doc-find');
  await expect(bar).toBeVisible();

  await bar.getByRole('textbox').fill('部署');
  await expect(bar.locator('.doc-find__count')).toHaveText('1/3');

  // 命中真的画进了正文：三处命中、恰有一处是「当前」。
  await expect(prose.locator('.doc-find-hit')).toHaveCount(3);
  await expect(prose.locator('.doc-find-hit.is-active')).toHaveCount(1);

  await bar.getByRole('textbox').press('Enter');
  await expect(bar.locator('.doc-find__count')).toHaveText('2/3');
  await bar.getByRole('textbox').press('Shift+Enter');
  await expect(bar.locator('.doc-find__count')).toHaveText('1/3');

  // 查不到时说一声。
  await bar.getByRole('textbox').fill('橘子');
  await expect(bar.locator('.doc-find__count')).toHaveText('没有找到');
  await expect(prose.locator('.doc-find-hit')).toHaveCount(0);

  // Esc：关条、撤掉高亮、焦点还给正文。
  await bar.getByRole('textbox').fill('部署');
  await expect(bar.locator('.doc-find__count')).toHaveText('1/3');
  await bar.getByRole('textbox').press('Escape');
  await expect(bar).toBeHidden();
  await expect(prose.locator('.doc-find-hit')).toHaveCount(0);
  await expect
    .poll(() => page.evaluate(() => !!document.activeElement?.closest('.doc-prose')))
    .toBe(true);
});
