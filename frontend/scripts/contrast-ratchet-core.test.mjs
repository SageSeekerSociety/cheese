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
// Last of all, a plant at the level the arithmetic can be wrong at: a run below
// its floor that the baseline has never seen has to come back as a regression.
// The plant for the walk itself — the mistake written into a real page, and the
// real compare() calling it a regression — lives in
// e2e/tests/contrast-ratchet.spec.ts, where the browser is.
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
  tierOf,
  TIERS,
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

  it('reports white on the amber brand fill as the 2.65:1 that started the ratchet', () => {
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
    // other would have `compare` report a key that is perfectly readable as
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
// 3. The ratchet.
// ---------------------------------------------------------------------------

const KEY = '/|light|span.im-time|'
const KEY2 = '/solutions|light|i.mdi-email-outline.mdi|::before'
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
const frozen = (ratio) => ({ [KEY]: { ratio, need: 4.5, unjudged: '', sample: '16:20' } })
/** The worst entry for one key, where `hidden` instances of it sit on a picture
 * the walker cannot read — built the way the page produces them, so the count is
 * `worstOf`'s answer and not the fixture's. */
const withHidden = (hidden, over) =>
  runsOf(run(over), ...Array.from({ length: hidden }, () => run({ ...over, unjudged: 'a gradient is behind it' })))

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
    const result = compare(
      {},
      runsOf(run({ unjudged: 'a background image or gradient sits between the run and its ground' }))
    )
    assert.equal(result.ok, false)
    assert.equal(result.unjudged.length, 1)
  })

  it('passes a run that was already frozen as unmeasurable', () => {
    const baseline = {
      [KEY]: {
        ratio: null,
        need: 4.5,
        unjudged: 'a background image or gradient sits between the run and its ground',
        sample: '16:20',
      },
    }
    const result = compare(
      baseline,
      runsOf(run({ unjudged: 'a background image or gradient sits between the run and its ground' }))
    )
    assert.equal(result.ok, true)
    assert.equal(result.unjudged.length, 0)
  })

  it('fails when a run frozen as unmeasurable becomes readable and is below its floor', () => {
    // The walker finally got a colour out of it, and the answer is bad. That is
    // not an improvement to be paid off with an update; the run is below its
    // floor and the gate says so.
    const baseline = {
      [KEY]: {
        ratio: null,
        need: 4.5,
        unjudged: 'a background image or gradient sits between the run and its ground',
        sample: '16:20',
      },
    }
    const result = compare(baseline, runsOf(run({ ratio: 1.5 })))
    assert.equal(result.ok, false)
    assert.equal(result.improved.length, 0)
    assert.equal(result.worse.length, 1)
    assert.match(formatContrastReport(result), /1\.5:1, needs 4\.5:1/)
  })

  it('stays quiet when one that becomes readable lands within the tolerance of its floor', () => {
    // The gradient went away and the run turns out to sit right on its floor.
    // Without the tolerance this one key would go red on 4.49 and green on
    // 4.50, run after run, which is the flapping TOLERANCE is here to stop.
    const baseline = { [KEY]: { ratio: null, need: 4.5, unjudged: 'a gradient is behind it', sample: '16:20' } }
    const result = compare(baseline, runsOf(run({ ratio: 4.49 })))
    assert.equal(result.ok, true)
    assert.equal(result.worse.length, 0)
    assert.equal(result.improved.length, 0)
  })

  it('passes one that becomes readable and clears its floor', () => {
    const baseline = { [KEY]: { ratio: null, need: 4.5, unjudged: 'a gradient is behind it', sample: '16:20' } }
    const result = compare(baseline, runsOf(run({ ratio: 6.1 })))
    assert.equal(result.ok, true)
    assert.equal(result.improved.length, 1)
  })

  it('fails when a frozen, measured run becomes unmeasurable', () => {
    const result = compare(
      frozen(4.33),
      runsOf(run({ unjudged: 'a background image or gradient sits between the run and its ground' }))
    )
    assert.equal(result.ok, false)
    assert.equal(result.unjudged.length, 1)
  })

  it('fails when an instance of a frozen key is one the walker cannot read', () => {
    // The key is readable where it was measured; what changed is that another
    // instance of the same selector went behind a picture. Nothing the ratio can
    // show, and exactly how a page would hide text from this check, so it fails
    // on its own.
    const result = compare(frozen(6.12), withHidden(1, { ratio: 6.12, need: 4.5 }))
    assert.equal(result.unjudged.length, 1)
    assert.equal(result.ok, false)
    assert.match(formatContrastReport(result), /1 instance\(s\) of it the walker cannot read/)
  })

  it('stays quiet about the instances the baseline already froze as unreadable', () => {
    const baseline = { [KEY]: { ratio: 6.12, need: 4.5, unreadable: 2, unjudged: '', sample: '众智之所为' } }
    const result = compare(baseline, withHidden(2, { ratio: 6.12, need: 4.5 }))
    assert.equal(result.ok, true)
    assert.equal(result.unjudged.length, 0)
    assert.equal(result.improved.length, 0, 'and it is a fixed point, not a run the update has to chase')
  })

  it('fails when one more of them goes behind a picture', () => {
    const baseline = { [KEY]: { ratio: 6.12, need: 4.5, unreadable: 2, unjudged: '', sample: '众智之所为' } }
    assert.equal(compare(baseline, withHidden(3, { ratio: 6.12, need: 4.5 })).ok, false)
    assert.equal(compare(baseline, withHidden(2, { ratio: 6.12, need: 4.5 })).ok, true)
  })

  it('passes a frozen key whose instances the walker can all read again', () => {
    // However much of it was behind a picture, none of it is now: the baseline
    // is holding a count the page no longer has, so it is an improvement to pay
    // off with an update rather than silence.
    const baseline = { [KEY]: { ratio: 6.12, need: 4.5, unreadable: 2, unjudged: '', sample: '众智之所为' } }
    const result = compare(baseline, runsOf(run({ ratio: 6.12, need: 4.5 })))
    assert.equal(result.ok, true)
    assert.equal(result.improved.length, 1)
  })

  it('does not call a key with instances behind a picture settled', () => {
    // It clears its floor, so by the ratio alone it is done with the baseline —
    // but the ratio is not the whole key, and dropping the entry would throw the
    // count away with it.
    const baseline = { [KEY]: { ratio: 6.12, need: 4.5, unreadable: 1, unjudged: '', sample: '众智之所为' } }
    const result = compare(baseline, withHidden(1, { ratio: 6.12, need: 4.5 }))
    assert.equal(result.improved.length, 0)
    assert.equal(result.ok, true)
  })

  it('counts a key that got part of its unreadable instances read again as an improvement', () => {
    // The ratio is unchanged and still clears the floor, so `passes(run)` alone
    // would call this a fixed point — but the key only owes the instance still
    // behind the picture, and the count is what says the debt went down. It is
    // deliberately out of reach of `passes(run) && !blind`, which is the case
    // the count's own comparison in `compare` exists for.
    const baseline = { [KEY]: { ratio: 6.12, need: 4.5, unreadable: 2, unjudged: '', sample: '众智之所为' } }
    const result = compare(baseline, withHidden(1, { ratio: 6.12, need: 4.5 }))
    assert.equal(result.ok, true)
    assert.equal(result.improved.length, 1)
    assert.equal(result.improved[0].run.unreadable, 1)
  })

  it('takes a key frozen as unmeasurable as covering however many instances of it there are', () => {
    // The count is not ratcheted here, and deliberately: what the baseline
    // accepted for this key is that the selector sits on a picture, and a second
    // paragraph behind the same picture is the same acceptance. It is the ratio
    // that needs a count beside it, because it answers for only part of the key.
    const baseline = { [KEY]: { ratio: null, need: 4.5, unreadable: 1, unjudged: 'a gradient is behind it' } }
    const result = compare(baseline, withHidden(3, { ratio: 6.1 }))
    assert.equal(result.ok, true)
    assert.equal(result.unjudged.length, 0)
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
    const result = compare({}, runsOf(faintWords))
    assert.equal(result.ok, false)
    assert.equal(result.added.length, 1)
  })

  it('passes a decoration on the 3:1 rung it is for, at a ratio words would fail', () => {
    // The chevron in --faint at 4.33:1 owes 3:1, which it clears — there is
    // nothing here for the ratchet to report or to freeze.
    const result = compare({}, runsOf(faintGlyph))
    assert.equal(result.ok, true)
    assert.equal(result.added.length, 0)
    assert.equal(result.belowFloor, 0)
  })

  it('blocks a decoration on that rung once it drops below 3:1', () => {
    const result = compare({}, runsOf(run({ ...faintGlyph, ratio: 2.65 })))
    assert.equal(result.ok, false)
    assert.match(formatContrastReport(result), /\(the graphic rung\)/)
    assert.match(formatContrastReport(result), /a decoration that\n\s+reads below 3:1 needs the token itself moved/)
  })

  it('hands words painted with it the fix for the token, not a deeper token', () => {
    const report = formatContrastReport(compare({}, runsOf(faintWords)))
    assert.match(report, /\(the body rung\)/)
    assert.match(report, /--faint is the decorative rung/)
    assert.match(report, /Do not deepen the token/)
    assert.match(report, /Put\n\s+the words on --muted \(\.t-meta-read \/ \.t-eyebrow-read\)/)
  })

  it('says that only for a run painted with the token', () => {
    // Same key, same ratio, same floor — the tag is the whole difference, so
    // this pair is what shows the report is reading the tag.
    const report = formatContrastReport(compare({}, runsOf(run({}))))
    assert.doesNotMatch(report, /--faint is the decorative rung/)
    assert.match(report, /--faint is decoration/, 'the generic hint still names the token')
  })

  it('repeats the fix on a frozen run that slipped further below its floor', () => {
    const report = formatContrastReport(compare(frozen(4.6), runsOf(run({ faint: true, ratio: 2.65 }))))
    assert.match(report, /Frozen runs that are below their floor/)
    assert.match(report, /Do not deepen the token/)
  })

  it('offers no colour fix where the failure is not a contrast failure', () => {
    // Tagged, and the walker cannot read the run at all, so there is no ratio
    // to answer for.
    const blind = formatContrastReport(
      compare(
        {},
        runsOf(run({ faint: true, unjudged: 'a background image or gradient sits between the run and its ground' }))
      )
    )
    assert.match(blind, /Runs the check cannot measure/)
    assert.doesNotMatch(blind, /Do not deepen the token/)
    // Tagged and measured, reported only because a sibling of the same selector
    // sits on a picture: the ratio itself clears the floor.
    const sibling = formatContrastReport(compare({}, withHidden(1, { faint: true, ratio: 6.1 })))
    assert.match(sibling, /cannot read/)
    assert.doesNotMatch(sibling, /Do not deepen the token/)
  })

  it('says it once where a key is below its floor and partly unreadable', () => {
    // The readable instance is below its floor and a sibling of the same
    // selector sits on a picture, so compare() reports the key under both
    // headings. Only the first is a contrast failure: repainting the words as
    // the fix says leaves the gate red, which is what the second heading is
    // about.
    const report = formatContrastReport(compare({}, withHidden(1, { faint: true })))
    const unmeasurable = report.indexOf('Runs the check cannot measure:')
    assert.ok(unmeasurable > -1, 'the key is also reported as one the check cannot measure')
    assert.match(report.slice(0, unmeasurable), /Do not deepen the token/)
    assert.doesNotMatch(report.slice(unmeasurable), /Do not deepen the token/)
  })

  it('hands a key reported only for the instance it cannot read no colour fix', () => {
    // Frozen at the ratio it still measures, and an instance of the key has
    // since appeared on a gradient. Nothing left in the report is a contrast
    // failure, so there is nothing for a colour fix to answer.
    const result = compare(frozen(4.33), withHidden(1, { faint: true }))
    assert.equal(result.added.length + result.worse.length, 0)
    const report = formatContrastReport(result)
    assert.match(report, /Runs the check cannot measure:/)
    assert.match(report, /cannot read/)
    assert.doesNotMatch(report, /Do not deepen the token/)
  })
})

