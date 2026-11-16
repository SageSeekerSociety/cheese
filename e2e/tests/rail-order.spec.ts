import { test, expect, type Page } from '@playwright/test';
import { apiLogin, openProjectSwitcher } from './helpers';

// 项目拖着换序，真的接通到了顺序上。
//
// 左边那一列方块栏 2026-11-16 拿掉了，项目住进侧栏头的项目切换列表（frontend/src/
// components/common/Navigation/ProjectSwitcher.vue），拖动排序也搬进了列表里。
//
// 两条：拖到第一行上半边成为第一个，拖到最后一行下半边成为最后一个。行的上半/下半决定
// 插到前面还是插到后面，这两个方向是两条各自独立的路——落点状态 class 分成 --before /
// --after 两个，列表也照着分别画插入线；哪一边写反了，只有走那一条才看得见。
//
// 顺序按 `data-project-id` 读：行的文字里带着「待我处理的件数」，那笔数在首帧之后才回来，
// 文字会变；`data-project-id` 不随数据变，也正是这条用例要守的东西。
const order = (page: Page) =>
  page.evaluate(() =>
    Array.from(document.querySelectorAll('.switcher__row-wrap[data-project-id]')).map((row) =>
      row.getAttribute('data-project-id')
    )
  );

const rowsOf = (page: Page) => page.locator('.switcher__row-wrap[data-project-id]');

async function dragTo(page: Page, from: number, to: number, edge: 'before' | 'after', target: string) {
  const rows = rowsOf(page);
  const source = (await rows.nth(from).boundingBox())!;
  const dest = (await rows.nth(to).boundingBox())!;
  const line = page.locator(`.switcher__row-wrap--${edge}[data-project-id="${target}"]`);
  await page.mouse.move(source.x + source.width / 2, source.y + source.height / 2);
  await page.mouse.down();
  // 先停在另一半再跨过中线：dragover 只在指针动的时候派发，停在最后一帧的位置才算数。
  const [first, last] = edge === 'before' ? [0.75, 0.25] : [0.25, 0.75];
  await page.mouse.move(dest.x + dest.width / 2, dest.y + dest.height * first, { steps: 6 });
  await page.mouse.move(dest.x + dest.width / 2, dest.y + dest.height * last, { steps: 6 });
  // 插入线出现了才松手：最后一次 dragover 还没派发就松手，用的是上一个位置留下的落点。
  await line.waitFor();
  await page.mouse.up();
}

test('把最后一个项目拖到第一行上半边，它成为新的第一个', async ({ page }) => {
  await apiLogin(page);
  await openProjectSwitcher(page);

  const before = await order(page);
  expect(before.length, '至少要三个项目才排得出顺序').toBeGreaterThan(2);

  await dragTo(page, before.length - 1, 0, 'before', before[0]!);
  const expected = [before[before.length - 1], ...before.slice(0, -1)];
  await expect.poll(() => order(page)).toEqual(expected);

  // 换的序要留得住：刷新之后还是这个顺序。
  await page.reload();
  await openProjectSwitcher(page);
  await expect.poll(() => order(page)).toEqual(expected);
});

test('把第一个项目拖到最后一行下半边，它成为新的最后一个', async ({ page }) => {
  await apiLogin(page);
  await openProjectSwitcher(page);

  const before = await order(page);
  expect(before.length, '至少要三个项目才排得出顺序').toBeGreaterThan(2);

  await dragTo(page, 0, before.length - 1, 'after', before[before.length - 1]!);
  const expected = [...before.slice(1), before[0]];
  await expect.poll(() => order(page)).toEqual(expected);

  await page.reload();
  await openProjectSwitcher(page);
  await expect.poll(() => order(page)).toEqual(expected);
});
