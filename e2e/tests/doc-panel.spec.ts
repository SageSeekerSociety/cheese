/** 右侧那一栏里的实况文档，装不装得下自己的内容。
 *
 * 这一条只有在真浏览器里才成立：布局是这个 bug 的**全部**内容，而 jsdom 不排版，
 * 所以组件单测对它一律绿。写进 e2e 是唯一能拦住它的地方。
 *
 * 布景走 API（文档本来就是芝士写的），断言全在屏幕上量。
 */
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { api, apiLogin, openFirstProject } from './helpers';

function projectIdOf(page: Page): string {
  const id = page.url().match(/\/projects\/([0-9a-f-]{36})/)?.[1];
  if (!id) throw new Error(`当前页不是项目工作台：${page.url()}`);
  return id;
}

// 一张宽表格。宽在**单元格拆不开**：路径、URL、英文标识符没有可换行的地方，所以
// 表格的最小宽度就是它们的宽度。纯中文表格逐字换行，挤得下，永远复现不出来。
const WIDE_TABLE_DOC = [
  '# 一份带宽表格的文档',
  '',
  '| 文件 | 症状 | 修复方式 | 负责人 |',
  '| --- | --- | --- | --- |',
  '| `backend/app/api/routes/topics.py` | PUT /topics/{topic_id}/doc 少了版本校验 | 补乐观锁 | 马霄宇 |',
  '| `frontend/src/components/panels/PanelDoc.vue` | 表格的横向滚动没生效 | 让上面那一列能收缩 | 李甘 |',
  '| `https://github.com/SageSeekerSociety/cheese/pull/753` | 合并后 e2e 变红 | 核对 head sha | 池若彤 |',
  '',
].join('\n');

test('文档里的宽表格在自己那格里横向滚动，不把整栏顶出面板', async ({ page }) => {
  await apiLogin(page);
  await openFirstProject(page);
  const projectId = projectIdOf(page);

  const room = (await api(page, 'post', '/topics', {
    project_id: projectId,
    title: `宽表格 ${Date.now()}`,
  })) as { id: string };
  const roomDoc = (await api(page, 'get', `/topics/${room.id}/document`)) as { id: string };
  await api(page, 'put', `/documents/${roomDoc.id}`, {
    content: WIDE_TABLE_DOC,
    expected_version: 0,
  });

  await page.goto(`/projects/${projectId}/topics/${room.id}`);
  const wrapper = page.locator('.tableWrapper');
  await expect(wrapper).toBeVisible({ timeout: 30_000 });

  // 面板右边界是硬边界：越过去的部分被上层的 overflow:hidden 剪掉，既看不见也拿
  // 不回来 —— 表格右边几列就是这么消失的，连带把工具栏顶出了屏幕。
  const panel = page.locator('.work-panel');
  const panelBox = (await panel.boundingBox())!;
  const wrapperBox = (await wrapper.boundingBox())!;
  expect(wrapperBox.x + wrapperBox.width).toBeLessThanOrEqual(panelBox.x + panelBox.width + 1);

  // 装不下的部分得留在表格自己的滚动区里，人还能滑到 —— 否则「不溢出」也可以靠
  // 把内容剪掉来达成。
  const scroll = await wrapper.evaluate((el) => ({ client: el.clientWidth, content: el.scrollWidth }));
  expect(scroll.content).toBeGreaterThan(scroll.client);

  // 页面整体没有横向滚动条：这是没有任何一层被顶宽的总检查。
  const doc = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(doc.scroll).toBe(doc.client);
});

// 键盘焦点落在正文上时，编辑器画出焦点环；鼠标点进正文时不画。
//
// 正文自己把 outline 去掉了（DocSurface 的 `.doc-prose`），环画在外面的编辑器盒子上，
// 只在焦点由键盘带进来时亮：可编辑区对 `:focus-visible` 不分鼠标键盘，靠它的话点一下
// 也亮。这也是一类**只有真浏览器才看得见**的不变量：jsdom 不算焦点和样式，组件单测对它
// 一律绿；只有量计算出来的 outline 才拦得住「键盘用户点了半天不知道焦点在哪」，以及
// 反过来，用鼠标写字的人眼前常亮一圈边框。
test('键盘焦点落在正文上时，编辑器盒子画出焦点环', async ({ page }) => {
  await apiLogin(page);
  await openFirstProject(page);
  const projectId = projectIdOf(page);

  const room = (await api(page, 'post', '/topics', {
    project_id: projectId,
    title: `焦点环 ${Date.now()}`,
  })) as { id: string };
  const roomDoc = (await api(page, 'get', `/topics/${room.id}/document`)) as { id: string };
  await api(page, 'put', `/documents/${roomDoc.id}`, {
    content: '# 焦点环\n\n正文。',
    expected_version: 0,
  });

  await page.goto(`/projects/${projectId}/topics/${room.id}`);
  const prose = page.locator('.work-panel .doc-editor .doc-prose');
  await expect(prose).toBeVisible({ timeout: 30_000 });
  // 编辑区（所有者打开，可编辑）才接得住键盘焦点；等它挂上再进。
  await expect(prose).toHaveAttribute('contenteditable', 'true', { timeout: 30_000 });

  // 键盘焦点进正文（没有先按鼠标）：外面的盒子画环。
  await prose.focus();

  const ring = await page.locator('.work-panel .doc-editor').evaluate((el) => {
    const style = getComputedStyle(el);
    return { style: style.outlineStyle, width: parseFloat(style.outlineWidth) };
  });
  expect(ring.style).not.toBe('none');
  expect(ring.width).toBeGreaterThanOrEqual(2);

  // 焦点离开时环收回：它只跟着键盘焦点，不常亮。
  const outline = () => page.locator('.work-panel .doc-editor').evaluate((el) => getComputedStyle(el).outlineStyle);
  await prose.evaluate((el) => (el as HTMLElement).blur());
  await expect.poll(outline).toBe('none');

  // 用鼠标点进正文：光标就是落点，不画环。
  await prose.click();
  await expect(prose).toBeFocused();
  expect(await outline()).toBe('none');
});
