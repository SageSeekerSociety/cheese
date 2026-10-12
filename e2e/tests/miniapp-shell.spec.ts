import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';

// The shell a document's mini-app runs in (frontend/miniapp/), the way the
// platform's browser runs a new version before it is kept: the shell's page
// with the app's Vue component written into it.
//
// What is checked is what 芝士 and a reader rely on: the examples the writing
// guide teaches run; a control hands the app its new value, as a number; an
// app cannot reach the network; and code that breaks says so, on the console
// the platform's check reads.

const GUIDE = fileURLToPath(new URL('../../backend/app/domain/agent/skill_library/doc_apps.md', import.meta.url));
const examples = [...readFileSync(GUIDE, 'utf8').matchAll(/^```vue\n([\s\S]*?)^```$/gm)].map((m) => m[1]);

async function runApp(page: Page, code: string): Promise<string[]> {
  const shell = await (await page.request.get('/miniapp/v1/index.html')).text();
  const errors: string[] = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  const boot = JSON.stringify({ code, theme: 'light', mode: 'shot' }).replace(/<\//g, '<\\/');
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
