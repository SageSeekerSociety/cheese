import { test, expect, type Page } from '@playwright/test';
import { apiLogin } from './helpers';

// 项目格子拖着换序，真的接通到了顺序上。
//
// 这里只走一条：拖到第一格上半边，成为新的第一个。落点的判定本身（第一格上方、
// 最后一格下方、两格之间的缝）在 frontend/src/lib/projectOrder.spec.ts 的
// dropTargetAt 里逐个钉着——那些位置在 CDP 驱动的原生拖放里不稳：dragover 只在指
// 针动的时候派发，停在留白处的那一帧时有时无，做成 e2e 就是一条会时红时绿的用例。
// 这一条守的是另一件事：拖拽这条链路整体接通了（DOM 的 data-project-id、rail 的
// 落点状态、保存下来的顺序），而那是单测看不见的。

// 顺序按 `data-project-id` 读，不按 `aria-label`：格子的可访问名里带着“待我处理的件数”
// （「知是 2.0 融合演示（3）」），而那笔数在首帧之后才回来。读到一半标签从「知是 2.0
// 融合演示」变成「知是 2.0 融合演示（3）」，拿旧标签拼出来的选择器就再也不会命中——
// 这条用例会一直等到超时。`data-project-id` 不随数据变，也正是这条用例要守的东西。
const order = (page: Page) =>
  page.evaluate(() =>
    Array.from(document.querySelectorAll('.app-rail-item--tile')).map((tile) => tile.getAttribute('data-project-id'))
  );

test('把最后一个项目拖到第一格上半边，它成为新的第一个', async ({ page }) => {
  await apiLogin(page);
  const tiles = page.locator('.app-rail-item--tile');
  await tiles.first().waitFor();

  const before = await order(page);
  expect(before.length, '至少要三个项目才排得出顺序').toBeGreaterThan(2);

  const source = (await tiles.nth(before.length - 1).boundingBox())!;
  const head = (await tiles.first().boundingBox())!;
  const line = page.locator(`.app-rail-item--drop-before[data-project-id="${before[0]}"]`);

  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  await page.mouse.move(head.x + head.width / 2, head.y + head.height * 0.75, { steps: 6 });
  await page.mouse.move(head.x + head.width / 2, head.y + head.height * 0.25, { steps: 6 });
  // 插入线出现了才松手：最后一次 dragover 还没派发就松手，用的是上一个位置留下的
  // 落点，那样的用例会时好时坏。
  await line.waitFor();
  await page.mouse.up();

  await expect.poll(() => order(page)).toEqual([before[before.length - 1], ...before.slice(0, -1)]);

  // 换的序要留得住：刷新之后还是这个顺序。
  await page.reload();
  await tiles.first().waitFor();
  await expect.poll(() => order(page)).toEqual([before[before.length - 1], ...before.slice(0, -1)]);
});
