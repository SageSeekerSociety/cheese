import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';

// The shell a document's mini-app runs in (frontend/miniapp/), the way the
// platform's browser runs a new version before it is kept: the shell's page
// with the app's Vue component written into it.
//
// What is checked is what 芝士 and a reader rely on: the examples the writing
// guide teaches run; a control hands the app its new value, as a number; an
// app cannot reach the network; code that breaks says so, on the console the
// platform's check reads, and so do a control whose value has nowhere to go
// and a number that fails the app's own check; and the design tokens the guide tells 芝士 to draw
// with exist in both themes.

const GUIDE = fileURLToPath(new URL('../../backend/app/domain/skill_library/doc_apps.md', import.meta.url));
const guide = readFileSync(GUIDE, 'utf8');
const examples = [...guide.matchAll(/^```vue\n([\s\S]*?)^```$/gm)].map((m) => m[1]);
// The design tokens the guide offers, by name: `--ink`, `--chart-1` 到 `--chart-8`.
const tokens = [
  ...new Set(
    [...guide.matchAll(/`(?:var\()?--([a-z0-9-]+)\)?`(?: 到 `--chart-(\d)`)?/g)].flatMap(([, name, last]) =>
      last ? Array.from({ length: Number(last) }, (_, i) => `chart-${i + 1}`) : [name]
    )
  ),
];

async function runApp(page: Page, code: string, theme = 'light'): Promise<string[]> {
  const shell = await (await page.request.get('/miniapp/v1/index.html')).text();
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  const boot = JSON.stringify({ code, theme, mode: 'shot' }).replace(/<\//g, '<\\/');
  const at = shell.indexOf('<head>') + '<head>'.length;
  await page.setContent(`${shell.slice(0, at)}<script>window.__CHEESE_APP__=${boot}</script>${shell.slice(at)}`);
  return errors;
}

test('the examples in the writing guide run in the shell', async ({ page }) => {
  expect(examples.length).toBeGreaterThan(0);
  for (const code of examples) {
    const errors = await runApp(page, code);
    // What an app draws may sit inside a component's own shadow tree, so it
    // is judged by the room it takes, not by text in the page.
    await expect.poll(async () => (await page.locator('#cheese-app').boundingBox())?.height ?? 0).toBeGreaterThan(40);
    expect(errors).toEqual([]);
  }
});

test('every design token the writing guide offers has a value, light and dark', async ({ page }) => {
  expect(tokens).toContain('chart-8');
  for (const theme of ['light', 'dark']) {
    await runApp(page, '<template><p>x</p></template>', theme);
    await expect(page.locator('#cheese-app')).not.toBeEmpty();
    const missing = await page.evaluate(
      (names) => names.filter((n) => !getComputedStyle(document.documentElement).getPropertyValue(`--${n}`).trim()),
      tokens
    );
    expect(missing, theme).toEqual([]);
  }
});

test('a control hands the app its new value, as a number', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
import { ref } from 'vue'
const n = ref(5)
</script>
<template>
  <c-slider label="并发数" :min="1" :max="16" v-model="n" />
  <p id="out">{{ typeof n }} {{ n }}</p>
</template>`
  );
  await expect(page.locator('#out')).toHaveText('number 5');
  await page.locator('c-slider input[type=range]').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#out')).toHaveText('number 6');
  expect(errors).toEqual([]);
});

test('a formula wider than a phone stays inside the column and can be scrolled to its end', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 });
  const errors = await runApp(
    page,
    `<template>
  <c-math display tex="T(n) = 99 \\min(n, 10) + 79 \\min(\\max(n - 10, 0), 40) + 59 \\max(n - 50, 0) + 12 \\times 0.85 \\times T(n)" />
</template>`
  );
  const formula = page.locator('c-math .katex-display').first();
  await expect(formula).toBeVisible();
  const whole = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, width: window.innerWidth }));
  expect(whole.scroll).toBeLessThanOrEqual(whole.width);
  const box = await page.locator('c-math').evaluate((el) => {
    el.scrollLeft = el.scrollWidth;
    const end = el.querySelector('.katex-display')!.getBoundingClientRect().right;
    return { end, right: el.getBoundingClientRect().right, scrolls: el.scrollWidth > el.clientWidth };
  });
  expect(box.scrolls).toBe(true);
  expect(box.end).toBeLessThanOrEqual(box.right + 1);
  expect(errors).toEqual([]);
});

/** The middle of a dot the reader would grab, inside a plane's own picture. */
async function dotCentre(page: Page, nth: number) {
  const box = await page.locator('c-plane .point.movable .dot').nth(nth).boundingBox();
  return { x: box!.x + box!.width / 2, y: box!.y + box!.height / 2 };
}

test('a point dragged on a plane hands the app its place in the plane’s own numbers, inside the plane and on its grid', async ({
  page,
}) => {
  const errors = await runApp(
    page,
    `<script setup>
import { ref } from 'vue'
const p = ref([2, 2])
</script>
<template>
  <c-plane :x="[0, 10]" :y="[0, 5]" :height="240"><c-point v-model="p" drag="xy" :snap="0.5" /></c-plane>
  <p id="out">{{ p.join(' ') }}</p>
</template>`
  );
  await expect(page.locator('c-plane .point.movable')).toHaveCount(1);
  const at = await dotCentre(page, 0);
  await page.mouse.move(at.x, at.y);
  await page.mouse.down();
  await page.mouse.move(at.x + 2000, at.y - 2000, { steps: 5 });
  await page.mouse.up();
  await expect(page.locator('#out')).toHaveText('10 5');

  await page.locator('c-plane .point.movable').focus();
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowDown');
  await expect(page.locator('#out')).toHaveText('9.5 4.5');
  expect(errors).toEqual([]);
});

test('an app that hands the plane a new function on every render still answers the reader', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
import { ref } from 'vue'
const p = ref([1, 1])
</script>
<template>
  <c-plane :x="[0, 4]" :y="[0, 4]">
    <c-curve :f="(x) => x * p[1] / 4" />
    <c-point v-model="p" drag="xy" :snap="1" />
  </c-plane>
  <p id="out">{{ p.join(' ') }}</p>
</template>`
  );
  await page.locator('c-plane .point.movable').focus();
  await page.keyboard.press('ArrowUp');
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#out')).toHaveText('2 2');
  expect(errors).toEqual([]);
});

