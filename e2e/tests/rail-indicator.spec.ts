import { expect, test, type Locator } from '@playwright/test';

// Mount the real rail, its scroll container and both product theme pipelines.
// A black monitor bezel must not be the background of the current-page mark.
const fixture = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"></head><body>
<div id="fixture"></div><script type="module">
import { createApp, h } from '/node_modules/.vite/deps/vue.js';
import { createMemoryHistory, createRouter } from '/node_modules/.vite/deps/vue-router.js';
import { VApp } from '/node_modules/.vite/deps/vuetify_components_VApp.js';
import LeftAppRail from '/src/components/common/Navigation/LeftAppRail.vue';
import vuetify from '/src/plugins/vuetify.ts';
import i18n from '/src/i18n/index.ts';
import '/src/style.css';
const router = createRouter({ history: createMemoryHistory(), routes: [
  { path: '/:pathMatch(.*)*', component: { render: () => null } },
] });
await router.push('/projects/current');
const items = [
  { type: 'item', key: 'current', title: '当前项目', projectId: 'current', img: '', to: '/projects/current', match: path => path.startsWith('/projects/current'), badge: 3 },
  { type: 'item', key: 'other', title: '另一个项目', projectId: 'other', img: '', to: '/projects/other', badge: 128 },
  { type: 'item', key: 'unread', title: '未读项目', projectId: 'unread', img: '', to: '/projects/unread', dot: true },
  { type: 'item', key: 'add', title: '新建项目', add: true, icon: 'mdi-plus', action: () => {} },
];
createApp({ render: () => h(VApp, {}, { default: () => h(LeftAppRail, { items }) }) })
  .use(vuetify).use(i18n).use(router).mount('#fixture');
window.setRailTheme = name => {
  document.documentElement.dataset.theme = name;
  vuetify.theme.change(name);
};
window.setRailTheme('light');
</script></body></html>`;

async function indicator(tile: Locator) {
  return tile.evaluate(el => {
    const box = el.getBoundingClientRect();
    const mark = getComputedStyle(el, '::before');
    const rail = el.closest('.v-navigation-drawer')!.getBoundingClientRect();
    return {
      x: box.x + parseFloat(mark.left),
      right: box.right,
      width: parseFloat(mark.width),
      height: parseFloat(mark.height),
      railRight: rail.right,
      color: mark.backgroundColor,
      themeColor: `rgb(${getComputedStyle(el).getPropertyValue('--v-theme-on-surface').trim().replace(/,\s*/g, ', ')})`,
    };
  });
}

test('current project is marked on the inner side in both themes without changing hover or keyboard focus', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.route('**/__rail-indicator-fixture__', route => route.fulfill({ contentType: 'text/html', body: fixture }));
  // This component fixture has no account and must not contact a live backend.
  await page.route(url => url.pathname.startsWith('/api/'), route => route.fulfill({ json: { code: 200, data: [] } }));
  await page.goto('/__rail-indicator-fixture__', { waitUntil: 'commit' });
  const current = page.locator('[data-project-id="current"]');
  const other = page.locator('[data-project-id="other"]');
  await expect(current).toHaveAttribute('aria-current', 'page', { timeout: 30_000 });
  const colors: string[] = [];
  for (const theme of ['light', 'dark']) {
    await page.evaluate(name => (window as unknown as { setRailTheme: (name: string) => void }).setRailTheme(name), theme);
    await page.mouse.move(600, 500);
    await expect.poll(async () => (await indicator(current)).height).toBe(36);
    const mark = await indicator(current);
    // Observe geometry, not whether the implementation says left or right.
    expect(mark.x).toBeGreaterThan(mark.right);
    expect(mark.x + mark.width).toBeLessThanOrEqual(mark.railRight + 0.5);
    expect(mark.color).toBe(mark.themeColor);
    colors.push(mark.color);
    await expect.poll(async () => (await indicator(other)).height).toBe(0);
    await other.hover();
    await expect.poll(async () => (await indicator(other)).height).toBe(20);
    await expect.poll(async () => (await indicator(current)).height).toBe(36);
    await page.mouse.down();
    expect((await indicator(other)).height).toBe(20);
    // Release away from the link so this is not a navigation test.
    await page.mouse.move(600, 500);
    await page.mouse.up();
    await page.keyboard.press('Tab');
    const focused = page.locator(':focus-visible');
    await expect(focused).toBeVisible();
    const outline = await focused.evaluate(el => getComputedStyle(el).outlineWidth);
    expect(parseFloat(outline)).toBeGreaterThan(0);
    await page.screenshot({ path: testInfo.outputPath(`rail-${theme}.png`) });
    await focused.blur();
  }
  expect(colors[0]).not.toBe(colors[1]);
});
