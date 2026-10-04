import { test, expect, type Locator, type Page } from '@playwright/test';
import { api, apiLogin, openFirstProject } from './helpers';

// Parallel so CI shards split this file by test rather than handing one shard
// all of it: no test depends on another, and one CI worker still runs them one
// at a time.
test.describe.configure({ mode: 'parallel' });

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
// 「字段」和「标签」的盒子按页面的画法而定（`FieldDrawing`）：Vuetify 的 outlined 是
// 一套，平台自己用令牌画的表单（反馈提交页）是另一套。**判据是同一个**，见下。
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

/** 一页表单把字段画成了哪一套 —— 量的人得先知道，因为「字段」和「标签」的盒子在两套
 *  里不是同一个选择器，而**判据**是同一个。
 *
 *  平台上只有两套：
 *
 *    * `VUETIFY_FIELDS` —— Vuetify 的 outlined 控件。真正画出来的 label 住在描边的
 *      缺口里（`.v-field__outline .v-field-label`），而字段是 `.v-field` 那个盒子。
 *    * `TOKEN_FIELDS` —— 平台自己用令牌画的表单（反馈提交页，见
 *      `components/feedback/SubmitFeedbackForm.vue` 的文件头：它**故意**不用 Vuetify
 *      的输入框）。那里一对「标签 + 控件」是 `.sb-field`，标签在控件上方一行，是普通
 *      的 `.sb-label`，没有骑在边框上的那一半。
 *
 *  两套要守的是同一件事：**标签不压到别的字段上、也不被滚动容器裁掉**。2026-09 换成
 *  令牌之后这条用例红过一次，红在「`.sb-form` 里一个 `.v-field` 都没有」——量不到字段
 *  时这个函数会当场炸（空范围永远返回「没有缺陷」），而那不是缺陷、是**画法变了**。
 *  把画法变成参数，判据一个字不用改。 */
type FieldDrawing = { field: string; label: string };
const VUETIFY_FIELDS: FieldDrawing = { field: '.v-field', label: '.v-field__outline .v-field-label' };
const TOKEN_FIELDS: FieldDrawing = { field: '.sb-field', label: '.sb-label' };

async function fieldDefects(scope: Locator, drawing: FieldDrawing = VUETIFY_FIELDS): Promise<Defect[]> {
  return scope.evaluate((root: Element, { field, label: labelSelector }: FieldDrawing) => {
    const out: { kind: string; what: string }[] = [];
    const drawn = (el: Element) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };

    const boxes = [...root.querySelectorAll(field)].filter(drawn);
    // 量不到字段就是范围选错了。空范围永远返回「没有缺陷」，是一条只会绿的断言，
    // 所以这里炸掉而不是放过：`.v-overlay__content` 的第一个是 tooltip 的浮层而
    // 不是对话框，这一条就是这么发现的。
    if (!boxes.length) throw new Error('这个范围里一个字段都没有，这条断言等于没做');

    const nameOf = (el: Element) =>
      (el.querySelector('.v-field-label, .sb-label')?.textContent || '').trim() || '(无标签字段)';
    const scrollerOf = (el: Element) => {
      for (let p = el.parentElement; p; p = p.parentElement) {
        const overflow = getComputedStyle(p).overflowY;
        if (overflow === 'auto' || overflow === 'scroll' || overflow === 'hidden') return p;
      }
      return null;
    };

    // outlined 变体真正画出来的那个 label 住在描边的缺口里；字段内部还有一个同名
    // 副本，是 visibility:hidden 的占位，量它只会得到错的坐标。（令牌画的那一套里
    // 标签本来就是元素的文字，没有这种副本，这个过滤对它无害。）
    const labels = [...root.querySelectorAll(labelSelector)].filter(
      (el) => getComputedStyle(el).visibility !== 'hidden' && drawn(el)
    );

    for (const label of labels) {
      const own = label.closest(field);
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
  }, drawing);
}

