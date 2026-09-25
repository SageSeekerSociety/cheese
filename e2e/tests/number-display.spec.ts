import { test, expect, type Locator } from '@playwright/test';
import { apiLogin } from './helpers';

// 数字显示不全的检测器。
//
// 起因是看板上一批数字被裁掉了尾巴（`2,532,615` 显示成 `2,532,6…`，`$12,345.67`
// 显示成 `$12,34…`）。这一类缺陷**现有测试一条都拦不住**：单测只看字符串格式化的
// 结果（格式化本身是对的），typecheck / eslint / stylelint 都不看盒子有多宽，而
// 真正把数字裁掉的是布局 —— 一个 nowrap 的数字塞进了比它窄的格子。
//
// 所以只能在屏幕上量。判据有两条，都要：
//
//   1. 这个元素是**数字叶子**（直接文字子节点里有数字），而且它的内容被裁了：
//      `scrollWidth > clientWidth`。
//   2. 裁它的那个 overflow 是 `hidden` / `clip` / 带 `text-overflow: ellipsis` 的
//      `visible` 以外的值 —— 一个能自由换行的数字不会被裁，只会换行，那不是本条
//      要拦的缺陷。
//
// 只报**叶子**：父容器的 scrollWidth 天然比它的第一个子节点大，拿父层去比会把
// 每一张卡片都报成缺陷。
//
// 断言里故意**不认具体的宽度数值**：改字号、换密度、KPI 格从 3 列变 2 列都不该
// 让它变红，只有真被裁了才该。

const DIGIT = /\d/;

/** 数字叶子被裁的清单：元素的文本 + 裁掉多少像素。 */
async function truncatedNumbers(scope: Locator): Promise<string[]> {
  return scope.evaluate((root: Element) => {
    const ownsDigits = (el: Element) =>
      [...el.childNodes].some(
        (n) => n.nodeType === Node.TEXT_NODE && /\d/.test(n.textContent || '')
      );
    const hits: string[] = [];
    for (const el of root.querySelectorAll('*')) {
      if (!ownsDigits(el)) continue;
      if (!(el.checkVisibility?.({ checkVisibilityCSS: true }) ?? true)) continue;
      // 叶子优先：有数字文字子节点的元素里最深的那个。父层包着子层时父层的
      // scrollWidth 是所有子层之和，拿它比会误报。
      const hasDigitChild = [...el.children].some((c) =>
        [...c.childNodes].some(
          (n) => n.nodeType === Node.TEXT_NODE && /\d/.test(n.textContent || '')
        )
      );
      if (hasDigitChild) continue;

      const style = getComputedStyle(el);
      const clips =
        style.overflowX === 'hidden' ||
        style.overflowX === 'clip' ||
        style.overflowX === 'auto' ||
        style.overflowX === 'scroll' ||
        style.textOverflow === 'ellipsis';
      if (!clips) continue;

      const over = el.scrollWidth - el.clientWidth;
      if (over <= 1) continue; // 1px 是亚像素余量
      hits.push(
        `「${(el.textContent || '').trim().slice(0, 24)}」被裁掉 ${over}px` +
          `（scrollWidth=${el.scrollWidth} clientWidth=${el.clientWidth}）`
      );
    }
    return hits;
  });
}

/** 带 title 的简写数字必须把完整数字放在 title 里 —— 简写是显示手段，不是事实。 */
async function abbreviatedWithoutTitle(scope: Locator): Promise<string[]> {
  return scope.evaluate((root: Element) => {
    const ABBREV = /\d[\d.,]*\s*[kMGT](?![a-zA-Z])/;
    const hits: string[] = [];
    for (const el of root.querySelectorAll('*')) {
      const own = [...el.childNodes]
        .filter((n) => n.nodeType === Node.TEXT_NODE)
        .map((n) => n.textContent || '')
        .join('')
        .trim();
      if (!ABBREV.test(own)) continue;
      if (!(el.checkVisibility?.({ checkVisibilityCSS: true }) ?? true)) continue;
      // 父层标题里有完整数也行（`title` 继承给读屏，但 `getAttribute` 不继承 ——
      // 所以这里自己往上找一层）。
      const t = el.getAttribute('title') || el.parentElement?.getAttribute('title') || '';
      if (!/\d/.test(t)) {
        hits.push(`简写「${own.slice(0, 24)}」没有把完整数字放进 title`);
      }
    }
    return hits;
  });
}

/** 极端值注入：把看板上所有能被 store 覆盖的数字推到会撑爆格子的量级。 */
async function inflateFixture(page: import('@playwright/test').Page): Promise<void> {
  await page.evaluate(() => {
    // 走真实渲染路径最准，但 store 的形状是内部的；这里在 DOM 层把每个数字叶子
    // 换成极端值，判据是「布局会不会裁」，不是「store 算得对不对」。
    const EXTREME = ['9,999,999,999', '$888,888.88', '2532615017', '0.000001%', '999999.9 ms'];
    let i = 0;
    for (const el of document.querySelectorAll('*')) {
      const own = [...el.childNodes].filter(
        (n) => n.nodeType === Node.TEXT_NODE && /\d/.test(n.textContent || '')
      );
      if (!own.length) continue;
      if (!el.checkVisibility?.({ checkVisibilityCSS: true })) continue;
      const deepest = [...el.children].some((c) =>
        [...c.childNodes].some(
          (n) => n.nodeType === Node.TEXT_NODE && /\d/.test(n.textContent || '')
        )
      );
      if (deepest) continue;
      for (const n of own) {
        n.textContent = EXTREME[i++ % EXTREME.length]!;
      }
    }
  });
  // 让布局结算完再量。
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
}

test.describe('数字不会被裁掉', () => {
  for (const kind of ['pipeline', 'usage', 'platform', 'performance'] as const) {
    test(`看板 · ${kind}：没有数字叶子被裁，简写都带完整值`, async ({ page }) => {
      await apiLogin(page);
      await page.goto('/admin/dashboard');
      await page.waitForLoadState('networkidle');
      // 切到这一类（导轨上的按钮：accessible name 是裸标签）。
      const label = { pipeline: '交付', usage: '用量', platform: '平台', performance: '性能' }[kind];
      await page.getByRole('button', { name: label, exact: true }).first().click();
      await page.waitForLoadState('networkidle');

      const board = page.locator('.ad, main').first();
      // 真实数据这一遍：量现在画着的这些数。
      expect(await truncatedNumbers(board)).toEqual([]);
      expect(await abbreviatedWithoutTitle(board)).toEqual([]);

      // 极端值这一遍：真实数据未必有撑爆格子的量级，所以再注入一次。
      await inflateFixture(page);
      expect(await truncatedNumbers(board)).toEqual([]);
    });
  }
});
