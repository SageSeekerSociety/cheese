import { test, expect, type Page } from '@playwright/test';
import { apiLogin } from './helpers';

// 项目格子拖着换序，真的接通到了顺序上。
//
// 两条：拖到第一格上半边成为第一个，拖到最后一格下半边成为最后一个。格子的上半/
// 下半决定插到前面还是插到后面，这两个方向是两条各自独立的路——落点状态 class 分
// 成 --drop-before / --drop-after 两个，rail 也照着分别画插入线；哪一边的 class
// 或者判定写反了，只有走那一条才看得见，所以两条都要走。
//
// 这里只走这两个「指针落在格子上」的位置。其余位置（第一格上方、最后一格下方、两
// 格之间的缝）在 frontend/src/lib/projectOrder.spec.ts 的 dropTargetAt 里逐个钉着
// ——那些位置在 CDP 驱动的原生拖放里不稳：dragover 只在指针动的时候派发，停在留白
// 处的那一帧时有时无，做成 e2e 就是一条会时红时绿的用例。这两条守的是另一件事：
// 拖拽这条链路整体接通了（DOM 的 data-project-id、rail 的落点状态、保存下来的顺
// 序），而那是单测看不见的。

const order = (page: Page) =>
  page.evaluate(() =>
    Array.from(document.querySelectorAll('.app-rail-item--tile')).map((tile) => tile.getAttribute('aria-label'))
  );

test('把最后一个项目拖到第一格上半边，它成为新的第一个', async ({ page }) => {
  await apiLogin(page);
  const tiles = page.locator('.app-rail-item--tile');
  await tiles.first().waitFor();

  const before = await order(page);
  expect(before.length, '至少要三个项目才排得出顺序').toBeGreaterThan(2);

  const source = (await tiles.nth(before.length - 1).boundingBox())!;
  const head = (await tiles.first().boundingBox())!;
  const line = page.locator(`.app-rail-item--drop-before[aria-label="${before[0]}"]`);

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

test('把第一个项目拖到最后一格下半边，它成为新的最后一个', async ({ page }) => {
  await apiLogin(page);
  const tiles = page.locator('.app-rail-item--tile');
  await tiles.first().waitFor();

  const before = await order(page);
  expect(before.length, '至少要三个项目才排得出顺序').toBeGreaterThan(2);

  const source = (await tiles.first().boundingBox())!;
  const tail = (await tiles.nth(before.length - 1).boundingBox())!;
  const line = page.locator(`.app-rail-item--drop-after[aria-label="${before[before.length - 1]}"]`);

  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  // 先停上半边再挪到下半边：dragover 只在指针动的时候派发，停在最后一帧的位置才
  // 算数，所以要真的从上往下跨过中线，不能一步落到底。
  await page.mouse.move(tail.x + tail.width / 2, tail.y + tail.height * 0.25, { steps: 6 });
  await page.mouse.move(tail.x + tail.width / 2, tail.y + tail.height * 0.75, { steps: 6 });
  await line.waitFor();
  await page.mouse.up();

  const expected = [...before.slice(1), before[0]];
  await expect.poll(() => order(page)).toEqual(expected);

  await page.reload();
  await tiles.first().waitFor();
  await expect.poll(() => order(page)).toEqual(expected);
});