test.describe('表单字段不会互相压住，也不会被裁掉', () => {
  test('artifact comparison keeps diff lines vertical on desktop and mobile', async ({ page }) => {
    await apiLogin(page);
    await page.locator('.app-rail-item--tile').first().click();
    await page.waitForURL(/\/projects\/[^/]+/);
    const projectId = page.url().match(/\/projects\/([^/?#]+)/)![1];
    const artifactId = '00000000-0000-0000-0000-000000000123';
    const versions = [1, 2].map(number => ({
      number, card_id: `version-${number}`, subject: `Report ${number}`,
      delivered_at: '2026-09-20T12:00:00Z', decided_by: 'alice',
      kind: 'file', filename: 'report.txt', url: null, bytes: 6, room: null,
    }));
    await page.route(`**/api/projects/${projectId}/artifacts/${artifactId}`, route => route.fulfill({
      json: { code: 200, data: { id: artifactId, name: 'Version comparison fixture', version: 2, delivered_at: versions[1].delivered_at, versions } },
    }));
    await page.route(`**/api/projects/${projectId}/artifacts/${artifactId}/compare?*`, route => route.fulfill({
      json: { code: 200, data: { kind: 'file', identical: false, note: null, files: [{ path: 'report.txt', diff: '--- report.txt\n+++ report.txt\n@@ -1 +1 @@\n-before\n+after', note: null }] } },
    }));
    await page.goto(`/projects/${projectId}/artifacts/${artifactId}?before=version-1&after=version-2`);
    await expect(page.getByText('+after', { exact: true })).toBeVisible();
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await expect(page.getByLabel('比较对象', { exact: true })).toBeVisible();
      const lines = await page.locator('.changes__diff span').evaluateAll(nodes => nodes.map(node => {
        const r = node.getBoundingClientRect();
        return { top: r.top, bottom: r.bottom, left: r.left };
      }));
      expect(lines.length).toBe(5);
      for (let i = 1; i < lines.length; i++) {
        expect(lines[i].top).toBeGreaterThanOrEqual(lines[i - 1].bottom);
        expect(lines[i].left).toBe(lines[0].left);
      }
      const bar = await page.locator('.compare__bar').boundingBox();
      expect(bar!.x + bar!.width).toBeLessThanOrEqual(width);
    }
  });

  test('账号页：登录、注册、找回密码，桌面与手机', async ({ page }) => {
    // 注册页的字段带常驻提示（邮箱、密码规则），手机上提示会折行，是这几页里最容易
    // 让下一个字段的浮动标签压上来的地方。
    for (const size of [
      { width: 1440, height: 900 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(size);
      for (const [path, label] of [
        ['/account/signin', '用户名'],
        ['/account/signup', '用户名'],
        ['/account/recover/password', '注册邮箱'],
      ]) {
        await page.goto(path);
        await page.getByLabel(label, { exact: true }).waitFor();
        expect(await fieldDefects(page.locator('body'))).toEqual([]);
        expect(await textOverlaps(page.locator('body'))).toEqual([]);
      }
    }
  });

  test('「修改 AI 队友」对话框', async ({ page }) => {
    await apiLogin(page);
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
    await apiLogin(page);
    await page.goto('/feedback');

    // 提交是一条**真路由**（`/feedback/new`），不是浮层：页头那颗渲染成链接。
    await page.getByRole('link', { name: '提交反馈' }).first().click();
    await expect(page).toHaveURL(/\/feedback\/new$/);

    // 范围取表单本身（`.sb-form`），不取 `body`：这一页的页头和底下那条说明都不是
    // 字段，喂给 `fieldDefects` 会把不相干的东西放在一起比。等的是表单真的画出来，
    // 不是地址变了 —— 地址先变、字段在后几帧里。
    //
    // 画法是 `TOKEN_FIELDS`：这一页是平台自己用令牌画的（`SubmitFeedbackForm.vue`
    // 的文件头写了为什么不用 Vuetify 的输入框），`.sb-form` 里一个 `.v-field` 都没
    // 有。量到的仍然是老一套：标签不压到别的字段上、也不被滚动容器裁掉。
    const form = page.locator('.sb-form');
    await form.waitFor();
    await expect(form.getByLabel(/^标题/)).toBeVisible();
    expect(await fieldDefects(form, TOKEN_FIELDS)).toEqual([]);
  });

  test('管理后台 · 反馈队列里打开一条', async ({ page }) => {
    await apiLogin(page);

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
    await apiLogin(page);
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto('/admin/queue');
    await expect(page.getByRole('heading', { name: '反馈', exact: true })).toBeVisible();
    await expect(page.locator('.qlist')).toBeVisible();

    // 1440 封顶居中。量的参照物是页面正文那一格（`.app-page__body`），不是按侧栏宽度
    // 反推——侧栏能拖宽拖窄，这条用例量的东西不变：列宽 1440，两侧留白相等。
    const box = await page.locator('.app-page__column--admin').boundingBox();
    const main = await page.locator('.app-page__body').evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return { x: rect.x, width: el.clientWidth };
    });
    expect(box!.width).toBeGreaterThanOrEqual(1438);
    expect(box!.width).toBeLessThanOrEqual(1442);
    const left = box!.x - main.x;
    const right = main.x + main.width - (box!.x + box!.width);
    expect(Math.abs(left - right)).toBeLessThanOrEqual(2);

    // 宽档的意义是整行在 1440 里放得下，不是把横滚挪到更宽的屏上。
    const noHScroll = await page.locator('.qlist').evaluate((el) => el.scrollWidth <= el.clientWidth + 1);
    expect(noHScroll).toBeTruthy();
  });

  test('管理后台 · 「添加管理员」那张表单', async ({ page }) => {
    await apiLogin(page);
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
    await apiLogin(page);
    await page.goto('/admin/models');

    // 模型表本身是张表（一个 `.v-field` 都没有），字段只在对话框里。所以范围取对话
    // 框，不取 `body` —— 空范围会让 `fieldDefects` 当场炸「这个范围里一个字段都没
    // 有」，而那正是它该做的。
    //
    // 这一档要网关可达。「新增模型」在网关不可达 / 没配管理密钥时是 `disabled` 的
    // （`gatewayDown`，main 上也是），点不下去就到不了对话框 —— 症状是 Playwright
    // 报 `element is not enabled` 等满超时，看着像布局挂了，其实是按钮压根没启用。
    // playwright.config.ts 给后端接了桩网关，走的是「网关答话」这条路；拿一个没接
    // 网关的后端跑就必挂（曾经把这档误判成开发服务器冷启动，就是因为这里写着「不
    // 需要网关真的有数据」—— 那句是错的）。
    await page.getByRole('button', { name: '新增模型' }).first().click();
    // 按标题筛，不能取 `.v-overlay__content` 的第一个：那一个是导航条的 tooltip 浮
    // 层，不是对话框（「添加管理员」那一档就是在这里踩到的）。
    const dialog = page.locator('.v-overlay__content').filter({ hasText: '新增模型' });
    await dialog.waitFor();
    await expect(dialog.getByLabel('模型名')).toBeVisible();
    expect(await fieldDefects(dialog)).toEqual([]);
  });

  test('看板：三个分类里，没有两处文字画在同一个坐标上', async ({ page }) => {
    await apiLogin(page);

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
      // 手机上页名在顶栏里，不是页内的标题；分类导轨两档都在。
      await expect(page.locator('.ad__kinds')).toBeVisible();

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
    await apiLogin(page);

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

    // 内容列吃满 admin 档的 1440（1920 视口去掉全局 rail 与侧栏后仍宽于 1440，居中）。
    const innerWidth = await page
      .locator('.app-page__column--admin')
      .evaluate((el) => el.getBoundingClientRect().width);
    expect(Math.round(innerWidth)).toBe(1440);
    // KPI 网格在 ≥1320 容器宽升到 auto-fit：轨道数不少于 4（此刻停在「平台」类，
    // 5 张卡）。
    const tracks = await page
      .locator('.ad__kpis')
      .evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length);
    expect(tracks).toBeGreaterThanOrEqual(4);
  });

  test('个人主页：全站和项目里两个入口，宽窄三档，没有两处文字画在同一个坐标上', async ({ page }) => {
    await apiLogin(page);
    await openFirstProject(page);
    const projectPath = new URL(page.url()).pathname.match(/^\/projects\/[^/]+/)?.[0];
    expect(projectPath).toBeTruthy();

    // 话题那一行在桌面上横排四样东西（标题、项目、条数、时间），窄一点的桌面宽度
    // 是它们最容易挤到一起的时候；手机上换成两行。
    for (const size of [
      { width: 1440, height: 900 },
      { width: 1100, height: 900 },
      { width: 390, height: 844 },
    ]) {
      await page.setViewportSize(size);
      for (const path of ['/users/alice', `${projectPath}/members/alice`]) {
        await page.goto(path);
        await expect(page.locator('[data-section="activity"]')).toBeVisible();
        expect(await textOverlaps(page.locator('.profile')), `${size.width}px · ${path}`).toEqual([]);
      }
    }
  });
});

// 侧栏顶上项目名那一条，和右边内容区的页头是同一条线：一样高、顶在同一处，两条底
// 线接成一条。这几页以前各画各的大标题，那条线到了这几页就断在半空——从房间切到
// 成员页，页头一会儿有一会儿没有。量的是渲染出来的盒子，因为这种错 vitest 和类型检
// 查都看不见。项目设置不在这里：它是盖在整个窗口上的一层，没有这条页头。
test('项目里每一页的页头都和侧栏项目名那一条对齐', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await apiLogin(page);
  await openFirstProject(page);
  const projectPath = new URL(page.url()).pathname.match(/^\/projects\/[^/]+/)?.[0];
  expect(projectPath).toBeTruthy();

  for (const sub of ['running', 'members', 'members/alice', 'docs/charter', 'library']) {
    await page.goto(`${projectPath}/${sub}`);
    const head = page.locator('.app-page__head');
    await expect(head).toBeVisible();
    const [side, main] = await Promise.all([page.locator('.rail-header').boundingBox(), head.boundingBox()]);
    expect(side, sub).not.toBeNull();
    expect(main, sub).not.toBeNull();
    expect(main!.y, `${sub} · top`).toBeCloseTo(side!.y, 0);
    expect(main!.height, `${sub} · height`).toBeCloseTo(side!.height, 0);
  }
});

