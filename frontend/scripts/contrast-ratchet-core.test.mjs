// Tests for the rendered-contrast rule and its verdict. Run by `pnpm run
// test:ratchet` (node --test scripts/*.test.mjs), locally and in frontend.yml.
//
// The measurement itself needs a browser and is driven by
// e2e/tests/contrast-ratchet.spec.ts; what is tested here is everything around
// it — the WCAG floors, how a run is identified, which instance of a key
// speaks for it, and the verdict on a walk.
//
// The plant for the walk itself — the mistake written into a real page, and the
// real judge() calling it a failure — lives in e2e/tests/contrast-ratchet.spec.ts,
// where the browser is.
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  contrastRatio,
  floorFor,
  formatContrastReport,
  judge,
  keyOf,
  luminance,
  roundRatio,
  tierOf,
  TIERS,
  worstOf,
} from './contrast-ratchet-core.mjs'

// ---------------------------------------------------------------------------
// 1. The floors.
// ---------------------------------------------------------------------------

describe('floorFor', () => {
  it('asks 4.5:1 of body text', () => {
    assert.equal(floorFor({ size: 16, weight: 400, icon: false }), 4.5)
    assert.equal(floorFor({ size: 17, weight: 700, icon: false }), 4.5, 'bold is not enough below the size')
  })

  it('asks 3:1 of large text, at either of the two ways in', () => {
    assert.equal(floorFor({ size: 24, weight: 400, icon: false }), 3, '24px at any weight')
    assert.equal(floorFor({ size: 18.66, weight: 700, icon: false }), 3, '18.66px from weight 700')
    assert.equal(floorFor({ size: 18.66, weight: 699, icon: false }), 4.5, 'just under that weight')
    assert.equal(floorFor({ size: 18.65, weight: 700, icon: false }), 4.5, 'just under that size')
  })

  it('asks 3:1 of an icon glyph however small it is set', () => {
    // A pill's chevron is drawn at 14px. It is a graphic, not a word, so WCAG
    // puts it on the 3:1 step — and it is the class of run axe-core never saw.
    assert.equal(floorFor({ size: 14, weight: 400, icon: true }), 3)
  })

  it('gives each rung a name, and reads the floor off the rung', () => {
    // The names are what a failure quotes ("the graphic rung"), so a report says
    // which step was missed instead of a bare number.
    const probes = {
      body: { size: 16, weight: 400, icon: false },
      large: { size: 24, weight: 400, icon: false },
      graphic: { size: 14, weight: 400, icon: true },
    }
    for (const [name, probe] of Object.entries(probes)) {
      assert.equal(tierOf(probe), name)
      assert.equal(floorFor(probe), TIERS[name], `${name} reads its floor off its rung`)
    }
  })
})

describe('the WCAG arithmetic', () => {
  it('agrees with the two values everyone knows', () => {
    assert.equal(roundRatio(contrastRatio([0, 0, 0], [255, 255, 255])), 21)
    assert.equal(roundRatio(contrastRatio([255, 255, 255], [255, 255, 255])), 1)
  })

  it('does not care which colour is which', () => {
    const a = luminance([0xf5, 0x7f, 0x17])
    const b = luminance([0xff, 0xff, 0xff])
    assert.equal(contrastRatio([0xf5, 0x7f, 0x17], [255, 255, 255]), contrastRatio([255, 255, 255], [0xf5, 0x7f, 0x17]))
    assert.ok(a < b)
  })

  it('reports white on the amber brand fill as the 2.65:1 the landing once shipped', () => {
    assert.equal(roundRatio(contrastRatio([255, 255, 255], [0xf5, 0x7f, 0x17])), 2.65)
  })
})

// ---------------------------------------------------------------------------
// 2. How a run is identified, and which instance of it speaks.
// ---------------------------------------------------------------------------

describe('keyOf', () => {
  it('is the page, the theme, the element and the pseudo-element', () => {
    assert.equal(
      keyOf({ path: '/', theme: 'light', selector: 'span.v-btn__content', pseudo: '' }),
      '/|light|span.v-btn__content|'
    )
    assert.equal(
      keyOf({ path: '/', theme: 'dark', selector: 'i.mdi-x', pseudo: '::before' }),
      '/|dark|i.mdi-x|::before'
    )
  })

  it('leaves the measured numbers out, so a re-render is the same run', () => {
    const key = keyOf({ path: '/', theme: 'light', selector: 'span.im-time' })
    assert.equal(key, keyOf({ path: '/', theme: 'light', selector: 'span.im-time' }))
  })
})

