import { test, expect, type Page } from '@playwright/test';
import { apiLogin, openFirstProject } from './helpers';

// 触屏上「手指点得中」：一颗控件能点的范围至少 44×44（docs/design-system.md 的手机
// 一节，也是 WCAG 2.5.5 Target Size 的 AAA 档）。
//
// 这件事**画出来的盒子量不出来**。`.base-btn` 的 sm/md 只有 28/36px 高，靠 `::before`
// 把能点的那块往四周撑到 44×44（`components/base/BaseButton.vue` 的
// `@media (pointer: coarse)`）；`.tap-target` 是同一套路的全局版本（`style.css`，
// 29 个调用处各自记着加）。而 `getBoundingClientRect` 看不见伪元素，
// `getComputedStyle(el, '::before')` 读到的是**声明值**、不是渲染值。所以这里量的是
// **浏览器认为点到了谁**：从控件中心往四个方向各走一步一格，看 `elementFromPoint`
// 落回谁身上 —— CSSOM View 规定命中伪元素时返回它所属的元素，撑开的那块因此量得到。
//
// 三处容易踩空：
//
//   * **触屏要显式开。** `pointer` 认的是主指针，光把视口调成 390px 它还是 `fine`；
//     要 `test.use({ hasTouch: true })`，Chromium 才报 `pointer: coarse`。不命中
//     `@media (pointer: coarse)` 时撑开的那块根本不存在，量出来「刚好够 44」的是别
//     的东西 —— 这条测试就变成一句和触屏无关的话。
//   * **±21 而不是 ±22。** 44 减去 1px 留给亚像素与取整：一颗真的 44×44 的盒子，
//     中心左右各 21px 那一点一定还在里面。
//   * **光有断言不够。** 画出来本来就够 44 的控件上，撑开的那块盖在里面，探针量到
//     也说明不了机制还在。所以每个用例都配一个**阴性对照**：一颗 20×20、没有任何
//     撑开机制的按钮，探针必须把它报成缺陷 —— 报不出来就是探针坏了，不是页面对了。
//
// 和「并排的两颗会不会互相盖住」是两件事：撑开会让相邻控件的可点区重叠，`BaseButton`
// 的文件头因此要求并排的图标按钮之间留缝。这一份只量「一颗自己够不够大」。

const MIN = 21; // 44/2 减去 1px 亚像素余量
/** 阴性对照的记号：探针必须把它报成缺陷，报不出来就是探针坏了。 */
const NEGATIVE = '__negative__';

type Reading = {
  name: string;
  /** 画出来的盒子 —— 和「能点到多大」是两回事。 */
  width: number;
  height: number;
  /** 从中心往四个方向各能点到多远。 */
  extent: { left: number; right: number; up: number; down: number };
  /** 44×44 的四个角那一点上，还认不认这颗。 */
  corners: boolean[];
  misses: string[];
};

/** 量 scope 里每一颗 `[data-probe]`（或给定选择器）能点到多大。
 *
 *  只量**中心落在视口里**的控件：`elementFromPoint` 对视口外的点一律返回 null，
 *  量了也是一片「点不到」。贴边的点钳进视口（手指按在屏幕边上也算点到），和
 *  `layout-invariants.spec.ts` 里那条顶栏底栏的量法一致。 */
