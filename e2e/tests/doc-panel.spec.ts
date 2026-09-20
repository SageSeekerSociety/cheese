/** 右侧那一栏里的实况文档，装不装得下自己的内容。
 *
 * 这一条只有在真浏览器里才成立：布局是这个 bug 的**全部**内容，而 jsdom 不排版，
 * 所以组件单测对它一律绿。写进 e2e 是唯一能拦住它的地方。
 *
 * 布景走 API（文档本来就是芝士写的），断言全在屏幕上量。
 */
import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { api, login, openFirstProject } from './helpers';

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
  await login(page);
  await openFirstProject(page);
  const projectId = projectIdOf(page);

  const room = (await api(page, 'post', '/topics', {
    project_id: projectId,
    title: `宽表格 ${Date.now()}`,
  })) as { id: string };
  await api(page, 'put', `/topics/${room.id}/doc`, {
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