test('a piece dropped on a zone is there, in the order it was dropped; a full one-piece zone swaps', async ({
  page,
}) => {
  const errors = await runApp(
    page,
    `<script setup>
import { ref } from 'vue'
const at = ref({ tray: ['a', 'b', 'c'], slot: [], list: ['x', 'y'] })
</script>
<template>
  <c-board v-model="at">
    <c-zone name="tray"><c-piece v-for="p in at.tray" :key="p" :name="p">{{ p }}</c-piece></c-zone>
    <c-zone name="slot" :max="1"><c-piece v-for="p in at.slot" :key="p" :name="p">{{ p }}</c-piece></c-zone>
    <c-zone name="list"><c-piece v-for="p in at.list" :key="p" :name="p">{{ p }}</c-piece></c-zone>
  </c-board>
  <p id="out">{{ JSON.stringify(at) }}</p>
</template>`
  );
  const out = async () => JSON.parse((await page.locator('#out').textContent())!);

  // Dragged, and let go on the empty one-piece zone.
  const a = (await page.locator('c-piece[name="a"]').boundingBox())!;
  const slot = (await page.locator('c-zone[name="slot"]').boundingBox())!;
  await page.mouse.move(a.x + 5, a.y + 5);
  await page.mouse.down();
  await page.mouse.move(slot.x + slot.width / 2, slot.y + slot.height / 2, { steps: 8 });
  await page.mouse.up();
  await expect.poll(out).toMatchObject({ tray: ['b', 'c'], slot: ['a'] });

  // Picked and placed: dropped on the full zone, it swaps with what is there.
  await page.locator('c-piece[name="b"]').click();
  await page.locator('c-zone[name="slot"]').click();
  await expect.poll(out).toMatchObject({ tray: ['a', 'c'], slot: ['b'] });

  // From the keyboard, onto a piece in a list: it goes in before that piece.
  await page.locator('c-piece[name="c"]').focus();
  await page.keyboard.press('Enter');
  await page.locator('c-piece[name="y"]').focus();
  await page.keyboard.press('Enter');
  await expect.poll(out).toMatchObject({ tray: ['a'], list: ['x', 'c', 'y'] });
  expect(errors).toEqual([]);
});