// 设置是盖在整个窗口上的一层（SettingsOverlay），它里面的下拉菜单和对话框却由 Vuetify
// 挂在 body 下另一层里。两层 z-index 一样时，DOM 里靠后的画在上面；而 Vuetify 那一层
// 在页面加载时就被提示气泡建好了，排在设置层前面，于是菜单打开了却画在设置层底下，
// 看上去是一个空下拉。单元测试没有布局，只有在真浏览器里点一下才看得见。
test('设置层里打开的下拉菜单画在设置层上面', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await apiLogin(page);
  await openFirstProject(page);
  const projectPath = new URL(page.url()).pathname.match(
    /^\/projects\/[^/]+/,
  )?.[0];
  expect(projectPath).toBeTruthy();

  await page.goto(`${projectPath}/settings/agents`);
  const field = page
    .locator('.v-select')
    .filter({ hasText: '项目主模型' })
    .first();
  await field.click();
  // A click checks that the option is the topmost thing under the pointer; under the
  // settings layer it is not, and the click is refused rather than landing on it.
  await page
    .getByRole('option', { name: 'DeepSeek V4.1 Flash' })
    .click({ timeout: 5_000 });
  await expect(field).toContainText('DeepSeek V4.1 Flash');
});

// 读的那一栏（`AppPage` 的 `read` 档）在宽屏上封顶居中，页头那一行跟着它：标题和正文
// 从同一条竖线开始；满宽那一档的标题也和正文缩进一样多。以前页头贴着内容区左边，正文居中，1440 宽时标题和正文差 72px，
// 1920 宽时差三百多。量的是渲染出来的字，因为这种错 vitest 和类型检查都看不见。
test('页头标题和正文同一条左沿', async ({ page }) => {
  await apiLogin(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await openFirstProject(page);
  const projectPath = new URL(page.url()).pathname.match(/^\/projects\/[^/]+/)?.[0];
  expect(projectPath).toBeTruthy();

  for (const width of [1440, 1920]) {
    await page.setViewportSize({ width, height: 900 });
    for (const path of ['/inbox', `${projectPath}/members`]) {
      await page.goto(path);
      const title = page.locator('.app-page__title');
      await expect(title).toBeVisible();
      const column = page.locator('.app-page__column--read');
      const [titleX, bodyX] = await Promise.all([
        title.evaluate((el) => el.getBoundingClientRect().x),
        column.evaluate((el) => el.getBoundingClientRect().x + parseFloat(getComputedStyle(el).paddingLeft)),
      ]);
      expect(Math.abs(titleX - bodyX), `${path} @ ${width}`).toBeLessThanOrEqual(1);
    }
    // 满宽那一档（资料库）：正文铺满，自己从左边缩进；页头标题跟它缩进同样多。量的是
    // 正文里最靠左的那行字。
    await page.goto(`${projectPath}/library`);
    const title = page.locator('.app-page__title');
    await expect(title).toBeVisible();
    await expect(page.locator('.app-page__body')).not.toHaveText('');
    const titleX = await title.evaluate((el) => el.getBoundingClientRect().x);
    const bodyX = await page.locator('.app-page__body').evaluate((body) => {
      const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
      let left = Infinity;
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!node.textContent?.trim()) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const rect of range.getClientRects()) if (rect.width > 0) left = Math.min(left, rect.left);
      }
      return left;
    });
    expect(Math.abs(titleX - bodyX), `library @ ${width}`).toBeLessThanOrEqual(1);
  }
});

