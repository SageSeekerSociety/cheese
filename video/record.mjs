// 真浏览器录屏：从自动 alice 入口（ws=off）进频道，等首屏任务卡出来后再录 ~9 秒。
import { chromium } from '/tmp/pw/node_modules/playwright/index.mjs';
import fs from 'node:fs';
const [base, side, outdir] = process.argv.slice(2);
fs.mkdirSync(outdir, { recursive: true });
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1280, height: 800 }, reducedMotion: 'no-preference', recordVideo: { dir: outdir, size: { width: 1280, height: 800 } } });
const page = await ctx.newPage();
const t0 = Date.now();
await page.goto(`${base}/__accept?ws=off`);
await page.waitForURL(/\/channels\/|\/topics\//, { timeout: 30000 });
const row = page.locator('.im-row').filter({ hasText: '首屏标记·会话在跑（模拟）' }).first();
await row.waitFor({ timeout: 60000 });
const mark = row.locator('.task-state').first();
await mark.waitFor();
await page.evaluate(() => document.fonts.ready);
const fontOk = await page.evaluate(() => document.fonts.check('24px "Material Design Icons"'));
await page.waitForTimeout(1500);
const shownAt = (Date.now() - t0) / 1000;
const box = await mark.boundingBox();
const icon = row.locator('.task-state__icon').first();
const samples = [];
for (let i = 0; i < 9; i++) {
  samples.push(await icon.evaluate((el) => ({ t: performance.now() | 0, transform: getComputedStyle(el).transform, animation: getComputedStyle(el).animationName, live: el.closest('.task-state')?.hasAttribute('data-live') ?? false, cls: [...el.classList].filter((c) => c.startsWith('mdi-')).join(' ') })));
  if (i === 0) await row.screenshot({ path: `${outdir}/${side}-card.png` });
  await page.waitForTimeout(1000);
}
const endAt = (Date.now() - t0) / 1000;
const video = page.video();
await ctx.close();
const raw = await video.path();
fs.renameSync(raw, `${outdir}/${side}-raw.webm`);
fs.writeFileSync(`${outdir}/${side}-meta.json`, JSON.stringify({ fontOk, base, side, url: page.url(), shownAt, endAt, box, samples, recordedAt: new Date().toISOString() }, null, 1));
console.log(JSON.stringify({ fontOk, shownAt, endAt, box, first: samples[0], transforms: samples.map((s) => s.transform) }));
await b.close();