test('a number in a sentence is dragged and stepped within its range', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
import { ref } from 'vue'
const n = ref(3)
</script>
<template>
  <p>每周 <c-scrub v-model="n" :min="1" :max="7" unit=" 次" />，<span id="out">{{ typeof n }} {{ n }}</span></p>
</template>`
  );
  const box = (await page.locator('c-scrub').boundingBox())!;
  await page.mouse.move(box.x + 4, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 600, box.y + box.height / 2, { steps: 6 });
  await page.mouse.up();
  await expect(page.locator('#out')).toHaveText('number 7');
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#out')).toHaveText('number 6');
  expect(errors).toEqual([]);
});

test('an app cannot reach the network', async ({ page }) => {
  await runApp(
    page,
    `<script setup>
import { ref, onMounted } from 'vue'
const said = ref('waiting')
onMounted(() => fetch('https://example.com/').then(() => (said.value = 'reached'), () => (said.value = 'blocked')))
</script>
<template><p id="out">{{ said }}</p></template>`
  );
  await expect(page.locator('#out')).toHaveText('blocked');
});

test('code that breaks says so where the check reads it', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
const rows = undefined
</script>
<template><p>{{ rows.length }}</p></template>`
  );
  await expect.poll(() => errors.join('\n')).toContain('length');
});

test('a control the reader moves but whose value cannot change is refused before it runs', async ({ page }) => {
  const wired = (template: string) => `<script setup>
import { ref, computed } from 'vue'
const x = ref(1)
const point = computed(() => [x.value, x.value ** 2])
const anywhere = computed({ get: () => [x.value, x.value ** 2], set: ([nx]) => (x.value = nx) })
const K = 3
</script>
<template><c-plane :x="[-3, 3]" :y="[0, 9]">${template}</c-plane><p>x</p></template>`;
  for (const broken of [
    '<c-point v-model="point" drag="x" />',
    '<c-point :v-model="[x, x * x]" />',
    '</c-plane><c-slider v-model="K" :min="0" :max="9" /><c-plane>',
    '<c-point :value="point" drag="x" />',
  ]) {
    const errors = await runApp(page, wired(broken));
    await expect.poll(() => errors.length, broken).toBeGreaterThan(0);
  }
  for (const fine of [
    '<c-point v-model="anywhere" drag="x" />',
    '<c-point :value="point" drag="x" @input="x = $event.target.value[0]" />',
    '<c-point v-model="point" />',
  ]) {
    const errors = await runApp(page, wired(fine));
    await expect(page.locator('#cheese-app')).not.toBeEmpty();
    expect(errors, fine).toEqual([]);
  }
});

test('a number that fails the app’s own check is refused; one that passes is not', async ({ page }) => {
  const app = (expected: number) => `<script setup>
const price = (tax) => 24 + tax / 2
console.assert(price(0) === ${expected}, '不征税时均衡价应是 ${expected}')
</script>
<template><p>{{ price(0) }}</p></template>`;
  const failed = await runApp(page, app(56));
  await expect.poll(() => failed.join('\n')).toContain('56');
  const passed = await runApp(page, app(24));
  await expect(page.locator('#cheese-app')).toHaveText('24');
  expect(passed).toEqual([]);
});
