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


/** 屏幕上**两个东西有没有画在同一个坐标上**。
 *
 *  这一条是补的，起因也是真事：看板「用量」那一类里，柱子底下 9 个项目名横排在
 *  9px 下互相压住（量到 9×9 像素），窄屏上两张 KPI 卡的标题也压在一起（148px）。
 *  这一整类缺陷**现有的测试一条都拦不住** —— 单测看的是数据和请求，e2e 看的是文案
 *  和路径，typecheck / eslint / stylelint 都不看坐标。它们只在屏幕上存在，所以只能在
 *  屏幕上量。
 *
 *  量的是**含文字的元素**（`svg text` 与任何有直接文字子节点的元素），逐对求交：
 *  两个方向的交叠都超过 2px 才算。2px 是给亚像素和「字与它的容器」留的余量 ——
 *  容器包着文字当然是重叠的，所以只比**同级或跨块**的文字盒，不比祖先。
 */
async function textOverlaps(scope: Locator): Promise<string[]> {
  return scope.evaluate((root: Element) => {
    const ownsText = (el: Element) =>
      [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent || '').trim().length > 0);
    const boxes = [...root.querySelectorAll('*')]
      .filter((el) => el instanceof SVGTextElement || ownsText(el))
      // **只量真的画出来的东西**。`getBoundingClientRect` 对「不渲染但仍有布局盒」的
      // 元素照样给坐标：收起状态的 `<details>`（那张「查看数据表」）就是这一类 ——
      // 它里面的 `<th>`/`<td>` 每一个都有 200×26 的盒子，量出来会跟页面正文报一大堆
      // 「重叠」，而屏幕上根本没有它们。`checkVisibility()` 是浏览器自己对这个问题的答案。
      .filter((el) => el.checkVisibility?.({ checkVisibilityCSS: true }) ?? true)
      .map((el) => ({ el, r: el.getBoundingClientRect() }))
      .filter((b) => b.r.width > 0 && b.r.height > 0)
      // 还有一类盒子是**被裁掉了但坐标还在**：滚动容器里的内容滚出可视区时，它的
      // `getBoundingClientRect` 照样给一个跑到容器外面的盒子（浏览器只是不画它）。
      // 不排掉这一类，页面底下任何一条被滚动容器裁住的行都会跟底部导航「重叠」——
      // 而屏幕上看不见它。判据是「这个盒子有没有越出某个祖先的裁剪框」。
      .filter((b) => !clippedByAncestor(b.el))
      // 同一段文字被父子两层都收进来时只留最深的那一层（父层的盒子更大，比出来永远是重叠）。
      .filter((b) => !elHasTextyAncestor(root, b.el));

    const hits: string[] = [];
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i];
        const b = boxes[j];
        if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
        const ox = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left);
        const oy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
        if (ox > 2 && oy > 2) {
          hits.push(
            `「${(a.el.textContent || '').trim().slice(0, 20)}」与「${(b.el.textContent || '').trim().slice(0, 20)}」` +
              `重叠 ${Math.round(ox)}×${Math.round(oy)}px`
          );
        }
      }
    }
    return hits;

    /** 这个元素的盒子有没有越出某个祖先的裁剪框（`overflow` 不是 visible 的那些）。
     *  必须在 evaluate 里声明：这个函数在浏览器里跑，Node 作用域里的东西它看不见。 */
    function clippedByAncestor(el: Element): boolean {
      const box = el.getBoundingClientRect();
      for (let p = el.parentElement; p; p = p.parentElement) {
        const s = getComputedStyle(p);
        const clips =
          /auto|scroll|hidden|clip/.test(s.overflowY) || /auto|scroll|hidden|clip/.test(s.overflowX);
        if (!clips) continue;
        const r = p.getBoundingClientRect();
        if (box.bottom > r.bottom + 1 || box.top < r.top - 1 || box.right > r.right + 1 || box.left < r.left - 1) {
          return true;
        }
      }
      return false;
    }

    function elHasTextyAncestor(scopeEl: Element, el: Element): boolean {
      for (let p = el.parentElement; p && p !== scopeEl.parentElement; p = p.parentElement) {
        if (p === scopeEl) break;
        if (p instanceof SVGTextElement) return true;
        if ([...p.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent || '').trim())) return true;
      }
      return false;
    }
  });
}

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

  test('反馈中心 · 提交反馈页', async ({ page }) => {
    await login(page);
    await page.goto('/feedback');

    // 提交是一条**真路由**（`/feedback/new`），不是浮层：页头那颗渲染成链接。
    await page.getByRole('link', { name: '提交反馈' }).first().click();
    await expect(page).toHaveURL(/\/feedback\/new$/);

    // 范围取表单本身（`.sb-form`），不取 `body`：这一页的页头和底下那条说明都不是
    // 字段，喂给 `fieldDefects` 会把不相干的东西放在一起比。等的是表单真的画出来，
    // 不是地址变了 —— 地址先变、字段在后几帧里。
    const form = page.locator('.sb-form');
    await form.waitFor();
    await expect(form.getByLabel(/^标题/)).toBeVisible();
    expect(await fieldDefects(form)).toEqual([]);
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

  test('管理后台 · 队列页宽档（1920 视口）', async ({ page }) => {
    await login(page);
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto('/admin/queue');
    await expect(page.getByRole('heading', { name: '反馈队列' })).toBeVisible();
    await expect(page.locator('.qlist')).toBeVisible();

    // 1440 封顶居中：1920 去掉 64 全局 rail 与 200 侧栏后可用 1656，这一档两侧
    // 各留 108。量的参照物是 `.admin-shell__main`（它自己无 padding），不是按
    // 264 这个常数反推——侧栏宽度将来再改，这条用例量的东西不变。
    const box = await page.locator('.qpage__inner').boundingBox();
    const main = await page.locator('.admin-shell__main').boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(1438);
    expect(box!.width).toBeLessThanOrEqual(1442);
    const left = box!.x - main!.x;
    const right = main!.x + main!.width - (box!.x + box!.width);
    expect(Math.abs(left - 108)).toBeLessThanOrEqual(2);
    expect(Math.abs(right - 108)).toBeLessThanOrEqual(2);

    // 宽档的意义是整行在 1440 里放得下，不是把横滚挪到更宽的屏上。
    const noHScroll = await page.locator('.qlist').evaluate((el) => el.scrollWidth <= el.clientWidth + 1);
    expect(noHScroll).toBeTruthy();
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

  test('管理后台 · 模型页的「新增模型」对话框', async ({ page }) => {
    await login(page);
    await page.goto('/admin/models');

    // 模型表本身是张表（一个 `.v-field` 都没有），字段只在对话框里。所以范围取对话
    // 框，不取 `body` —— 空范围会让 `fieldDefects` 当场炸「这个范围里一个字段都没
    // 有」，而那正是它该做的。
    //
    // 页头那颗「新增模型」在网关读不到时照常可点：错误态只换掉那份列表，不换掉主
    // 操作。这一档因此不需要网关真的有数据 —— 它量的本来也只是几何。
    await page.getByRole('button', { name: '新增模型' }).first().click();
    // 按标题筛，不能取 `.v-overlay__content` 的第一个：那一个是导航条的 tooltip 浮
    // 层，不是对话框（「添加管理员」那一档就是在这里踩到的）。
    const dialog = page.locator('.v-overlay__content').filter({ hasText: '新增模型' });
    await dialog.waitFor();
    await expect(dialog.getByLabel('模型名')).toBeVisible();
    expect(await fieldDefects(dialog)).toEqual([]);
  });

  test('看板：三个分类里，没有两处文字画在同一个坐标上', async ({ page }) => {
    await login(page);

    // 三个分类都过一遍。宽窄两档都要：窄屏是 KPI 卡那一行最容易压的时候（卡片曾经
    // 写死 263px 宽，比窗口还宽，直接压到隔壁那张上）。
    // 三档都要：1440 是设计宽度（四列正好 263），**1100 是四列但比设计窄的那一段**
    // （每列比 263 小，卡片写死宽度时就是从这里开始压到隔壁），390 是手机（两列）。
    // 只测设计宽度的话，那个 bug 一次都不会露头 —— 这正是它当初能上线的原因。
    for (const size of [
      { width: 1440, height: 900 },
      { width: 1100, height: 900 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(size);
      await page.goto('/admin/dashboard');
      await expect(page.getByRole('heading', { name: '看板' })).toBeVisible();

      for (const tab of ['反馈', '用量', '平台']) {
        // `exact: true`：顶栏那颗「帮助与反馈」（另一个 PR）的可访问名字里也含「反馈」，
        // 而 Playwright 的 `name` 默认按**子串**匹配 —— 不加这一条，'反馈' 那一轮会同时
        // 命中它和这一页的分类页签，报 strict mode 违规。
        await page.getByRole('button', { name: tab, exact: true }).click();
        // 等这一类的数据到货（骨架上也有文字，量骨架没有意义）。
        await expect(page.locator('.ad__kpis .akpi__num').first()).toBeVisible();
        await expect(page.locator('.akpi__skel')).toHaveCount(0);
        expect(await textOverlaps(page.locator('body')), `${size.width}px · ${tab}`).toEqual([]);
      }
    }
  });

  test('看板：1920 宽档下内容列吃到 1440，KPI 网格不少于 4 轨', async ({ page }) => {
    await login(page);

    // 宽度变档的回执：1920 视口下内容列曾经停在 1100（约 1/3 是死空白）。admin 档
    // 是 1440，网格跟着容器查询升档 —— 这两条断言量的就是「宽出来的部分有人用」。
    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto('/admin/dashboard');
    await expect(page.getByRole('heading', { name: '看板' })).toBeVisible();

    // 三个分类的文字在宽档下也不压（宽档更容易出「网格升档后列数变了」的排版事故）。
    for (const tab of ['反馈', '用量', '平台']) {
      await page.getByRole('button', { name: tab, exact: true }).click();
      await expect(page.locator('.ad__kpis .akpi__num').first()).toBeVisible();
      await expect(page.locator('.akpi__skel')).toHaveCount(0);
      expect(await textOverlaps(page.locator('body')), `1920px · ${tab}`).toEqual([]);
    }

    // 内容列吃满 admin 档的 1440（侧栏展开时 1920 视口的可用宽是 1608，1440 居中）。
    const innerWidth = await page.locator('.ad__inner').evaluate((el) => el.getBoundingClientRect().width);
    expect(Math.round(innerWidth)).toBe(1440);
    // KPI 网格在 ≥1320 容器宽升到 auto-fit：轨道数不少于 4（此刻停在「平台」类，
    // 5 张卡）。
    const tracks = await page
      .locator('.ad__kpis')
      .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length);
    expect(tracks).toBeGreaterThanOrEqual(4);
  });
});
