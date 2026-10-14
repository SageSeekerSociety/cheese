import { test, expect, type Page } from '@playwright/test'
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import {
  PAGES,
  THEMES,
  collectRuns,
  compare,
  entryFor,
  floorFor,
  formatContrastReport,
  tightenedBaseline,
  worstOf,
} from '../../frontend/scripts/contrast-ratchet-core.mjs'

// The rendered half of `fe-contrast`: what a reader of the public pages
// actually gets, painted. The rule, the ratchet and the baseline live in
// frontend/ (scripts/contrast-ratchet-core.mjs, contrast-baseline.json) beside
// the other frontend ratchets; this is the driver, because the measurement
// needs a real browser and Playwright lives here.
//
// What it covers, and what it deliberately does not: the three public pages a
// visitor can reach, in both themes, at 1440×900. `/` and `/about` are the same
// document for a visitor (`/` is where the site's links point), so the landing
// is measured once. The signed-in app is not covered — Playwright signs in as
// alice and every spec shares that backend, but the app surface is large enough
// that freezing it would freeze more than anyone has looked at. Public pages
// are where #1328 and #3691 both went wrong.
//
// Animations are off (`reducedMotion: 'reduce'`), and not only for speed: the
// landing fades `.manifesto-clause` from `--line-2` to `--ink` over 700ms, and
// a measurement taken mid-fade reports text at 1.21:1 that no reader ever sees.
// Under reduced motion the landing's own stylesheet resolves the clause to
// `--ink` immediately, so the measured value is the settled one.

const BASELINE = fileURLToPath(new URL('../../frontend/contrast-baseline.json', import.meta.url))
const UPDATE = process.env.CONTRAST_UPDATE === '1'
const FREEZE_NEW = process.env.CONTRAST_FREEZE_NEW === '1'

const readBaseline = () => {
  try {
    return JSON.parse(readFileSync(BASELINE, 'utf8')).runs ?? {}
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {}
    throw error
  }
}

/**
 * A floor on how much each page has to produce, so that "the page rendered
 * nothing measurable" cannot pass as "nothing is wrong". Each is about half of
 * what the page actually produced when the baseline was frozen — `/solutions`
 * is the longest and `/download` is a short page with a heading and a few
 * buttons — and between them they catch the failure modes that make a walk
 * return almost nothing: a page that did not load, a redirect to sign-in, a
 * removed section, or a browser with no font able to lay out text at all. The
 * last of those is not hypothetical: it is what this check found on the machine
 * it was written on, where 86 of the landing's 94 text nodes measured 0×0 and
 * the walk quietly returned a handful of runs. The one failure this count
 * cannot see — the app stylesheet missing, which leaves every run at the
 * browser default and therefore at 21:1 — is caught by the token check in
 * `measure()` instead.
 */
const MIN_RUNS: Record<string, number> = { '/': 50, '/solutions': 60, '/download': 8 }

/**
 * Open a page in one theme and leave it settled: loaded, mounted, and with its
 * stylesheet confirmed to have arrived.
 */
async function open(page: Page, path: string, theme: string) {
  await page.emulateMedia({ colorScheme: theme === 'dark' ? 'dark' : 'light', reducedMotion: 'reduce' })
  await page.addInitScript((value) => {
    localStorage.setItem('cheesex.theme', value)
  }, theme)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(path, { waitUntil: 'load' })
  // The visitor shell's own bar is the first thing the app renders, and it is
  // on all three pages. `body` is visible from the first byte of index.html and
  // would let the walk run against a page the app has not mounted yet — which
  // is how this measured eight runs on a page that has a hundred.
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  // The app mounting does not mean its stylesheet arrived: index.html loads the
  // CSS as its own render-blocking <link>, and the module mounts either way. With
  // no stylesheet every run falls back to the browser's black on white — 21:1,
  // which is greener than the styled page — and a walk over it would empty the
  // baseline. One design token is enough to tell the two apart, so this is the
  // check that the count guard cannot be.
  const muted = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--muted').trim(),
  )
  expect(muted, 'the app stylesheet did not load — every colour would measure as the browser default').not.toBe('')
}

/**
 * Walk the page as it now stands and return the runs it painted. Split from
 * `open` so a test can change the page (a planted style tag survives) without
 * navigating, which would throw the change away.
 */
