// Screenshots of the docs site itself, for its own home page: the top of each
// page the 问芝士 tour visits, below the site's header — on a desktop as it
// looks with the 问芝士 panel docked (the tour draws the panel itself).
// Run after a build, against that build served
// under /docs (DOCS, default http://localhost:5500/docs):
//
//   OUT=/tmp/site npm run build && node gen/serve.mjs /tmp/site
//   node shots/site.mjs
//
// Needs a Playwright browser, like shots.mjs (BROWSER_WS for a browser server).
// Keep TOUR in step with the tour in src/home.mjs. Writes
// docs/manual/public/images/site/<slug>.jpg (desktop) and m-<slug>.jpg (phone);
// dev/<slug> becomes dev-<slug>.jpg.
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const DOCS = process.env.DOCS || 'http://localhost:5500/docs'
const OUT = join(HERE, '../../manual/public/images/site')
mkdirSync(OUT, { recursive: true })

const TOUR = ['quickstart', 'student-tutorial', 'accept', 'troubleshooting', 'changelog', 'dev/turn']
const shots = TOUR.flatMap((s) => [[s, 'd'], [s, 'm']])

const browser = process.env.BROWSER_WS ? await chromium.connect(process.env.BROWSER_WS) : await chromium.launch()
const ctx = {
  d: await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.25, locale: 'zh-CN', colorScheme: 'light' }),
  m: await browser.newContext({ viewport: { width: 390, height: 760 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN', colorScheme: 'light' }),
}
for (const [slug, kind] of shots) {
  const page = await ctx[kind].newPage()
  const r = await page.goto(`${DOCS}/${slug}`, { waitUntil: 'networkidle' })
  if (!r || r.status() !== 200) { console.log('skip', slug, r?.status()); await page.close(); continue }
  await page.addStyleTag({ content: '.progress,.scroll-progress{display:none!important}*{animation-delay:-10s!important;transition:none!important}' })
  if (kind === 'd') await page.evaluate(() => document.body.classList.add('docked'))
  await page.waitForTimeout(900)
  const hdr = await page.evaluate(() => document.querySelector('#hdr').getBoundingClientRect().bottom)
  const vp = page.viewportSize(), dock = kind === 'd' ? 420 : 0
  const clip = { x: 0, y: Math.round(hdr), width: vp.width - dock, height: vp.height - Math.round(hdr) }
  const name = `${kind === 'm' ? 'm-' : ''}${slug.replace('/', '-')}.jpg`
  await page.screenshot({ path: join(OUT, name), type: 'jpeg', quality: 80, clip })
  console.log(name)
  await page.close()
}
await browser.close()