/** 首屏以下的内容里，有没有**够不着**的 —— 它在视野外，而页面上没有任何祖先能滚。
 *
 *  平台的外壳把整页钉死在窗口高度（`styles/common.scss` 把 html/body/#app 定成
 *  `height:100%; overflow:hidden`），文档层永不滚动，所以滚动得由每一页自己领。
 *  领漏了不会报错、不会让任何单测变红，typecheck / eslint / stylelint 也都看不见
 *  —— 只有量屏幕看得见。2026-09 的小队详情就栽在这里：容器 630px、内容 1576px，
 *  **946px 永远够不着**（成员一多才露馅，短内容看不出）。
 *
 *  判据刻意只有两条，宁可漏报也不误报：
 *    1. 元素整体落在首屏以下（`rect.top >= innerHeight`）；
 *    2. 从它往上**没有任何祖先真的能滚**（`overflow-y: auto|scroll` 且
 *       `scrollHeight > clientHeight`）。
 *  只判第 1 条会把所有正常滚动都报成缺陷；只看「最近的那个裁剪祖先」又会漏 ——
 *  小队详情里最近的一层（`.content-body`）当时是被内容撑高的，并没有裁到谁，
 *  真正裁人的是更外面的 `.layout-container`。
 */
async function unreachableBelowFold(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const vh = window.innerHeight;
    const scroller = (el: Element): boolean => {
      for (let p = el.parentElement; p; p = p.parentElement) {
        const s = getComputedStyle(p);
        if ((s.overflowY === 'auto' || s.overflowY === 'scroll') && p.scrollHeight > p.clientHeight + 1) {
          return true;
        }
      }
      return false;
    };
    const out: string[] = [];
    const reported: Element[] = [];
    for (const el of document.body.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (!(el.checkVisibility?.({ checkVisibilityCSS: true }) ?? true)) continue;
      const text = (el.textContent || '').trim();
      if (!text) continue;
      if (r.top < vh - 2) continue;
      if (scroller(el)) continue;
      if (reported.some((r0) => r0.contains(el))) continue;
      reported.push(el);
      const cls = typeof el.className === 'string' ? (el.className.split(/\s+/)[0] ?? '') : '';
      out.push(`${el.tagName.toLowerCase()}${cls ? '.' + cls : ''}「${text.slice(0, 24)}」`);
      if (out.length > 8) break;
    }
    return out;
  });
}

// vite dev 按需编译：某个路由第一次被访问时才现编译它的模块图，冷启动能到 30s 以上
// （`playwright.config.ts` 就是为这件事把整条用例的超时提到 60s 的）。小队详情不是别的
// 用例会先踩到的路由，默认 5s 的前置等待会在冷编译上误报成「元素不存在」——2026-09-25
// 实测连 signin 页都能 60s 出不来。所以前置等待放到 30s：**仍然要求元素真的出现**，
// 只是给冷编译留出时间，不是把断言放水。
const ROUTE_READY_MS = 30_000;

