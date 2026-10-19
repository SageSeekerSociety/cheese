import { test, expect } from '@playwright/test'
import {
  PAGES,
  THEMES,
  collectRuns,
  contrastRatio,
  formatContrastReport,
  judge,
  roundRatio,
  worstOf,
} from '../../frontend/scripts/contrast-ratchet-core.mjs'
import { collect, measure, open } from '../contrast-walk.mjs'

// The rendered half of `fe-contrast`: what a reader of the public pages
// actually gets, painted. The rule lives in frontend/
// (scripts/contrast-ratchet-core.mjs) beside the other frontend checks; this is
// the gate, because the measurement needs a real browser and Playwright lives
// here. Every run must clear its floor; there is no baseline.
//
// The walk lives in e2e/contrast-walk.mjs. The tests here check its verdict and
// the planted cases that show the walk still measures the intended property.
//
// What it covers, and what it deliberately does not: the three public pages a
// visitor can reach, in both themes, at 1440×900. `/` and `/about` are the same
// document for a visitor (`/` is where the site's links point), so the landing
// is measured once. The signed-in app is not covered — Playwright signs in as
// alice and every spec shares that backend, but the app surface is large enough
// that gating it would gate more than anyone has looked at. Public pages
// are where #1328 and #3691 both went wrong.
//
// Animations are off (`reducedMotion: 'reduce'`), and not only for speed: the
// landing fades `.manifesto-clause` from `--line-2` to `--ink` over 700ms, and
// a measurement taken mid-fade reports text at 1.21:1 that no reader ever sees.
// Under reduced motion the landing's own stylesheet resolves the clause to
// `--ink` immediately, so the measured value is the settled one.

test('the public pages render every text run above its contrast floor', async ({ page }) => {
  test.slow()
  const runs = []
  for (const theme of THEMES) {
    for (const path of PAGES) runs.push(...(await measure(page, path, theme)))
  }
  const verdict = judge(worstOf(runs))
  expect(verdict.ok, `\n${formatContrastReport(verdict)}\n`).toBe(true)
})

test('a run planted below its floor goes red', async ({ page }) => {
  // The plant the unit tests cannot be: they exercise the verdict on runs they
  // construct themselves, and the one thing that would make this gate useless
  // is the walk itself. So this plants the mistake #3691 fixed — text written
  // in `--faint` — in the real DOM, walks the real page, and requires the same
  // judge() the gate runs to call it a failure.
  const runs = await measure(page, '/', 'light')
  expect(judge(worstOf(runs)).ok, 'the page as it stands passes').toBe(true)

  // The visitor bar's links, in `--faint`. No navigation after this: `open()`
  // would throw the planted stylesheet away.
  await page.addStyleTag({ content: '.site-nav a { color: var(--faint) !important; }' })
  const planted = await collect(page, '/', 'light')
  const verdict = judge(worstOf(planted))
  expect(verdict.ok, `\n${formatContrastReport(verdict)}\n`).toBe(false)
  // Named, not just counted: a plant that stopped hitting the links but darkened
  // something else on the way past would still leave a non-empty `below` list.
  expect(verdict.below.map(({ key }: { key: string }) => key)).toEqual([
    '/|light|a.router-link-active.router-link-exact-active|',
    '/|light|a|',
  ])
})

test('the ratio the page works out is the one the rule would', async ({ page }) => {
  // Every judgement this gate makes rests on a number the page worked out for
  // itself. `need` is cross-checked run by run in collect(), and the floors agree
  // because of it; `ratio` never was, and the module's own luminance() has no
  // caller outside its unit test. So editing the formula in the page — the half
  // that actually decides — would move every judgement with nothing to notice,
  // and editing the module's copy would move nothing at all. This measures real
  // painted runs whose colours are known and requires both halves to agree.
  await open(page, '/', 'light')
  const planted = await page.evaluate(
    (pairs: [string, string][]) =>
      pairs.map(([fg, bg], index) => {
        const el = document.createElement('span')
        el.className = `contrast-probe contrast-probe-${index}`
        el.textContent = 'probe'
        el.style.color = fg
        el.style.background = bg
        el.style.fontSize = '16px'
        el.style.fontWeight = '400'
        document.body.appendChild(el)
        return { selector: `span.contrast-probe.contrast-probe-${index}`, fg, bg }
      }),
    PROBES,
  )
  const runs = await page.evaluate(collectRuns)
  for (const { selector, fg, bg } of planted) {
    const run = runs.find((candidate: { selector: string }) => candidate.selector === selector)
    expect(run, `${selector} was not walked`).toBeTruthy()
    expect(run.unjudged, `${selector} paints on a solid ground of its own`).toBe('')
    expect(run.ratio, `${selector} ${fg} on ${bg}`).toBe(roundRatio(contrastRatio(hex(fg), hex(bg))))
  }
})

/** Colours the pages really paint, and the two halves of the rule must agree on:
 * --faint on white (below the 4.5 text floor), the amber fill's label (below the
 * 3 large-text/icon floor), and ordinary ink on white (above both). */
const PROBES: [string, string][] = [
  ['#747a82', '#ffffff'],
  ['#ffffff', '#f57f17'],
  ['#101418', '#ffffff'],
]

/** `#rrggbb` as the `[r, g, b]` triple the rule takes. */
function hex(value: string): [number, number, number] {
  return [parseInt(value.slice(1, 3), 16), parseInt(value.slice(3, 5), 16), parseInt(value.slice(5, 7), 16)]
}
