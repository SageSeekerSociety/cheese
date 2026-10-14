// Tests for the rendered-contrast rule and its ratchet. Run by `pnpm run
// test:ratchet` (node --test scripts/*.test.mjs), locally and in frontend.yml.
//
// The measurement itself needs a browser and is driven by
// e2e/tests/contrast-ratchet.spec.ts; what is tested here is everything around
// it — the WCAG floors, how a run is identified across two measurements, and
// the arithmetic that decides what a change to a ratio means. That last part is
// the one worth having: the interesting cases are the ones where the answer
// must be "nothing changed", and none of them look like a failure in a diff.
//
// Last of all, a plant: a run below its floor that the baseline has never seen
// has to come back as a regression. A gate nobody has watched fail is not a
// gate.
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  compare,
  contrastRatio,
  entryFor,
  floorFor,
  formatContrastReport,
  keyOf,
  luminance,
  roundRatio,
  tightenedBaseline,
  TOLERANCE,
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

  it('reports white on the amber brand fill as the 2.65:1 the baseline freezes', () => {
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
      '/|light|span.v-btn__content|',
    )
    assert.equal(keyOf({ path: '/', theme: 'dark', selector: 'i.mdi-x', pseudo: '::before' }), '/|dark|i.mdi-x|::before')
  })

  it('leaves the measured numbers out, so a re-render is the same run', () => {
    const key = keyOf({ path: '/', theme: 'light', selector: 'span.im-time' })
    assert.equal(key, keyOf({ path: '/', theme: 'light', selector: 'span.im-time' }))
  })
})