async function measureTargets(page: Page, selector: string): Promise<Reading[]> {
  return page.evaluate((sel) => {
    const MIN = 21;
    const CAP = 64; // 走到 64px 就够：44/2 = 22，留一倍余量看有没有撑过头

    const clamp = (v: number, hi: number) => Math.min(Math.max(v, 0), hi - 1);

    return [...document.querySelectorAll<HTMLElement>(sel)]
      .filter((el) => el.checkVisibility?.({ checkVisibilityCSS: true }) ?? true)
      // 禁用态本来就不接点击（Vuetify 给 `--disabled` 的按钮 `pointer-events: none`），
      // 量出来的「点不到」是设计如此，不是缺陷。
      .filter((el) => !(el as HTMLButtonElement).disabled && el.getAttribute('aria-disabled') !== 'true')
      .filter((el) => {
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        // 中心不在视口里的先跳过 —— 滚动到它再量。
        const cy = r.top + r.height / 2;
        const cx = r.left + r.width / 2;
        return cy >= 0 && cy < innerHeight && cx >= 0 && cx < innerWidth;
      })
      .map((el) => {
        const r = el.getBoundingClientRect();
        const cx = Math.round(r.left + r.width / 2);
        const cy = Math.round(r.top + r.height / 2);
        const owns = (dx: number, dy: number) => {
          const hit = document.elementFromPoint(clamp(cx + dx, innerWidth), clamp(cy + dy, innerHeight));
          return !!hit && (hit === el || el.contains(hit));
        };
        // 一步 1px 往外走，走到不再认这颗为止。
        const reach = (dx: number, dy: number) => {
          let d = 0;
          while (d < CAP && owns(dx * (d + 1), dy * (d + 1))) d += 1;
          return d;
        };
        const extent = { left: reach(-1, 0), right: reach(1, 0), up: reach(0, -1), down: reach(0, 1) };
        const corners = [
          owns(-MIN, -MIN),
          owns(MIN, -MIN),
          owns(-MIN, MIN),
          owns(MIN, MIN),
        ];
        const name =
          el.dataset.probe ||
          (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '')
            .trim()
            .slice(0, 24) ||
          el.className;

        const misses: string[] = [];
        for (const [dir, value] of Object.entries(extent)) {
          if (value < MIN) {
            misses.push(`${dir} 只到 ${value}px（要 ${MIN}）`);
          }
        }
        if (corners.some((owned) => !owned)) {
          misses.push(`44×44 的角上有 ${corners.filter((o) => !o).length} 个点不认这颗`);
        }
        return {
          name,
          width: r.width,
          height: r.height,
          extent,
          corners,
          misses,
        };
      });
  }, selector);
}

/** 量出来的缺陷，写成能直接读的句子。 */
function tooSmall(readings: Reading[]): string[] {
  return readings.filter((r) => r.misses.length).map((r) => `「${r.name}」${Math.round(r.width)}×${Math.round(r.height)}：${r.misses.join('、')}`);
}

// ---------------------------------------------------------------------------

// 机制本身：`.base-btn` 的每一个 kind × size、纯图标的那几颗、`.tap-target` 的一个
// 样本。挂的是真组件和真样式（本地夹具，只替掉 fetch/WebSocket），只有 vite dev 就能
// 跑 —— 这一层坏了，底下那条真页面上的扫描量到的就都是别的东西。
const TAP_TARGET_HTML =
  '<button class="tap-target" data-probe=".tap-target 样本（20×20）" style="position:relative;width:20px;height:20px" aria-label="点这里"></button>';
const NEGATIVE_HTML = `<button data-probe="${NEGATIVE}" style="width:20px;height:20px" aria-label="阴性对照"></button>`;

const fixture = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body><div id="fixture"></div>
<script type="module">
import { createApp, h } from '/node_modules/.vite/deps/vue.js';
import { VApp } from '/node_modules/.vite/deps/vuetify_components.js';
import BaseButton from '/src/components/base/BaseButton.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n, { setLocale } from '/src/i18n/index.ts';
import '/src/style.css';
import '/src/styles/fonts.css';

setLocale('zh-CN');
localStorage.setItem('user', JSON.stringify({ id: 1, username: 'me', nickname: '我' }));

const KINDS = ['ghost', 'secondary', 'primary', 'danger'];
const SIZES = ['sm', 'md', 'lg'];

// 一颗一行、行高 52px：撑开到 44 之后上下各多出 (44−28)/2 = 8px，52px 的间距足够，
// 上下两颗不会互相盖住 —— 这一份只量「一颗自己够不够大」。
const row = (label, node) =>
  h('div', { style: 'min-height:52px;padding:4px 8px;display:flex;align-items:center' }, [node]);