describe('worstOf', () => {
  const run = (over) => ({
    path: '/',
    theme: 'light',
    selector: 'span.im-time',
    ratio: 4.33,
    need: 4.5,
    unjudged: '',
    text: '16:20',
    ...over,
  })

  it('keeps one entry per key, the instance closest to unreadable', () => {
    const worst = worstOf([run({ ratio: 4.33 }), run({ ratio: 3.1 }), run({ ratio: 4.4 })])
    assert.equal(worst.size, 1)
    assert.equal(worst.get('/|light|span.im-time|').ratio, 3.1)
  })

  it('prefers a measured instance over one the walker could not read, and counts the other', () => {
    // The unreadable instance is given the better-looking ratio on purpose: the
    // answer is only 4.33 if the measured instance is the one kept. Keeping the
    // other would have `judge` report a key that is perfectly readable as
    // unmeasurable, which is a failure — so which one wins has a consequence,
    // and this is what notices it changing. What the two instances are is not a
    // reason to lose one of them: the ratio says what the readable instance
    // measures, and the count says there is another the walker cannot see.
    const worst = worstOf([run({ ratio: 1.2, unjudged: 'a gradient is behind it' }), run({ ratio: 4.33 })])
    assert.equal(worst.get('/|light|span.im-time|').ratio, 4.33)
    assert.equal(worst.get('/|light|span.im-time|').unjudged, '')
    assert.equal(worst.get('/|light|span.im-time|').unreadable, 1)
  })

  it('counts every instance it could not read, on the key it kept', () => {
    // `/solutions` as it stands: five plain paragraphs on a wash and two on a
    // gradient. One entry per key means one ratio, but the two the ratio does
    // not speak for have to survive somewhere.
    const worst = worstOf([
      run({ selector: 'p', path: '/solutions', ratio: 6.12 }),
      run({ selector: 'p', path: '/solutions', unjudged: 'a gradient is behind it' }),
      run({ selector: 'p', path: '/solutions', unjudged: 'a gradient is behind it' }),
      run({ selector: 'p', path: '/solutions', ratio: 7.4 }),
    ])
    assert.equal(worst.size, 1)
    assert.equal(worst.get('/solutions|light|p|').ratio, 6.12)
    assert.equal(worst.get('/solutions|light|p|').unreadable, 2)
  })

  it('counts the key itself when no instance of it can be read', () => {
    const worst = worstOf([run({ unjudged: 'a gradient is behind it' }), run({ unjudged: 'a gradient is behind it' })])
    assert.equal(worst.get('/|light|span.im-time|').ratio, 4.33, 'the ratio is still recorded on the run')
    assert.equal(worst.get('/|light|span.im-time|').unreadable, 2)
  })

  it('keeps keys apart across page, theme and pseudo-element', () => {
    const worst = worstOf([run({}), run({ theme: 'dark' }), run({ pseudo: '::before' }), run({ path: '/solutions' })])
    assert.deepEqual([...worst.keys()].sort(), [
      '/solutions|light|span.im-time|',
      '/|dark|span.im-time|',
      '/|light|span.im-time|',
      '/|light|span.im-time|::before',
    ])
  })
})

// ---------------------------------------------------------------------------
// 3. The verdict.
// ---------------------------------------------------------------------------

const KEY = '/|light|span.im-time|'
const runsOf = (...runs) => worstOf(runs)
const run = (over) => ({
  path: '/',
  theme: 'light',
  selector: 'span.im-time',
  ratio: 4.33,
  need: 4.5,
  size: 12,
  weight: 400,
  icon: false,
  unjudged: '',
  text: '16:20',
  ...over,
})
/** The worst entry for one key, where `hidden` instances of it sit on a picture
 * the walker cannot read — built the way the page produces them, so the count is
 * `worstOf`'s answer and not the fixture's. */
const withHidden = (hidden, over) =>
  runsOf(run(over), ...Array.from({ length: hidden }, () => run({ ...over, unjudged: 'a gradient is behind it' })))
const PICTURE = 'a background image or gradient sits between the run and its ground'

describe('judge', () => {
  it('fails on a run below its floor', () => {
    const result = judge(runsOf(run({})))
    assert.equal(result.ok, false)
    assert.deepEqual(
      result.below.map(({ key }) => key),
      [KEY]
    )
  })

  it('passes a run that clears its floor', () => {
    const result = judge(runsOf(run({ ratio: 4.6 })))
    assert.equal(result.ok, true)
    assert.equal(result.below.length, 0)
  })

  it('passes a page with nothing on it to judge', () => {
    assert.equal(judge(runsOf()).ok, true)
  })

  it('judges a key by its worst instance', () => {
    const result = judge(runsOf(run({ ratio: 6.1 }), run({ ratio: 2.65 })))
    assert.equal(result.ok, false)
    assert.equal(result.below[0].run.ratio, 2.65)
  })

  it('fails on a run the walker cannot measure', () => {
    // The whole reason this is a failure rather than a note. A page that puts a
    // gradient behind its text would otherwise pass this check by hiding the
    // text from it.
    const result = judge(runsOf(run({ unjudged: PICTURE })))
    assert.equal(result.ok, false)
    assert.equal(result.unjudged.length, 1)
    assert.equal(result.below.length, 0, 'there is no ratio to be below a floor with')
    assert.match(formatContrastReport(result), /Runs the check cannot measure/)
  })

  it('fails on a readable key one of whose instances sits on a picture', () => {
    // The readable instance clears its floor; a sibling of the same selector
    // does not count as clean just because the walker could not read it.
    const result = judge(withHidden(1, { ratio: 6.1 }))
    assert.equal(result.ok, false)
    assert.equal(result.below.length, 0)
    assert.equal(result.unjudged.length, 1)
    assert.match(formatContrastReport(result), /1 instance\(s\) of it the walker cannot read/)
  })

  it('reports a key below its floor and partly unreadable under both headings', () => {
    const result = judge(withHidden(2, {}))
    assert.equal(result.below.length, 1)
    assert.equal(result.unjudged.length, 1)
  })

  it('names the two colour rules a reader should reach for first', () => {
    const report = formatContrastReport(judge(runsOf(run({ ratio: 2.65 }))))
    assert.match(report, /--accent is a fill and not a text colour/)
    assert.match(report, /--faint is decoration/)
    assert.doesNotMatch(report, /baseline/)
  })

  it('says every run clears its floor when they do', () => {
    assert.match(formatContrastReport(judge(runsOf(run({ ratio: 7 })))), /every run clears its floor/)
  })
})