async function collect(page: Page, path: string, theme: string) {
  // Walk the document once so anything revealed on scroll has been revealed,
  // then come back to the top where the page settles.
  await page.evaluate(async () => {
    for (let y = 0; y < document.body.scrollHeight; y += 700) {
      window.scrollTo(0, y)
      await new Promise((resolve) => requestAnimationFrame(resolve))
    }
    window.scrollTo(0, 0)
    await document.fonts.ready
    await new Promise((resolve) => requestAnimationFrame(resolve))
  })
  const runs = await page.evaluate(collectRuns)
  const stamped = runs.map((run: object) => ({ ...run, path, theme }))
  expect(
    stamped.length,
    `${path} [${theme}] produced ${stamped.length} rendered text runs — too few to be the page; ` +
      'see MIN_RUNS in this spec for what that usually means.',
  ).toBeGreaterThanOrEqual(MIN_RUNS[path])
  // The floor the driver applies in the page and floorFor() above are the same
  // rule written twice — the page cannot import it. This is where a change to
  // one that is not mirrored in the other fails, instead of silently
  // misjudging every run.
  for (const run of stamped) {
    expect(run.need, `${run.path} [${run.theme}] ${run.selector}`).toBe(
      floorFor({ size: run.size, weight: run.weight, icon: run.icon }),
    )
  }
  return stamped
}

const measure = async (page: Page, path: string, theme: string) => {
  await open(page, path, theme)
  return collect(page, path, theme)
}

test('the public pages render every text run above its contrast floor', async ({ page }) => {
  test.slow()
  const runs = []
  for (const theme of THEMES) {
    for (const path of PAGES) runs.push(...(await measure(page, path, theme)))
  }
  const worst = worstOf(runs)
  if (UPDATE) {
    const next = tightenedBaseline(readBaseline(), worst, { freezeNew: FREEZE_NEW })
    writeFileSync(BASELINE, `${JSON.stringify(baselineFile(next), null, 2)}\n`)
    console.log(
      `${Object.keys(next).length} frozen run(s) written to ${BASELINE}` +
        (FREEZE_NEW ? '' : ' (new runs not added — set CONTRAST_FREEZE_NEW=1 to accept them as debt)'),
    )
    return
  }
  const comparison = compare(readBaseline(), worst)
  expect(comparison.ok, `\n${formatContrastReport(comparison)}\n`).toBe(true)
})

test('a run planted below its floor goes red, and freezing it turns the page green', async ({ page }) => {
  // The plant the unit tests cannot be: they exercise the arithmetic on runs
  // they construct themselves, and the one thing that would make this gate
  // useless is the walk itself. So this plants the mistake #3691 fixed — text
  // written in `--faint` — in the real DOM, walks the real page, and requires
  // the same compare() the gate runs to call it a regression.
  const runs = await measure(page, '/', 'light')
  expect(compare(readBaseline(), worstOf(runs)).ok, 'the page as it stands passes').toBe(true)

  // The visitor bar's links, in `--faint`. No navigation after this: `open()`
  // would throw the planted stylesheet away.
  await page.addStyleTag({ content: '.site-nav a { color: var(--faint) !important; }' })
  const planted = await collect(page, '/', 'light')
  const comparison = compare(readBaseline(), worstOf(planted))
  expect(comparison.ok, `\n${formatContrastReport(comparison)}\n`).toBe(false)
  // Named, not just counted: a plant that stopped hitting the links but darkened
  // something else on the way past would still leave a non-empty `added` list.
  expect(comparison.added.map(({ key }: { key: string }) => key)).toEqual([
    '/|light|a.router-link-active.router-link-exact-active|',
    '/|light|a|',
  ])
  // The way out is a commit that says so, not a flag on the check.
  const frozen = tightenedBaseline(readBaseline(), worstOf(planted), { freezeNew: true })
  expect(compare(frozen, worstOf(planted)).ok, 'and the way out is to freeze it on purpose').toBe(true)
})

/** The baseline file as it is written, matching the other ratchets' shape. */
function baselineFile(runs: Record<string, object>) {
  return {
    _comment:
      'Frozen rendered-contrast debt on the public pages, one entry per text run that is below ' +
      'its WCAG floor (4.5:1, or 3:1 for large text and icon glyphs) or that the walker cannot ' +
      'measure at all — a `ratio` of null with a reason means a background image or gradient ' +
      'sits between the run and its ground, and those are frozen too rather than skipped. The ' +
      'ratchet blocks any new or worse run and the baseline only goes down; each entry records ' +
      'the worst instance of that element, its floor, and a sample of its text. Regenerate with ' +
      '`CONTRAST_UPDATE=1 pnpm --dir e2e exec playwright test contrast-ratchet`, which will ' +
      'not add a key the baseline has never seen unless CONTRAST_FREEZE_NEW=1 is set as well. ' +
      'The rule and its messages live in frontend/scripts/contrast-ratchet-core.mjs.',
    runs,
  }
}