const cells = [];
for (const size of SIZES) {
  for (const kind of KINDS) {
    cells.push(row(kind + '-' + size, h(BaseButton, {
      kind, size, 'data-probe': 'BaseButton ' + kind + ' ' + size,
    }, () => '文字按钮')));
  }
}
for (const size of SIZES) {
  cells.push(row('icon-' + size, h(BaseButton, {
    kind: 'ghost', size, icon: 'mdi-dots-horizontal',
    'aria-label': '更多', 'data-probe': 'BaseButton icon ' + size,
  })));
}
cells.push(row('tap-target', h('span', { innerHTML: \`${TAP_TARGET_HTML}\` })));
cells.push(row('negative', h('span', { innerHTML: \`${NEGATIVE_HTML}\` })));

createApp({
  render: () => h(VApp, {}, { default: () => h('main', {
    style: 'width:360px;margin:0;padding:0',
  }, cells) }),
}).use(vuetify).use(i18n).mount('#fixture');
</script></body></html>`;

test.describe('44px 点击区：量渲染出来的能点范围', () => {
  // 不显式开触屏，`@media (pointer: coarse)` 就不命中，撑开的那块压根不存在。
  test.use({ hasTouch: true, isMobile: true });

  test('每一颗 BaseButton 和 .tap-target 都撑到 44×44（触屏）', async ({ page }) => {
    // 宽度取手机宽（判据只跟 `pointer: coarse` 有关，跟高度无关）；高度是让十几颗
    // 变体加阴性对照一屏放得下 —— 探针只量中心落在视口里的，排到屏幕外的量不到。
    await page.setViewportSize({ width: 390, height: 1200 });
    await page.route('**/__touch-target-fixture__', (route) =>
      route.fulfill({ contentType: 'text/html', body: fixture }),
    );
    await page.goto('/__touch-target-fixture__', { waitUntil: 'commit' });

    await expect(page.locator('[data-probe]').first()).toBeVisible({ timeout: 30_000 });
    await page.evaluate(() => document.fonts.ready);

    // 前提：这一跑真的是触屏。不是的话下面量到的东西和 44px 无关。
    expect(
      await page.evaluate(() => matchMedia('(pointer: coarse)').matches),
      '这一跑没落到触屏上，撑开的规则不生效，量出来的不算数',
    ).toBe(true);

    const readings = await measureTargets(page, '[data-probe]');
    await test.info().attach('touch-target-fixture', {
      body: JSON.stringify(readings, null, 2),
      contentType: 'application/json',
    });

    const controls = readings.filter((r) => r.name !== NEGATIVE);
    // 12 颗 kind × size 的文字按钮 + 3 颗纯图标 + 1 个 .tap-target 样本。
    expect(controls.length, '控件没量全，探针的选择器或夹具写错了').toBe(16);

    // 撑开的那块真的是承重的：这十颗画出来本身不够 44，全靠 `::before` 撑到 ——
    // 文字按钮 sm 28、md 36（各 4 个 kind），纯图标 sm 40，`.tap-target` 的 20×20。
    // 剩下的：文字 lg 本来就是 44，纯图标 md/lg 是 Vuetify 的 48/56。
    const nativelySmall = controls.filter((r) => Math.min(r.width, r.height) < 44);
    expect(
      nativelySmall.length,
      '画出来就不够 44 的那十颗没量全，这条测不到撑开机制 —— 把 sm/md 和 .tap-target 加回来',
    ).toBeGreaterThanOrEqual(10);

    expect(tooSmall(controls), '画出来的盒子（宽×高），后面是点不到 44 的方向').toEqual([]);

    // 阴性对照：20×20、没有撑开机制，探针必须报出来。
    const negative = readings.find((r) => r.name === NEGATIVE);
    expect(negative, '阴性对照没量到，这一份等于没验探针').toBeTruthy();
    expect(
      negative!.misses.length,
      '探针把一颗 20×20 的裸按钮报成合格 —— 探针坏了，上面那条绿的不算数',
    ).toBeGreaterThan(0);
  });
});

// 反过来那半句：鼠标下**不该**撑开。`.base-btn` 的撑开挂在 `@media (pointer: coarse)`
// 上是有原因的 —— 鼠标没那么粗，撑开只会让并排的两颗互相盖住、点到隔壁去。原先
// `BaseButton.spec.ts` 里有一条用例断言「源码里写了 `@media (pointer: coarse)` 和
// `width: max(100%, 44px)`」，那是钉写法，不是钉行为：少一个花括号、或者后面多一条规则
// 覆盖掉，源码断言照样绿。这里在真浏览器里把同一件事量出来 —— 鼠标下能点的范围就该跟
// 画出来的盒子一样大。
test.describe('44px 点击区：鼠标下不撑开', () => {
  // 故意不写 `hasTouch`：主指针就是 fine，正是要量的那一种。
  test('BaseButton 的能点范围就是画出来的盒子，没有往外多撑', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 1200 });
    await page.route('**/__touch-target-fixture__', (route) =>
      route.fulfill({ contentType: 'text/html', body: fixture }),
    );
    await page.goto('/__touch-target-fixture__', { waitUntil: 'commit' });

    await expect(page.locator('[data-probe]').first()).toBeVisible({ timeout: 30_000 });
    await page.evaluate(() => document.fonts.ready);

    expect(
      await page.evaluate(() => matchMedia('(pointer: coarse)').matches),
      '这一跑落到了触屏上，量不了鼠标那半边',
    ).toBe(false);

    const readings = await measureTargets(page, '[data-probe^="BaseButton"]');
    expect(readings.length, 'BaseButton 没量全，夹具变了').toBe(15);

    // 撑开 44 之后，28 高的文字按钮往上能点到 22px；不撑开就只有一半 14px。
    // 容 1px 给取整。
    const grown = readings.flatMap((r) => {
      const half = { left: Math.ceil(r.width / 2), right: Math.ceil(r.width / 2), up: Math.ceil(r.height / 2), down: Math.ceil(r.height / 2) };
      return (Object.keys(half) as (keyof typeof half)[])
        .filter((dir) => r.extent[dir] > half[dir] + 1)
        .map((dir) => `「${r.name}」${Math.round(r.width)}×${Math.round(r.height)} 往 ${dir} 多能点到 ${r.extent[dir]}px（盒子一半才 ${half[dir]}）`);
    });
    expect(grown, '鼠标下也撑开了 —— 并排的两颗会互相盖住').toEqual([]);
  });
});

// ---------------------------------------------------------------------------

// 真页面上那些调用处。上面那一份证明机制在，这一份证明**每一颗真的按上了它** ——
// 漏一颗 `.tap-target`、某颗被 overflow 裁掉、或者被隔壁盖住，都在这里现形。
// 量的是设计系统承诺了 44px 的那两类元素：`.base-btn`（业务代码写按钮只用它）和
// `.tap-target`。页面上的正文链接不算 —— 行内链接是 WCAG 2.5.5 明写除外的。
const MOBILE_ROUTES = ['/home', '/inbox'] as const;

test.describe('44px 点击区：真页面上的调用处', () => {
  test.use({ hasTouch: true, isMobile: true });

  test('手机外壳六个页面：当屏每一颗 BaseButton / .tap-target 都够 44', async ({ page }) => {
    // 六个路由，每个要等一次渲染（冷编译最长 60s）再按半屏滚动着量一遍，
    // 默认 60s 的预算不够。
    test.setTimeout(6 * 70_000 + 60_000);

    await page.setViewportSize({ width: 1440, height: 900 });
    await apiLogin(page);
    const rows = await openFirstProject(page);
    const projectPath = new URL(page.url()).pathname.match(/^\/projects\/[^/]+/)?.[0];
    expect(projectPath).toBeTruthy();
    await rows.first().click();
    await page.waitForURL(/\/topics\//);
    const topicHref = new URL(page.url()).pathname;

    const paths = [...MOBILE_ROUTES, projectPath!, `${projectPath}/members`, `${projectPath}/routines`, topicHref];
    const selector = '.base-btn, .tap-target';

    for (const path of paths) {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto(path);
      // vite dev 按需 transform，冷编译这一条路由量到过 27 秒（`/routines`），所以
      // 门开到 60 秒 —— 和 playwright.config.ts 给整条用例的 60 秒同一个理由。
      // 不用 `networkidle`：房间里有 websocket，它可能一直不安静下来。
      await expect(page.locator('.v-app-bar').first(), `${path}：应用外壳没出来`).toBeVisible({
        timeout: 60_000,
      });
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))));

      // 页面比一屏长，控件分布在整页上：按半屏一步往下走，每一步量当屏那一批。
      // 同一颗只量一次（中心落进视口的那一步）。
      const seen = new Map<string, Reading>();
      const steps = await page.evaluate(() =>
        Math.max(1, Math.ceil(document.documentElement.scrollHeight / (innerHeight * 0.5))),
      );
      for (let step = 0; step < steps; step += 1) {
        await page.evaluate((n) => scrollTo(0, n * innerHeight * 0.5), step);
        for (const reading of await measureTargets(page, selector)) {
          const key = `${reading.name}|${Math.round(reading.width)}x${Math.round(reading.height)}`;
          if (!seen.has(key)) seen.set(key, reading);
        }
      }
      await page.evaluate(() => scrollTo(0, 0));

      const readings = [...seen.values()];
      await test.info().attach(`touch-targets${path.replace(/\//g, '_')}`, {
        body: JSON.stringify(readings, null, 2),
        contentType: 'application/json',
      });
      expect(readings.length, `${path}：一颗都没量到，选择器或页面结构变了`).toBeGreaterThan(0);
      expect(tooSmall(readings), `${path} 上点不到 44 的控件`).toEqual([]);
    }
  });
});
