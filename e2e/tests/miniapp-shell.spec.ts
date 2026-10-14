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
  await page.locator('c-slider [role=slider]').focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#out')).toHaveText('number 6');
  expect(errors).toEqual([]);
});

test('a slider shows the value the app sets to its step, not the arithmetic behind it', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
import { ref } from 'vue'
const t = ref(0.1 + 0.2)
</script>
<template><c-slider label="时刻" :min="0" :max="1" :step="0.02" unit=" ms" v-model="t" /></template>`
  );
  await expect(page.locator('c-slider output')).toHaveText('0.3 ms');
  expect(errors).toEqual([]);
});

test('a control stacked in a column is as tall as itself, and in a row still shares the width', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
import { ref } from 'vue'
const a = ref(3)
const b = ref(4)
</script>
<template>
  <div style="display: flex; flex-direction: column; gap: 8px">
    <c-slider id="stacked" label="甲" :min="0" :max="10" v-model="a" />
    <c-stat id="card" label="合计" :value="a + b" />
  </div>
  <c-row><c-slider id="left" label="甲" :min="0" :max="10" v-model="a" /><c-slider id="right" label="乙方每轮投入的资源数量" :min="0" :max="10" v-model="b" /></c-row>
</template>`
  );
  const box = async (id: string) => (await page.locator(`#cheese-app [id=${id}]`).first().boundingBox())!;
  expect((await box('stacked')).height).toBeLessThan(90);
  expect((await box('card')).height).toBeLessThan(120);
  const [left, right] = [await box('left'), await box('right')];
  expect(Math.abs(left.width - right.width)).toBeLessThan(2);
  expect(left.width + right.width).toBeGreaterThan(500);
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

test('a plane asked for equal scales draws a unit square square, with every asked-for range still in view', async ({
  page,
}) => {
  const errors = await runApp(
    page,
    `<script setup>
import { ref } from 'vue'
const a = ref([-4, 4])
const b = ref([4, -4])
</script>
<template>
  <c-plane :x="[-4, 4]" :y="[-4, 4]" :height="240" equal>
    <c-area :points="[[0, 0], [1, 0], [1, 1], [0, 1]]" />
    <c-point v-model="a" drag="xy" /><c-point v-model="b" drag="xy" />
  </c-plane>
</template>`
  );
  const square = (await page.locator('c-plane path.area').boundingBox())!;
  expect(Math.abs(square.width - square.height)).toBeLessThan(1.5);
  const plane = (await page.locator('c-plane svg').first().boundingBox())!;
  for (const i of [0, 1]) {
    const dot = await dotCentre(page, i);
    expect(dot.x).toBeGreaterThan(plane.x);
    expect(dot.x).toBeLessThan(plane.x + plane.width);
    expect(dot.y).toBeGreaterThan(plane.y);
    expect(dot.y).toBeLessThan(plane.y + plane.height);
  }
  expect(errors).toEqual([]);
});

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
    '</c-plane><c-zone name="a"><c-piece name="x">x</c-piece></c-zone><c-plane>',
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

test('a value from @platform/motion slides to a one-off change, and is there at once in the check’s pictures', async ({ page }) => {
  const app = `<script setup>
import { ref } from 'vue'
import { tween } from '@platform/motion'
const n = ref(0)
const shown = tween(n)
</script>
<template><button id="go" @click="n = 100">go</button><p id="out">{{ Math.round(shown) }}</p></template>`;
  const errors = await runApp(page, app);
  await page.locator('#go').click();
  await expect(page.locator('#out')).toHaveText('100');
  await runApp(page, app);
  await page.evaluate(() => (document.documentElement.dataset.mode = 'inline'));
  const seen = await page.evaluate(async () => {
    (document.getElementById('go') as HTMLButtonElement).click();
    const values: number[] = [];
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => requestAnimationFrame(r));
      values.push(Number(document.getElementById('out')!.textContent));
    }
    return values;
  });
  expect(seen.some((v) => v > 0 && v < 100)).toBe(true);
  expect(seen.at(-1)).toBe(100);
  expect(errors).toEqual([]);
});

