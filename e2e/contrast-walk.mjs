// The browser half of `fe-contrast`: open one of the public pages in one
// theme, walk what it painted, hand the runs back.
//
// Two callers share this file. `tests/contrast-ratchet.spec.ts` is the gate
// that runs on a pull request; `contrast-ratchet.mjs` is the record the ratchet
// snapshot collector reads after the merge, because the number the page freezes
// and the verdict the gate gives have to come from the same walk. A second copy
// of this code is how the two would drift apart.
//
// Plain JavaScript with no test-runner imports on purpose: the collector runs
// the driver as a node script, outside Playwright's test runner, and a helper
// that needed `expect` would only work for one of the two callers.

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { collectRuns, floorFor } from '../frontend/scripts/contrast-ratchet-core.mjs'

/** The frozen debt, beside the rule it belongs to, in frontend/. */
export const BASELINE = fileURLToPath(new URL('../frontend/contrast-baseline.json', import.meta.url))

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
 * `open()` instead.
 */
export const MIN_RUNS = { '/': 50, '/solutions': 60, '/download': 8 }

/** How long a page gets, from navigation to mounted, before the walk gives up
 *  on it — the whole of `open()`, deliberately one clock rather than a second
 *  one for the load. A cold dev server transforms every module on the first
 *  request, and waiting twice as long for the load as for the mount it feeds
 *  would fail the page at the earlier of two deadlines. Comfortably above what
 *  a served build needs; the collector's driver passes a much longer one. */
export const MOUNT_TIMEOUT = 15_000

/** The baseline’s `runs`, or an empty one where the file is not there yet. */
export function readBaseline() {
  try {
    return JSON.parse(readFileSync(BASELINE, 'utf8')).runs ?? {}
  } catch (error) {
    if (error.code === 'ENOENT') return {}
    throw error
  }
}

/** The baseline file as it is written, matching the other ratchets' shape. */
export function baselineFile(runs) {
  return {
    _comment:
      'Frozen rendered-contrast debt on the public pages, one entry per text run that is below ' +
      'its WCAG floor (4.5:1, or 3:1 for large text and icon glyphs) or that the walker cannot ' +
      'measure, wholly or in part — a `ratio` of null with a reason means a background image or ' +
      'gradient ' +
      'sits between the run and its ground, and those are frozen too rather than skipped. One ' +
      'entry covers a whole selector, and a selector is not always one element: `ratio` answers ' +
      'for the instances of it the walker could read, and `unreadable` counts those it could ' +
      'not, so text hidden behind a picture cannot drop out of the check by sitting beside a ' +
      'readable sibling. `sample` is a piece of the text the ratio was measured on. The ratchet ' +
      'blocks any new or worse run, and the count is ratcheted the same way; the baseline only ' +
      'goes down. Regenerate with `CONTRAST_UPDATE=1 pnpm --dir e2e exec playwright test ' +
      'contrast-ratchet`, which will not add a key the baseline has never seen unless ' +
      'CONTRAST_FREEZE_NEW=1 is set as well. The rule and its messages live in ' +
      'frontend/scripts/contrast-ratchet-core.mjs.',
    runs,
  }
}

/**
 * Open a page in one theme and leave it settled: loaded, mounted, and with its
 * stylesheet confirmed to have arrived. `timeout` is the clock for all of it.
 */
export async function open(page, path, theme, { timeout = MOUNT_TIMEOUT } = {}) {
  await page.emulateMedia({ colorScheme: theme === 'dark' ? 'dark' : 'light', reducedMotion: 'reduce' })
  await page.addInitScript((value) => {
    localStorage.setItem('cheesex.theme', value)
  }, theme)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(path, { waitUntil: 'load', timeout })
  // The visitor shell's own bar is the first thing the app renders, and it is
  // on all three pages. `body` is visible from the first byte of index.html and
  // would let the walk run against a page the app has not mounted yet — which
  // is how this measured eight runs on a page that has a hundred.
  const bar = page.getByRole('navigation', { name: '主导航' })
  try {
    await bar.waitFor({ state: 'visible', timeout })
  } catch {
    throw new Error(`${path} [${theme}] never rendered the visitor bar — the app did not mount`)
  }
  // The app mounting does not mean its stylesheet arrived: index.html loads the
  // CSS as its own render-blocking <link>, and the module mounts either way. With
  // no stylesheet every run falls back to the browser's black on white — 21:1,
  // which is greener than the styled page — and a walk over it would empty the
  // baseline. One design token is enough to tell the two apart, so this is the
  // check that the count guard cannot be.
  const muted = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--muted').trim(),
  )
  if (!muted) {
    throw new Error(
      `${path} [${theme}] rendered without its stylesheet — every colour would measure as the browser default`,
    )
  }
}

/**
 * Walk the page as it now stands and return the runs it painted. Split from
 * `open` so a caller can change the page (a planted style tag survives) without
 * navigating, which would throw the change away.
 */
export async function collect(page, path, theme) {
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
  const stamped = runs.map((run) => ({ ...run, path, theme }))
  const least = MIN_RUNS[path]
  if (stamped.length < least) {
    throw new Error(
      `${path} [${theme}] produced ${stamped.length} rendered text runs, fewer than the ${least} ` +
        'this page is known to have — see MIN_RUNS in contrast-walk.mjs for what that usually means',
    )
  }
  // The floor the walker applies in the page and floorFor() are the same rule
  // written twice — the page cannot import it. This is where a change to one
  // that is not mirrored in the other fails, instead of silently misjudging
  // every run.
  for (const run of stamped) {
    const rule = floorFor({ size: run.size, weight: run.weight, icon: run.icon })
    if (run.need !== rule) {
      throw new Error(
        `${run.path} [${run.theme}] ${run.selector}: the page judged it against ${run.need}:1, ` +
          `the rule says ${rule}:1`,
      )
    }
  }
  return stamped
}

export async function measure(page, path, theme, options) {
  await open(page, path, theme, options)
  return collect(page, path, theme)
}
