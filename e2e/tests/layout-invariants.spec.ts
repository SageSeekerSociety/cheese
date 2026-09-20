import { test, expect, type Locator } from '@playwright/test';
import { login } from './helpers';

// 表单字段的几何不变量。
//
// 起因是一类会静悄悄发出去的缺陷：outlined 字段的浮动 label 用 translateY(-50%)
// 坐在自己的上边框线上，有一半探在字段的边框盒之外。所以它会压到上一个字段的下
// 边框上（两个字段之间没留出间距时），也会被 overflow 的滚动容器裁掉（容器上沿
// 没留 padding 时）。两种都不报错、不让任何单元测试变红，typecheck 和 stylelint
// 也都看不见——只有量出来的坐标看得见，所以守在这里。
//
// 量的是 `.v-field`（画着描边的那个盒子），不是 `.v-input`：`.v-input` 还包着下面
// 那一行 `.v-input__details`，没有 hint 时它是空的、不可见的，两个字段的 `.v-input`
// 因此首尾相接是正常的，屏幕上并没有任何东西挨上。拿 `.v-input` 去比会把每一对
// 竖排字段都报成缺陷。
//
// 这一份只断言「屏幕上有没有压上/被裁」，不认任何具体的间距数值：改密度、换变体、
// 把 hide-details 设成别的都不该让它变红，只有真叠上了才该。

type Defect = { kind: string; what: string };

async function fieldDefects(scope: Locator): Promise<Defect[]> {
  return scope.evaluate((root: Element) => {
    const out: { kind: string; what: string }[] = [];
    const drawn = (el: Element) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };

    const boxes = [...root.querySelectorAll('.v-field')].filter(drawn);
    // 量不到字段就是范围选错了。空范围永远返回「没有缺陷」，是一条只会绿的断言，
    // 所以这里炸掉而不是放过：`.v-overlay__content` 的第一个是 tooltip 的浮层而
    // 不是对话框，这一条就是这么发现的。
    if (!boxes.length) throw new Error('这个范围里一个字段都没有，这条断言等于没做');

    const nameOf = (el: Element) => (el.querySelector('.v-field-label')?.textContent || '').trim() || '(无标签字段)';
    const scrollerOf = (el: Element) => {
      for (let p = el.parentElement; p; p = p.parentElement) {
        const overflow = getComputedStyle(p).overflowY;
        if (overflow === 'auto' || overflow === 'scroll' || overflow === 'hidden') return p;
      }
      return null;
    };

    // outlined 变体真正画出来的那个 label 住在描边的缺口里；字段内部还有一个同名
    // 副本，是 visibility:hidden 的占位，量它只会得到错的坐标。
    const labels = [...root.querySelectorAll('.v-field__outline .v-field-label')].filter(
      (el) => getComputedStyle(el).visibility !== 'hidden' && drawn(el)
    );

    for (const label of labels) {
      const own = label.closest('.v-field');
      const text = (label.textContent || '').trim();
      const lr = label.getBoundingClientRect();

      for (const box of boxes) {
        if (box === own) continue;
        const br = box.getBoundingClientRect();
        // 1px 容差：边框相接不算压上，真叠进去才算。
        const hit = lr.left < br.right && lr.right > br.left && lr.top < br.bottom - 1 && lr.bottom > br.top + 1;
        if (hit) out.push({ kind: 'overlap', what: `「${text}」压在「${nameOf(box)}」上` });
      }

      const scroller = scrollerOf(label);
      const fr = own?.getBoundingClientRect();
      if (scroller && fr) {
        const sr = scroller.getBoundingClientRect();
        // 只看「字段本身完整露着、label 却被切掉」。字段滚到视野外是正常的滚动，
        // 不是缺陷。
        const fieldFullyInView = fr.top >= sr.top - 0.5 && fr.bottom <= sr.bottom + 0.5;
        if (fieldFullyInView && (lr.top < sr.top - 0.5 || lr.bottom > sr.bottom + 0.5))
          out.push({ kind: 'clipped', what: `「${text}」被它所在的滚动容器裁掉` });
      }
    }
    return out;
  });
}

test.describe('表单字段不会互相压住，也不会被裁掉', () => {
  test('登录页', async ({ page }) => {
    await page.goto('/account/signin');
    await page.getByLabel('用户名').waitFor();
    expect(await fieldDefects(page.locator('body'))).toEqual([]);
  });

  test('「修改 AI 队友」对话框', async ({ page }) => {
    await login(page);
    await page.locator('.app-rail-item--tile').first().click();
    await page.waitForURL(/\/projects\/[^/]+/);
    const projectId = page.url().match(/\/projects\/([^/?#]+)/)![1];

    await page.goto(`/projects/${projectId}/agents`);
    const edit = page.getByRole('button', { name: '编辑' }).first();
    await edit.waitFor();
    await edit.click();

    const dialog = page.locator('.v-overlay__content').filter({ hasText: '修改 AI 队友' });
    await dialog.waitFor();
    // 两个下拉的选项是异步取回来的，而「角色设定」是 autoGrow 的文本域——内容灌
    // 进去之后高度才定下来。等到两个下拉都显示出选中的值，这一屏就不会再动了。
    // （不等 getByLabel('模型')：v-select 的可访问名来自内部那个 combobox，不是
    //  描边缺口里的那行字。）
    await expect(dialog.locator('.v-select__selection')).toHaveCount(2);
    expect(await fieldDefects(dialog)).toEqual([]);
  });
});
