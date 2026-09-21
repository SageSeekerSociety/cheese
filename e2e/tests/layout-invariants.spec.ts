import { test, expect, type Locator } from '@playwright/test';
import { api, login } from './helpers';

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
  test('artifact comparison keeps diff lines vertical on desktop and mobile', async ({ page }) => {
    await login(page);
    await page.locator('.app-rail-item--tile').first().click();
    await page.waitForURL(/\/projects\/[^/]+/);
    const projectId = page.url().match(/\/projects\/([^/?#]+)/)![1];
    const artifactId = '00000000-0000-0000-0000-000000000123';
    const versions = [1, 2].map(number => ({
      number, card_id: `version-${number}`, subject: `Report ${number}`,
      delivered_at: '2026-09-20T12:00:00Z', decided_by: 'alice',
      kind: 'file', filename: 'report.txt', url: null,
    }));
    await page.route(`**/api/projects/${projectId}/artifacts/${artifactId}`, route => route.fulfill({
      json: { code: 200, data: { id: artifactId, name: 'Version comparison fixture', version: 2, delivered_at: versions[1].delivered_at, versions } },
    }));
    await page.route(`**/api/projects/${projectId}/artifacts/${artifactId}/compare?*`, route => route.fulfill({
      json: { code: 200, data: { kind: 'file', identical: false, note: null, files: [{ path: 'report.txt', diff: '--- report.txt\n+++ report.txt\n@@ -1 +1 @@\n-before\n+after', note: null }] } },
    }));
    await page.goto(`/projects/${projectId}/artifacts/${artifactId}`);
    await expect(page.getByText('+after', { exact: true })).toBeVisible();
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await expect(page.getByLabel('基准版本', { exact: true })).toBeVisible();
      await expect(page.getByLabel('对比版本', { exact: true })).toBeVisible();
      const lines = await page.locator('.comparison-diff span').evaluateAll(nodes => nodes.map(node => {
        const r = node.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, left: r.left };
      }));
      expect(lines.length).toBe(5);
      for (let i = 1; i < lines.length; i++) {
        expect(lines[i].top).toBeGreaterThanOrEqual(lines[i - 1].bottom);
        expect(lines[i].left).toBe(lines[0].left);
      }
      const selectors = await page.locator('.comparison-selectors').boundingBox();
      expect(selectors!.x + selectors!.width).toBeLessThanOrEqual(width);
    }
    await page.getByLabel('对比版本', { exact: true }).selectOption('version-1');
    await expect(page.getByText('请选择两个不同的版本')).toBeVisible();
    await expect(page.locator('.comparison-diff')).toHaveCount(0);
  });

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
    // 这张表单不再取任何异步选项（模型与运行方式都不是队友的属性了），会动的
    // 只剩「角色设定」那个 autoGrow 的文本域——内容灌进去之后高度才定下来。
    // 等到名字和角色设定都是这个队友自己的值，这一屏就不会再动了。
    await expect(dialog.getByLabel('名字', { exact: true })).toHaveValue(/.+/);
    await expect(dialog.getByLabel('角色设定（可留空）')).toBeVisible();
    expect(await fieldDefects(dialog)).toEqual([]);
  });

  test('反馈中心 · 提交反馈抽屉', async ({ page }) => {
    await login(page);
    await page.goto('/feedback');
    await page.getByRole('button', { name: '提交反馈' }).first().click();

    // 抽屉是 `temporary` 的：关着的时候它**根本不在 DOM 里**，所以 `.fb-drawer`
    // 出现就等于「这一屏来了」。等的是里面那个字段真的画出来，不是抽屉的容器：
    // 容器一挂上就有坐标，字段还在后面几帧里。
    //
    // 范围只取抽屉，不取 `body`：这一页的工具行上还有一个搜索框，喂给
    // `fieldDefects` 会把两处不相干的字段放在一起比，而它们本来就不在一个平面
    // 上（一个是页面正文，一个是浮层）。
    const drawer = page.locator('.fb-drawer');
    await drawer.waitFor();
    await expect(drawer.getByLabel('标题', { exact: true })).toBeVisible();
    expect(await fieldDefects(drawer)).toEqual([]);
  });

  test('管理后台 · 反馈队列里打开一条', async ({ page }) => {
    await login(page);

    // 这条反馈是**这条用例自己造的**：详情面板上的字段只在某一条被打开之后才
    // 存在，而 e2e 的库是干净的、用例之间的顺序也不是契约（别指望别的用例留下
    // 的数据）。标题带一个时间戳是为了搜得到——队列里不止这一条。
    const stamp = `${Date.now()}`;
    await api(page, 'post', '/feedback', {
      kind: 'bug',
      title: `【e2e】字段几何 ${stamp}`,
      problem: 'layout-invariants 自己造的，只为了把详情面板的字段画出来。',
      visibility: 'public',
    });

    await page.goto('/admin/queue');
    await page.locator('.qpage__search-input').fill(stamp);
    await page.locator('.fbrow__link').filter({ hasText: stamp }).first().click();

    // 视口默认 1280 宽，详情是按**页面内**那一套画的（`.qdet`），不是抽屉。
    //
    // 用 `getByRole('textbox')` 而不是 `getByLabel('指派给')`：那个字段的
    // accessible name 是「指派给 指派给」（label 拼上 placeholder），旁边那颗
    // 清除图标的 aria-label 是「清除 指派给」——两个都被 `getByLabel('指派给')`
    // 子串命中，locator 当场变成 2 个元素。
    const detail = page.locator('.qdet');
    await expect(detail.getByRole('textbox', { name: /指派给/ })).toBeVisible();
    expect(await fieldDefects(detail)).toEqual([]);
  });

  test('管理后台 · 「添加管理员」那张表单', async ({ page }) => {
    await login(page);
    await page.goto('/admin/members');

    // 这一页唯一的一组字段在对话框里：名单本身是张表，一个 `.v-field` 都没有，
    // 所以这里不能拿 `body` 当范围——`fieldDefects` 会当场炸「这个范围里一个
    // 字段都没有」，而那正是它该做的（空范围永远返回「没有缺陷」）。
    await page.getByRole('button', { name: '添加管理员' }).first().click();
    const dialog = page.locator('.v-overlay__content').filter({ hasText: '搜索账号' });
    await dialog.waitFor();
    await expect(dialog.getByLabel('搜索账号')).toBeVisible();
    expect(await fieldDefects(dialog)).toEqual([]);
  });
});