test('random numbers from one seed repeat; from another they differ', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
import { random } from '@platform/random'
const draw = (seed) => { const r = random(seed); return [r.next(), r.normal(), r.int(1, 6), r.poisson(3)].join(' ') }
</script>
<template><p id="a">{{ draw(7) }}</p><p id="b">{{ draw(7) }}</p><p id="c">{{ draw(8) }}</p></template>`
  );
  const [a, b, c] = await Promise.all(['#a', '#b', '#c'].map((s) => page.locator(s).textContent()));
  expect(a).toBe(b);
  expect(a).not.toBe(c);
  expect(errors).toEqual([]);
});

test('a simulation opens at its start, stops at its end, and plays again from the start', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
import { simulate } from '@platform/sim'
const sim = simulate({ init: () => ({ n: 0 }), step: (s) => { s.n += 1; return s }, dt: 0.5, speed: 5, end: 3 })
</script>
<template>
  <p id="out">{{ sim.steps }} {{ sim.t }} {{ sim.state.n }} {{ sim.done ? 'done' : sim.running ? 'running' : 'paused' }}</p>
  <button id="on" @click="sim.advance(3)">on</button><button id="play" @click="sim.play()">play</button>
  <button id="back" @click="sim.reset()">back</button>
</template>`
  );
  await expect(page.locator('#out')).toHaveText('0 0 0 paused');
  await page.locator('#on').click();
  await expect(page.locator('#out')).toHaveText('3 1.5 3 paused');
  await page.locator('#on').click();
  await expect(page.locator('#out')).toHaveText('6 3 6 done');
  await page.locator('#play').click();
  await expect(page.locator('#out')).toHaveText(/^[0-5] .* running$/);
  await expect(page.locator('#out')).toHaveText('6 3 6 done');
  await page.locator('#back').click();
  await expect(page.locator('#out')).toHaveText('0 0 0 paused');
  expect(errors).toEqual([]);
});

test('a colour read in script follows the document from light to dark', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
import { computed } from 'vue'
import { color } from '@platform/theme'
const ink = computed(() => color('chart-1'))
</script>
<template><p id="out">{{ ink }}</p></template>`
  );
  const light = await page.locator('#out').textContent();
  expect(light).toBeTruthy();
  await page.evaluate(() => window.postMessage({ ch: 'cheese-app', type: 'theme', theme: 'dark' }, '*'));
  await expect(page.locator('#out')).not.toHaveText(light!);
  expect(errors).toEqual([]);
});

test('a card the layout moves slides to its new place, and is there at once in the check’s pictures', async ({ page }) => {
  const app = `<script setup>
import { ref } from 'vue'
import { vLayout } from '@platform/motion'
const span = ref(4)
</script>
<template>
  <button id="go" @click="span = 10">go</button>
  <div v-layout style="display: grid; grid-template-columns: repeat(12, 1fr); gap: 4px; width: 600px">
    <div :style="{ gridColumn: 'span ' + span, height: '40px' }">A</div>
    <div id="b" style="grid-column: span 4; height: 40px">B</div>
  </div>
</template>`;
  const errors = await runApp(page, app);
  const moving = () =>
    page.evaluate(() => document.getElementById('b')!.getAnimations().length > 0);
  await page.locator('#go').click();
  expect(await moving()).toBe(false);
  await runApp(page, app);
  await page.evaluate(() => (document.documentElement.dataset.mode = 'inline'));
  await page.locator('#go').click();
  expect(await moving()).toBe(true);
  await expect.poll(moving).toBe(false);
  expect(errors).toEqual([]);
});

test('a slider follows the finger, settles on a step, and its thumb sits on its track', async ({ page }) => {
  const errors = await runApp(
    page,
    `<script setup>
import { ref } from 'vue'
const n = ref(6)
</script>
<template><c-slider label="栏数" :min="1" :max="12" v-model="n" /><p id="out">{{ n }}</p></template>`
  );
  const area = page.locator('c-slider [role=slider]');
  const box = (await area.boundingBox())!;
  const centre = async (sel: string) => {
    const b = (await page.locator(`c-slider ${sel}`).boundingBox())!;
    return b.y + b.height / 2;
  };
  expect(Math.abs((await centre('.thumb')) - (await centre('.track')))).toBeLessThan(1);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2, { steps: 8 });
  await expect(page.locator('#out')).toHaveText('12');
  await page.mouse.up();
  await page.keyboard.press('Home');
  await expect(page.locator('#out')).toHaveText('1');
  expect(errors).toEqual([]);
});