// 热路由：模块已经编译过，一次导航 + 一次渲染 + 一次接口回来的预算。
const WARM_STEP_MS = 10_000;

// 小队详情这一条要导航 8 次（下面 2 个窗口 × 4 个 tab），而四个 tab 是**四个各自懒加载
// 的路由块**（`router/teams.ts`：`detail/TeamProjects.vue` / `Members.vue` /
// `Knowledge.vue` / `Compute.vue`），所以这条用例天生是「冷 4 次 + 热 12 次」。
// 两段的等待预算都写在这里：**整条用例的预算必须容得下它们之和**。不然后果和
// `WorkPanelTabs.test.ts` 那次一模一样 —— 内层还没轮到说话，外层先把用例掐了，报出来
// 只有一句「Test timeout of 60000ms exceeded」（playwright 那边是
// `Error: Test timeout of 60000ms exceeded`），看不出在等第几次导航、等的是哪一步。
// 2026-09-26 它就是因此成了 main 上的常客：当天至少 8 次跑里先失败、靠 `retries: 2`
// 才绿，CI 每次白等一两分钟。
const TAB_PATHS = ['', 'members', 'knowledge', 'compute'];
const VIEWPORT_SIZES = [
  // 视图高度压到 360 是故意的：这一套种子数据只有 5 个小队成员，靠内容自然长到溢出
  // 不可靠；把窗口压矮能让「内容比窗口高」这件事在任何数据下都成立，而宽度保持在
  // 960 以上 —— 小队详情在 ≤960px 会切成自适应高度、滚动交还外壳，那是另一条分支，
  // 这里要量的是桌面那条。
  { width: 1280, height: 663 },
  { width: 1100, height: 360 },
];
const WARMUP_MS = TAB_PATHS.length * 2 * ROUTE_READY_MS; // 每个 tab 冷一次：出现 + 画出字
const SWEEP_MS = VIEWPORT_SIZES.length * TAB_PATHS.length * 2 * WARM_STEP_MS; // 每个窗口 × 每个 tab 两道等待

test.describe('首屏以下的内容不会被裁掉而没人能滚', () => {
  test('小队详情：四个 tab 在窄窗口下都够得着底部', async ({ page }) => {
    // 这条用例真正需要的范围是「冷 4 次 + 热 12 次」两段之和，比 config 里给的 60s 大
    // 一个数量级。它只在「又慢又没错」时才用得满：真出问题会在第一道等待上炸出来
    // （最多 ROUTE_READY_MS），不会一路拖到底。
    test.setTimeout(WARMUP_MS + SWEEP_MS + 20_000);

    await apiLogin(page);

    // 冷编译这份钱在循环外付清，每个 tab 一次。等的东西和下面循环里完全一样
    // （`.app-page__body` 真的出现、里面真的画出了字），一字没有放水 —— 只是预算按冷的
    // 给。这样下面量到的才是布局本身，不是 Vite 的编译速度。
    for (const path of TAB_PATHS) {
      await page.goto(`/teams/team-1${path ? '/' + path : ''}`);
      await expect(page.locator('.app-page__body')).toBeVisible({ timeout: ROUTE_READY_MS });
      await expect
        .poll(async () => page.locator('.app-page__body').evaluate((el) => (el.textContent || '').trim().length), {
          message: `/teams/team-1${path ? '/' + path : ''}：工作区一直是空的，这一条等于没做`,
          timeout: ROUTE_READY_MS,
        })
        .toBeGreaterThan(0);
    }

    for (const size of VIEWPORT_SIZES) {
      await page.setViewportSize(size);
      for (const path of TAB_PATHS) {
        await page.goto(`/teams/team-1${path ? '/' + path : ''}`);

        // 量不到这一层就是范围选错了（比如 alice 不是成员，看到的是对外主页），
        // 空范围永远返回「没有缺陷」，是一条只会绿的断言，所以这里炸掉而不是放过。
        await expect(
          page.locator('.app-page__body'),
          `${size.width}×${size.height} · /teams/team-1/${path}：没落在小队工作区里，这条断言等于没做`,
        ).toBeVisible({ timeout: WARM_STEP_MS });

        // `.app-page__body` 一出现就量还不够 —— 它的内容（成员/资料/算力）是另一次请求
        // 填进去的，量早了会量到一个半空的工作区，而**空范围永远返回「没有缺陷」**。
        // 等这一层里真的画出了字（页头在它外面，所以量到的就是当前 tab 自己的内容），
        // 四个 tab 在种子数据下都有内容，等不到就是页面根本没起来，该炸。
        await expect
          .poll(async () => page.locator('.app-page__body').evaluate((el) => (el.textContent || '').trim().length), {
            message: `${size.width}×${size.height} · /teams/team-1/${path}：工作区一直是空的，这一条等于没做`,
            timeout: WARM_STEP_MS,
          })
          .toBeGreaterThan(0);

        expect(await unreachableBelowFold(page), `${size.width}×${size.height} · /teams/team-1/${path}`).toEqual([]);
      }
    }
  });

  // 法务页（`/legal/terms`、`/legal/privacy`）其实是同一类，但它已经有人守住，
  // 就不在这里再摆一份：`e2e/tests/legal-pages.spec.ts`（随修复 #1722 一起进来）
  // 直接驱动滚轮和 Home/End，量的是「读到读不到最后一节」，比这套几何判据更贴读者。
});

