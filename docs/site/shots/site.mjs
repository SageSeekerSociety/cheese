// Screenshots of the docs site itself, for its own home page: the top of each
// page as a reader first sees it. Run after a build, against that build served
// under /docs (DOCS, default http://localhost:5500/docs):
//
//   OUT=/tmp/site npm run build && node gen/serve.mjs /tmp/site
//   node shots/site.mjs
//
// Needs a Playwright browser, like shots.mjs (BROWSER_WS for a browser server).
// Writes docs/manual/public/images/site/<slug>.jpg (desktop) and m-<slug>.jpg
// (phone, only for the pages the home page's tour shows); dev/<slug> becomes
// dev-<slug>.jpg.
import { chromium } from '@playwright/test'
import { mkdirSync, readdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const DOCS = process.env.DOCS || 'http://localhost:5500/docs'
const OUT = join(HERE, '../../manual/public/images/site')
mkdirSync(OUT, { recursive: true })

// Every page in the manual, plus the pages the tour visits on phones too.
const pages = readdirSync(join(HERE, '../../manual')).filter((f) => f.endsWith('.md') && f !== 'README.md' && f !== 'index.md').map((f) => f.slice(0, -3))
const TOUR = ['quickstart', 'student-tutorial', 'accept', 'troubleshooting', 'changelog', 'dev/turn']
const shots = [...new Set([...pages, ...TOUR])].map((s) => [s, 'd']).concat(TOUR.map((s) => [s, 'm']))

const browser = process.env.BROWSER_WS ? await chromium.connect(process.env.BROWSER_WS) : await chromium.launch()
const ctx = {
  d: await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, locale: 'zh-CN', colorScheme: 'light' }),
  m: await browser.newContext({ viewport: { width: 390, height: 760 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: 'zh-CN', colorScheme: 'light' }),
}
for (const [slug, kind] of shots) {
  const page = await ctx[kind].newPage()
  const r = await page.goto(`${DOCS}/${slug}`, { waitUntil: 'networkidle' })
  if (!r || r.status() !== 200) { console.log('skip', slug, r?.status()); await page.close(); continue }
  await page.addStyleTag({ content: '.progress,.scroll-progress{display:none!important}*{animation-delay:-10s!important;transition:none!important}' })
  await page.waitForTimeout(700)
  const name = `${kind === 'm' ? 'm-' : ''}${slug.replace('/', '-')}.jpg`
  await page.screenshot({ path: join(OUT, name), type: 'jpeg', quality: 80 })
  console.log(name)
  await page.close()
}
await browser.close()