// ---------------------------------------------------------------------------
// 3b. The --faint rung: the token decides the fix, never the verdict.
// ---------------------------------------------------------------------------

describe('the --faint rung', () => {
  // `--faint` is the project's decorative rung — a deliberate 3:1 for icons,
  // dividers and placeholder shapes (docs/design-system.md §2.6, #3691). A run
  // painted with it is judged on the rung its own size, weight and font put it
  // on, and a failure quotes the fix for that token.
  const faintWords = run({ faint: true })
  const faintGlyph = run({
    faint: true,
    selector: 'i.mdi-chevron-right.mdi',
    icon: true,
    size: 14,
    need: 3,
    ratio: 4.33,
    text: '',
  })

  it('does not lift a run off the rung its size and weight put it on', () => {
    assert.equal(tierOf(faintWords), 'body')
    assert.equal(floorFor(faintWords), 4.5)
    // 4.33:1 is where #3691 found body text on the public pages. The token does
    // not make it a graphic's problem: it is still below the text floor.
    const result = judge(runsOf(faintWords))
    assert.equal(result.ok, false)
    assert.equal(result.below.length, 1)
  })

  it('passes a decoration on the 3:1 rung it is for, at a ratio words would fail', () => {
    const result = judge(runsOf(faintGlyph))
    assert.equal(result.ok, true)
  })

  it('blocks a decoration on that rung once it drops below 3:1', () => {
    const result = judge(runsOf(run({ ...faintGlyph, ratio: 2.65 })))
    assert.equal(result.ok, false)
    assert.match(formatContrastReport(result), /\(the graphic rung\)/)
    assert.match(formatContrastReport(result), /a decoration that\n\s+reads below 3:1 needs the token itself moved/)
  })

  it('hands words painted with it the fix for the token, not a deeper token', () => {
    const report = formatContrastReport(judge(runsOf(faintWords)))
    assert.match(report, /\(the body rung\)/)
    assert.match(report, /--faint is the decorative rung/)
    assert.match(report, /Do not deepen the token/)
    assert.match(report, /Put\n\s+the words on --muted \(\.t-meta-read \/ \.t-eyebrow-read\)/)
  })

  it('says that only for a run painted with the token', () => {
    // Same key, same ratio, same floor — the tag is the whole difference, so
    // this pair is what shows the report is reading the tag.
    const report = formatContrastReport(judge(runsOf(run({}))))
    assert.doesNotMatch(report, /--faint is the decorative rung/)
    assert.match(report, /--faint is decoration/, 'the generic hint still names the token')
  })

  it('offers no colour fix where the failure is not a contrast failure', () => {
    // Tagged, and the walker cannot read the run at all, so there is no ratio
    // to answer for.
    const blind = formatContrastReport(judge(runsOf(run({ faint: true, unjudged: PICTURE }))))
    assert.match(blind, /Runs the check cannot measure/)
    assert.doesNotMatch(blind, /Do not deepen the token/)
    // Tagged and measured, reported only because a sibling of the same selector
    // sits on a picture: the ratio itself clears the floor.
    const sibling = formatContrastReport(judge(withHidden(1, { faint: true, ratio: 6.1 })))
    assert.match(sibling, /cannot read/)
    assert.doesNotMatch(sibling, /Do not deepen the token/)
  })

  it('says it once where a key is below its floor and partly unreadable', () => {
    // Only the first heading is a contrast failure: repainting the words as the
    // fix says leaves the gate red, which is what the second heading is about.
    const report = formatContrastReport(judge(withHidden(1, { faint: true })))
    const unmeasurable = report.indexOf('Runs the check cannot measure:')
    assert.ok(unmeasurable > -1, 'the key is also reported as one the check cannot measure')
    assert.match(report.slice(0, unmeasurable), /Do not deepen the token/)
    assert.doesNotMatch(report.slice(unmeasurable), /Do not deepen the token/)
  })
})