// 手机外壳上的按钮，手指点得中：能点的范围至少 44×44（docs/design-system.md 的手机
// 一节）。量的不是按钮画出来的盒子——小按钮靠 `.tap-target` 的伪元素把能点的那块撑
// 开，`getBoundingClientRect` 看不见伪元素。量的是**浏览器认为点到了谁**：从按钮中心
// 往上下左右各走 21px，那一点上 `elementFromPoint` 还得是这颗按钮（或它里面的东西）。
// 撑得不够、或者被隔壁那颗盖住了一截，都会在这里红。
test('手机外壳：顶栏和底栏上每一颗按钮，手指能点的范围至少 44×44', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await apiLogin(page);
  const rows = await openFirstProject(page);
  const projectPath = new URL(page.url()).pathname.match(/^\/projects\/[^/]+/)?.[0];
  expect(projectPath).toBeTruthy();
  await rows.first().click();
  await page.waitForURL(/\/topics\//);
  const topicHref = new URL(page.url()).pathname;

  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ['/home', '/inbox', projectPath!, `${projectPath}/members`, `${projectPath}/routines`, topicHref]) {
    await page.goto(path);
    await expect(page.locator('.v-app-bar')).toBeVisible();
    await page.waitForLoadState('networkidle');
    const { count, misses } = await page.evaluate(() => {
      const CONTROL = ':is(a[href], button, [role="tab"])';
      const scope = `.v-app-bar ${CONTROL}, .v-bottom-navigation ${CONTROL}`;
      const controls = [...document.querySelectorAll<HTMLElement>(scope)]
        .filter((el) => el.checkVisibility?.({ checkVisibilityCSS: true }) ?? true)
        .filter((el) => {
          const r = el.getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        })
        // 按钮里套按钮时只量外层那颗。
        .filter((el) => !el.parentElement?.closest(scope));
      const out: string[] = [];
      for (const el of controls) {
        const r = el.getBoundingClientRect();
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        for (const [dx, dy] of [[-21, 0], [21, 0], [0, -21], [0, 21]]) {
          // 贴着屏幕边的那一侧，手指按在屏幕边上也算点到。
          const x = Math.min(Math.max(cx + dx, 0), innerWidth - 1);
          const y = Math.min(Math.max(cy + dy, 0), innerHeight - 1);
          const hit = document.elementFromPoint(x, y);
          if (!hit || !(hit === el || el.contains(hit))) {
            const name = (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 16);
            out.push(`「${name}」${Math.round(r.width)}×${Math.round(r.height)}，(${dx}, ${dy}) 处点到的是别的`);
            break;
          }
        }
      }
      return { count: controls.length, misses: out };
    });
    // 一颗都没量到就是范围选错了，空范围永远是「没有缺陷」。
    expect(count, `${path}：顶栏和底栏上一颗按钮都没量到`).toBeGreaterThan(0);
    expect(misses, path).toEqual([]);
  }
});