describe('worstOf', () => {
  const run = (over) => ({ path: '/', theme: 'light', selector: 'span.im-time', ratio: 4.33, need: 4.5, unjudged: '', text: '16:20', ...over })

  it('keeps one entry per key, the instance closest to unreadable', () => {
    const worst = worstOf([run({ ratio: 4.33 }), run({ ratio: 3.1 }), run({ ratio: 4.4 })])
    assert.equal(worst.size, 1)
    assert.equal(worst.get('/|light|span.im-time|').ratio, 3.1)
  })

  it('prefers a measured instance over one the walker could not read', () => {
    const worst = worstOf([run({ unjudged: 'a gradient is behind it' }), run({ ratio: 4.33 })])
    assert.equal(worst.get('/|light|span.im-time|').ratio, 4.33)
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
// 3. The ratchet.
// ---------------------------------------------------------------------------

const KEY = '/|light|span.im-time|'
const KEY2 = '/solutions|light|i.mdi-email-outline.mdi|::before'
const runsOf = (...runs) => worstOf(runs)
const run = (over) => ({ path: '/', theme: 'light', selector: 'span.im-time', ratio: 4.33, need: 4.5, size: 12, weight: 400, icon: false, unjudged: '', text: '16:20', ...over })
const frozen = (ratio) => ({ [KEY]: { ratio, need: 4.5, unjudged: '', sample: '16:20' } })

describe('compare', () => {
  it('fails on a run below its floor that the baseline has never seen', () => {
    const result = compare({}, runsOf(run({})))
    assert.equal(result.ok, false)
    assert.equal(result.added.length, 1)
    assert.equal(result.added[0].key, KEY)
  })

  it('passes a new run that clears its floor — there is nothing to freeze', () => {
    const result = compare({}, runsOf(run({ ratio: 4.6 })))
    assert.equal(result.ok, true)
    assert.equal(result.added.length, 0)
  })

  it('fails on a frozen run that got further below its floor', () => {
    const result = compare(frozen(4.33), runsOf(run({ ratio: 2.65 })))
    assert.equal(result.ok, false)
    assert.deepEqual(result.worse, [{ key: KEY, was: frozen(4.33)[KEY], run: runsOf(run({ ratio: 2.65 })).get(KEY) }])
  })

  it('passes a frozen run that moved by less than the tolerance', () => {
    // The case that would otherwise alternate between red and green on a run
    // sitting a hair from its floor: `span.scene-avatar`'s "周" measures 4.44
    // against a 4.5 floor, and a re-render can put it either side of 4.49.
    const result = compare(frozen(4.44), runsOf(run({ ratio: 4.44 + TOLERANCE - 0.01 })))
    assert.equal(result.ok, true)
    assert.equal(result.improved.length, 0)
  })

  it('passes a frozen run that got better but is still below its floor, and asks for a tighter baseline', () => {
    const result = compare(frozen(4.33), runsOf(run({ ratio: 4.33 + TOLERANCE + 0.01 })))
    assert.equal(result.ok, true)
    assert.equal(result.improved.length, 1)
    assert.match(formatContrastReport(result), /tighten the baseline/)
  })

  it('passes a frozen run that clears its floor now', () => {
    const result = compare(frozen(2.65), runsOf(run({ selector: 'span.im-time', ratio: 4.6 })))
    assert.equal(result.ok, true)
    assert.equal(result.improved.length, 1)
  })

  it('passes a frozen run that is gone from the page', () => {
    const result = compare(frozen(4.33), runsOf())
    assert.equal(result.ok, true)
    assert.equal(result.improved.length, 1)
    assert.match(formatContrastReport(result), /gone from the page/)
  })

  it('fails on a run the walker cannot measure that was not already frozen', () => {
    // The whole reason this is a failure rather than a note. A page that puts a
    // gradient behind its text would otherwise pass this check by hiding the
    // text from it.
    const result = compare({}, runsOf(run({ unjudged: 'a background image or gradient sits between the run and its ground' })))
    assert.equal(result.ok, false)
    assert.equal(result.unjudged.length, 1)
  })

  it('passes a run that was already frozen as unmeasurable', () => {
    const baseline = { [KEY]: { ratio: null, need: 4.5, unjudged: 'a background image or gradient sits between the run and its ground', sample: '16:20' } }
    const result = compare(baseline, runsOf(run({ unjudged: 'a background image or gradient sits between the run and its ground' })))
    assert.equal(result.ok, true)
    assert.equal(result.unjudged.length, 0)
  })

  it('fails when a frozen, measured run becomes unmeasurable', () => {
    const result = compare(frozen(4.33), runsOf(run({ unjudged: 'a background image or gradient sits between the run and its ground' })))
    assert.equal(result.ok, false)
    assert.equal(result.unjudged.length, 1)
  })

  it('fails when a worse instance of a frozen run appears', () => {
    const result = compare(frozen(4.33), runsOf(run({ ratio: 4.33 }), run({ ratio: 1.8 })))
    assert.equal(result.ok, false)
    assert.equal(result.worse[0].run.ratio, 1.8)
  })

  it('names the two colour rules a reader should reach for first', () => {
    const report = formatContrastReport(compare(frozen(4.33), runsOf(run({ ratio: 2.65 }))))
    assert.match(report, /--accent is a fill and not a text colour/)
    assert.match(report, /--faint is decoration/)
    assert.match(report, /contrast-baseline\.json/)
  })
})

describe('tightenedBaseline', () => {
  it('keeps a frozen run that is still below its floor, at the worse of the two ratios', () => {
    assert.deepEqual(tightenedBaseline(frozen(4.33), runsOf(run({ ratio: 4.2 }))), {
      [KEY]: { ratio: 4.2, need: 4.5, unjudged: '', sample: '16:20' },
    })
  })

  it('drops a frozen run that clears its floor, and one that is gone', () => {
    assert.deepEqual(tightenedBaseline(frozen(4.33), runsOf(run({ ratio: 4.6 }))), {})
    assert.deepEqual(tightenedBaseline(frozen(4.33), runsOf()), {})
  })

  it('refuses to freeze a run the baseline has never seen', () => {
    // An update is not a way to launder new debt in: the new run stays unfrozen
    // and the next run of the check fails on it.
    assert.deepEqual(tightenedBaseline({}, runsOf(run({}))), {})
  })

  it('freezes one when asked for it by name', () => {
    assert.deepEqual(tightenedBaseline({}, runsOf(run({})), { freezeNew: true }), {
      [KEY]: { ratio: 4.33, need: 4.5, unjudged: '', sample: '16:20' },
    })
  })

  it('keeps an unmeasurable run as unmeasurable', () => {
    const next = tightenedBaseline({}, runsOf(run({ unjudged: 'a gradient is behind it' })), { freezeNew: true })
    assert.equal(next[KEY].ratio, null)
    assert.equal(next[KEY].unjudged, 'a gradient is behind it')
    assert.ok(compare(next, runsOf(run({ unjudged: 'a gradient is behind it' }))).ok)
  })

  it('never loosens what it keeps, so an update cannot paper over a regression', () => {
    // Frozen at 2.65, measured worse. The kept value must be the worse one, or
    // the run becomes invisible to the next comparison.
    const next = tightenedBaseline({ [KEY]: { ratio: 2.65, need: 3, unjudged: '', sample: '开始使用' } }, runsOf(run({ ratio: 1.4, need: 3 })))
    assert.equal(next[KEY].ratio, 1.4)
    assert.equal(compare(next, runsOf(run({ ratio: 2.0, need: 3 }))).ok, true, 'still worse than the new baseline')
  })

  it('is a fixed point: updating a passing tree changes nothing', () => {
    const baseline = {
      [KEY]: entryFor(run({})),
      [KEY2]: entryFor({ path: '/solutions', theme: 'light', selector: 'i.mdi-email-outline.mdi', pseudo: '::before', ratio: 2.65, need: 3, icon: true, unjudged: '', text: '' }),
    }
    const runs = runsOf(
      run({}),
      { path: '/solutions', theme: 'light', selector: 'i.mdi-email-outline.mdi', pseudo: '::before', ratio: 2.65, need: 3, icon: true, unjudged: '', text: '' },
    )
    assert.deepEqual(tightenedBaseline(baseline, runs), baseline)
  })
})

// ---------------------------------------------------------------------------
// 4. The plant.
// ---------------------------------------------------------------------------

describe('a plant', () => {
  it('goes red on a run below its floor and green once the baseline knows it', () => {
    // A page that grew one sub-floor run: a new timestamp written in --faint.
    const plant = run({ selector: 'span.tl-mark__body', ratio: 4.33 })
    const before = compare(frozen(4.33), runsOf(run({})))
    assert.equal(before.ok, true, 'the tree as it stands passes')
    const after = compare(frozen(4.33), runsOf(run({}), plant))
    assert.equal(after.ok, false, 'the plant is caught')
    assert.equal(after.added.length, 1)
    assert.match(formatContrastReport(after), /span\.tl-mark__body/)
    // And the way out is a commit that says so, not a flag on the check.
    const frozenIn = tightenedBaseline(frozen(4.33), runsOf(run({}), plant), { freezeNew: true })
    assert.equal(compare(frozenIn, runsOf(run({}), plant)).ok, true)
  })
})
