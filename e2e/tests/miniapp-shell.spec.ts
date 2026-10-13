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
// platform's check reads; and the design tokens the guide tells 芝士 to draw
// with exist in both themes.

const GUIDE = fileURLToPath(new URL('../../backend/app/domain/agent/skill_library/doc_apps.md', import.meta.url));
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
    await expect(page.locator('#cheese-app')).not.toBeEmpty();
    expect((await page.locator('#cheese-app').boundingBox())?.height ?? 0).toBeGreaterThan(40);
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