// 房间的输入框：输入框下面那一行按钮只会越加越多（附件、照片、清单、提问、提醒、
// 交给芝士、发送）。触屏上按钮之间要拉开、能点的范围要撑到 44×44，同样几颗在桌面
// 上放得下，到手机上就会顶出输入框的边、或者互相盖住。所以这里在真触屏（pointer:
// coarse）的手机宽度和桌面宽度各量一次：没有一颗伸出输入框，每一颗手指都点得中。
test.describe('房间输入框：下面那一行放得下，手指点得中', () => {
  test.use({ hasTouch: true, isMobile: true });

  async function measureComposer(page: Page) {
    return page.evaluate(() => {
      // 量按钮自己的盒子，不量这一行的 scrollWidth：触屏上每颗按钮的伪元素把能点的
      // 范围撑到 44×44，最右边那颗发送键的撑开部分本来就探出这一行 8px，那不算伸出去。
      const box = document.querySelector<HTMLElement>('.composer-box');
      const row = document.querySelector<HTMLElement>('.composer-actions');
      if (!box || !row) return null;
      const edge = box.getBoundingClientRect();
      const controls = [...row.querySelectorAll<HTMLElement>('button')].filter((el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      const outside: string[] = [];
      const wrapped: string[] = [];
      const misses: string[] = [];
      for (const el of controls) {
        const r = el.getBoundingClientRect();
        const name = (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').trim().slice(0, 16);
        if (r.left < edge.left - 0.5 || r.right > edge.right + 0.5) outside.push(`「${name}」${Math.round(r.left)}–${Math.round(r.right)}`);
        // 挤不下时按钮先被压窄，字折成两行（「交给」竖着排），还没伸出去就已经坏了。
        // 只量字（文字节点），图标和 Vuetify 的叠层不算行。
        const lines = new Set<number>();
        const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        for (let node = walk.nextNode(); node; node = walk.nextNode()) {
          if (!node.textContent?.trim()) continue;
          const range = document.createRange();
          range.selectNodeContents(node);
          for (const line of range.getClientRects()) if (line.width > 0) lines.add(Math.round(line.top));
        }
        if (lines.size > 1) wrapped.push(`「${name}」折成了 ${lines.size} 行`);
        if (!matchMedia('(pointer: coarse)').matches) continue;
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        for (const [dx, dy] of [[-21, 0], [21, 0], [0, -21], [0, 21]]) {
          const x = Math.min(Math.max(cx + dx, 0), innerWidth - 1);
          const y = Math.min(Math.max(cy + dy, 0), innerHeight - 1);
          const hit = document.elementFromPoint(x, y);
          if (!hit || !(hit === el || el.contains(hit))) {
            misses.push(`「${name}」(${dx}, ${dy}) 处点到的是别的`);
            break;
          }
        }
      }
      return {
        coarse: matchMedia('(pointer: coarse)').matches,
        count: controls.length,
        outside,
        wrapped,
        misses,
      };
    });
  }

  test('手机（触屏）和桌面两档', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    await rows.first().click();
    await page.waitForURL(/\/topics\//);
    const topicHref = new URL(page.url()).pathname;

    for (const size of [
      { width: 375, height: 812 },
      { width: 1280, height: 800 },
    ]) {
      await page.setViewportSize(size);
      await page.goto(topicHref);
      // 手机上房间先开在别的页签，对话在「对话」页签里。
      const chatTab = page.getByRole('tab', { name: '对话', exact: true });
      if (size.width < 960) await chatTab.click();
      await expect(page.locator('.composer-actions')).toBeVisible();
      // 有字时发送键才可点（禁用的按钮不接点击，量不出来）。只填不发。
      await page.locator('.composer-input textarea:not([aria-hidden])').fill('量一下');
      const got = await measureComposer(page);
      expect(got, `${size.width}：没找到输入框`).not.toBeNull();
      if (size.width < 960) expect(got!.coarse, '手机这一档要在触屏下量').toBe(true);
      // 附件 + 交给芝士 + 发送，至少这三颗；一颗都没量到就是范围选错了。
      expect(got!.count, `${size.width}`).toBeGreaterThanOrEqual(3);
      expect(got!.outside, `${size.width}：伸出输入框的按钮`).toEqual([]);
      expect(got!.wrapped, `${size.width}：被压窄、字折了行的按钮`).toEqual([]);
      expect(got!.misses, `${size.width}`).toEqual([]);
    }
  });
});

// 设置浮层（`SettingsOverlay`）的外壳几何：目录灰栏钉窗口左缘、定宽 264，内容列最宽 720，
// 在「窗口减目录」剩下的地方居中，关闭按钮右缘贴内容列右缘。这几条以前都不成立 —— 最早灰栏
// 随窗口长到 440、内容列贴着灰栏靠左，右边空出一大片；改了一版又变成「目录 + 内容列」一组
// 居中，目录和内容之间隔出 500 多 px。四类设置页（个人、资料、项目、空间）各写各的宽度，
// 同一条内容列里对不齐。量的都是渲染出来的盒子：这种错 vitest、typecheck、stylelint 全看不见。
test.describe('设置浮层：目录钉左缘、内容列在剩余空间居中、关闭按钮不随内容滚走', () => {
  // 量当前打开的设置页：灰栏、内容列、关闭按钮三个盒子，外加内容列在主滚动区里两侧的
  // 留白。留白用 `clientWidth`（滚动条槽算在里面），居中的基准才是同一个宽度 —— 和
  // 「管理后台 · 队列页宽档」那条一个量法。
  async function overlayGeometry(page: Page) {
    return page.evaluate(() => {
      const box = (sel: string) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { left: r.left, right: r.right, width: r.width, top: r.top, height: r.height };
      };
      const main = document.querySelector<HTMLElement>('.so__main');
      const content = document.querySelector('.so__content');
      let gaps: { left: number; right: number } | null = null;
      if (main && content) {
        const mr = main.getBoundingClientRect();
        const cr = content.getBoundingClientRect();
        gaps = { left: cr.left - mr.left, right: mr.left + main.clientWidth - cr.right };
      }
      return { side: box('.so__side'), content: box('.so__content'), close: box('.so__close'), gaps };
    });
  }

  test('桌面三档：目录定宽 264 贴左缘、内容列 720 居中、关闭按钮贴内容右缘且不压页头', async ({ page }) => {
    await apiLogin(page);
    for (const width of [1280, 1440, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/users/settings/security');
      await expect(page.locator('.so__content')).toBeVisible();
      const g = await overlayGeometry(page);
      const label = `${width}px`;

      // 目录灰栏定宽 264，钉在窗口左缘 —— 不再随窗口变宽。
      expect(g.side, `${label}：没落在桌面外壳里`).not.toBeNull();
      expect(Math.abs(g.side!.width - 264), `${label}：目录灰栏宽`).toBeLessThanOrEqual(3);
      expect(g.side!.left, `${label}：灰栏没贴窗口左缘`).toBeLessThanOrEqual(1);

      // 内容列最宽 720 —— 四类设置页共用同一条。
      expect(g.content, `${label}：没有内容列`).not.toBeNull();
      expect(Math.round(g.content!.width), `${label}：内容列宽`).toBe(720);

      // 内容列在「窗口减目录」剩下的地方居中：两侧留白相等（右边扣掉滚动条槽，容差放到 8）。
      expect(g.gaps, `${label}：量不到主区`).not.toBeNull();
      expect(Math.abs(g.gaps!.left - g.gaps!.right), `${label}：内容列没在剩余空间里居中`).toBeLessThanOrEqual(8);

      // 关闭按钮右缘贴内容列右缘，并且整颗落在内容上方的内距里，不压页头的按钮。
      expect(g.close, `${label}：没有关闭按钮`).not.toBeNull();
      expect(Math.abs(g.close!.right - g.content!.right), `${label}：关闭按钮没贴内容右缘`).toBeLessThanOrEqual(3);
      const headTop = await page.evaluate(() => {
        const first = document.querySelector('.so__content > *');
        const kids = first ? [...first.querySelectorAll('h1, h2, button, a')] : [];
        return kids.length ? Math.min(...kids.map((k) => k.getBoundingClientRect().top)) : Infinity;
      });
      expect(g.close!.top + g.close!.height, `${label}：关闭按钮压到了页头`).toBeLessThanOrEqual(headTop + 1);
    }
  });

  test('同一宽度下，四类设置页的内容列左右缘一致', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await apiLogin(page);
    await openFirstProject(page);
    const projectPath = new URL(page.url()).pathname.match(/^\/projects\/[^/]+/)?.[0];
    expect(projectPath).toBeTruthy();

    const paths = [
      '/users/settings/security',
      '/users/settings/profile',
      '/users/settings/devices',
      `${projectPath}/settings/agents`,
    ];
    const seen: { path: string; left: number; width: number }[] = [];
    for (const path of paths) {
      await page.goto(path);
      await expect(page.locator('.so__content')).toBeVisible();
      const g = await overlayGeometry(page);
      expect(g.content, `${path}：没有内容列`).not.toBeNull();
      seen.push({ path, left: g.content!.left, width: g.content!.width });
    }
    // 资料页原来写死 660（`--page-w-read`）、项目设置页原来没上限，都对不上别的页。
    for (const s of seen) {
      expect(Math.abs(s.left - seen[0].left), `${s.path}：内容列左缘`).toBeLessThanOrEqual(2);
      expect(Math.abs(s.width - seen[0].width), `${s.path}：内容列宽`).toBeLessThanOrEqual(2);
    }
  });

  test('平板 768：内容列 720 居中', async ({ page }) => {
    await apiLogin(page);
    await page.setViewportSize({ width: 768, height: 900 });
    await page.goto('/users/settings/profile');
    await expect(page.locator('.so__content')).toBeVisible();
    const gaps = await page.evaluate(() => {
      const phone = document.querySelector<HTMLElement>('.so__phone');
      const content = document.querySelector('.so__content');
      if (!phone || !content) return null;
      const pr = phone.getBoundingClientRect();
      const cr = content.getBoundingClientRect();
      return { left: cr.left - pr.left, right: pr.left + phone.clientWidth - cr.right };
    });
    // 窄于 960 是手机外壳（没有灰栏）：内容列铺到自己的 720 上限后就该居中，
    // 以前它贴着左边，右边空一整条。
    expect(gaps, '768px：没落在手机外壳里').not.toBeNull();
    expect(Math.abs(gaps!.left - gaps!.right), '768px：内容列没居中').toBeLessThanOrEqual(8);
  });

  test('往下滚一屏，关闭按钮仍在视口里、位置不变', async ({ page }) => {
    await apiLogin(page);
    await page.setViewportSize({ width: 1280, height: 480 });
    await page.goto('/users/settings/security');
    await expect(page.locator('.so__close')).toBeVisible();
    const before = await page.locator('.so__close').boundingBox();
    expect(before).not.toBeNull();

    // 真的滚了一屏：这一页不滚的话下面量的就不是「滚了还在」。
    const scrolled = await page.locator('.so__main').evaluate((el) => {
      el.scrollTop = el.clientHeight;
      return { top: el.scrollTop, canScroll: el.scrollHeight > el.clientHeight + 1 };
    });
    expect(scrolled.canScroll, '这一页不滚，这一条等于没做').toBeTruthy();
    expect(scrolled.top, '没滚下去').toBeGreaterThan(0);

    await expect(page.locator('.so__close')).toBeVisible();
    const after = await page.locator('.so__close').boundingBox();
    expect(after).not.toBeNull();
    expect(Math.abs(after!.y - before!.y), '关闭按钮跟着内容滚走了').toBeLessThanOrEqual(1);
    expect(after!.y, '关闭按钮滚出了视口').toBeGreaterThanOrEqual(0);
    expect(after!.y + after!.height, '关闭按钮滚出了视口').toBeLessThanOrEqual(480);
  });
});