describe('tightenedBaseline', () => {
  it('keeps a frozen run that is still below its floor, at the worse of the two ratios', () => {
    assert.deepEqual(tightenedBaseline(frozen(4.33), runsOf(run({ ratio: 4.2 }))), {
      [KEY]: { ratio: 4.2, need: 4.5, unreadable: 0, unjudged: '', sample: '16:20' },
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
      [KEY]: { ratio: 4.33, need: 4.5, unreadable: 0, unjudged: '', sample: '16:20' },
    })
  })

  it('keeps a key that clears its floor while part of it is still unreadable', () => {
    // The half of a mixed key that can be measured is not the half the baseline
    // has to answer for. Dropping the entry on the floor alone would take the
    // count of the instances behind the picture with it, and the next run would
    // find a new key with unreadable instances and nothing that remembered them.
    const worst = withHidden(2, { selector: 'p', path: '/solutions', ratio: 6.12, need: 4.5 })
    const next = tightenedBaseline({}, worst, { freezeNew: true })
    assert.deepEqual(next, {
      '/solutions|light|p|': { ratio: 6.12, need: 4.5, unreadable: 2, unjudged: '', sample: '16:20' },
    })
    assert.equal(compare(next, worst).ok, true, 'and what it wrote is a fixed point')
  })

  it('drops that key once the walker can read all of it', () => {
    const baseline = { [KEY]: { ratio: 6.12, need: 4.5, unreadable: 2, unjudged: '', sample: '众智之所为' } }
    assert.deepEqual(tightenedBaseline(baseline, runsOf(run({ ratio: 6.12, need: 4.5 }))), {})
  })

  it('records the ratio a below-floor key reached once part of it is readable again', () => {
    // Frozen below its floor while carrying instances nobody could read. The
    // clamp would pin it at the old 4.33 forever, because `Math.min` only knows
    // the worse of the two — but the page now measures 6.12, and the entry stays
    // only for the one instance still behind the picture. So the new ratio is
    // what belongs there, and the run after it is a no-op rather than a key the
    // update chases in circles.
    const baseline = { [KEY]: { ratio: 4.33, need: 4.5, unreadable: 2, unjudged: '', sample: '16:20' } }
    const worst = withHidden(1, { ratio: 6.12, need: 4.5 })
    const next = tightenedBaseline(baseline, worst)
    assert.equal(next[KEY].ratio, 6.12)
    assert.equal(next[KEY].unreadable, 1)
    assert.equal(compare(next, worst).ok, true, 'and what it wrote is a fixed point')
  })

  it('refuses to freeze an unmeasurable run the baseline has never seen either', () => {
    // Being unmeasurable is not a reason to let a new key in without the flag:
    // `compare` fails on it, so an update that wrote it in would launder the
    // failure the gate had just reported.
    const runs = runsOf(run({ unjudged: 'a gradient is behind it' }))
    assert.deepEqual(tightenedBaseline({}, runs), {})
    assert.equal(compare({}, runs).ok, false, 'which the plain run reports as a failure')
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
    const next = tightenedBaseline(
      { [KEY]: { ratio: 2.65, need: 3, unjudged: '', sample: '开始使用' } },
      runsOf(run({ ratio: 1.4, need: 3 }))
    )
    assert.equal(next[KEY].ratio, 1.4)
    assert.equal(compare(next, runsOf(run({ ratio: 2.0, need: 3 }))).ok, true, 'still worse than the new baseline')
  })

  it('keeps a frozen run at the worse ratio when it improves but is still below its floor', () => {
    // The one case where the clamp has something to do. Frozen 2.65, measured
    // 2.9: an improvement, so `compare` is green and the very next person runs
    // the update — which must not write 2.9 over the 2.65 and quietly let the
    // run creep towards its floor one acceptable step at a time.
    const baseline = { [KEY]: { ratio: 2.65, need: 3, unjudged: '', sample: '开始使用' } }
    const runs = runsOf(run({ ratio: 2.9, need: 3 }))
    assert.equal(compare(baseline, runs).ok, true, 'green, so an update is what happens next')
    assert.equal(tightenedBaseline(baseline, runs)[KEY].ratio, 2.65)
  })

  it('is a fixed point: updating a passing tree changes nothing', () => {
    const baseline = {
      [KEY]: entryFor(run({})),
      [KEY2]: entryFor({
        path: '/solutions',
        theme: 'light',
        selector: 'i.mdi-email-outline.mdi',
        pseudo: '::before',
        ratio: 2.65,
        need: 3,
        icon: true,
        unjudged: '',
        text: '',
      }),
    }
    const runs = runsOf(run({}), {
      path: '/solutions',
      theme: 'light',
      selector: 'i.mdi-email-outline.mdi',
      pseudo: '::before',
      ratio: 2.65,
      need: 3,
      icon: true,
      unjudged: '',
      text: '',
    })
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
