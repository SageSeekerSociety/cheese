import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  compare,
  DESIGN_RULES,
  formatReport,
  parseStylelintReport,
  tightenedBaseline,
  violationDetails,
} from './stylelint-ratchet-core.mjs'

const ROOT = '/repo/frontend'
const toRelativePath = (source) => source.replace(`${ROOT}/`, '')

/**
 * A stylelint --formatter json report. Mixes design rules with the ungated
 * noise (property order, class naming) that dominates this tree, because
 * separating the two is the whole job of parseStylelintReport.
 */
const SAMPLE = [
  {
    source: `${ROOT}/src/components/ChatPanel.vue`,
    warnings: [
      { line: 12, column: 3, rule: 'color-no-hex', text: 'Unexpected hex color "#757575"' },
      { line: 40, column: 5, rule: 'order/properties-order', text: 'Expected "color" to come before "background"' },
      { line: 58, column: 3, rule: 'declaration-property-value-allowed-list', text: 'Unexpected value "10px"' },
    ],
  },
  {
    source: `${ROOT}/src/views/OverviewView.vue`,
    warnings: [{ line: 7, column: 9, rule: 'color-named', text: 'Unexpected named color "white"' }],
  },
  {
    // A file whose only findings are ungated rules must not appear at all —
    // otherwise the baseline records a 0 that looks like a tracked file.
    source: `${ROOT}/src/views/Clean.vue`,
    warnings: [{ line: 1, column: 1, rule: 'selector-class-pattern', text: 'Expected kebab-case' }],
  },
  { source: `${ROOT}/src/views/Spotless.vue`, warnings: [] },
]

test('counts only design-system rules, per file', () => {
  assert.deepEqual(parseStylelintReport(SAMPLE, toRelativePath), {
    'src/components/ChatPanel.vue': 2,
    'src/views/OverviewView.vue': 1,
  })
})

test('a clean report parses to no violations', () => {
  assert.deepEqual(parseStylelintReport([], toRelativePath), {})
  assert.deepEqual(parseStylelintReport(undefined, toRelativePath), {})
})

test('entries without a source are skipped rather than crashing', () => {
  // stylelint emits these for stdin input and for some parse failures.
  const report = [{ warnings: [{ rule: 'color-no-hex', text: 'x' }] }, ...SAMPLE]
  assert.deepEqual(parseStylelintReport(report, toRelativePath), {
    'src/components/ChatPanel.vue': 2,
    'src/views/OverviewView.vue': 1,
  })
})

test('the gated rule set is the four design rules', () => {
  // Guards against widening the gate by accident: adding a rule here silently
  // turns 2842 pre-existing violations into blocking ones.
  assert.deepEqual([...DESIGN_RULES].sort(), [
    'color-named',
    'color-no-hex',
    'declaration-property-value-allowed-list',
    'declaration-property-value-disallowed-list',
  ])
})

test('a new violation in a previously clean file is a regression', () => {
  const result = compare({}, { 'src/views/New.vue': 1 })
  assert.equal(result.ok, false)
  assert.deepEqual(result.regressions, [{ file: 'src/views/New.vue', base: 0, now: 1 }])
})

test('staying at the frozen count passes', () => {
  const result = compare({ 'src/a.vue': 3 }, { 'src/a.vue': 3 })
  assert.equal(result.ok, true)
  assert.deepEqual(result.regressions, [])
})

test('going below the frozen count passes and is reported as an improvement', () => {
  const result = compare({ 'src/a.vue': 3 }, { 'src/a.vue': 1 })
  assert.equal(result.ok, true)
  assert.deepEqual(result.improvements, [{ file: 'src/a.vue', base: 3, now: 1 }])
})

test('the baseline can only be tightened, never loosened', () => {
  // An --update run made while a regression is present must not launder it in.
  const next = tightenedBaseline({ 'src/a.vue': 2 }, { 'src/a.vue': 9, 'src/b.vue': 1 })
  assert.deepEqual(next, { 'src/a.vue': 2, 'src/b.vue': 1 })
})

test('a file fixed to zero drops out of the baseline', () => {
  assert.deepEqual(tightenedBaseline({ 'src/a.vue': 2 }, {}), {})
})

test('violationDetails lists the offending lines for regressed files only', () => {
  const details = violationDetails(SAMPLE, toRelativePath, new Set(['src/components/ChatPanel.vue']))
  assert.deepEqual(details, [
    '  src/components/ChatPanel.vue:12:3  Unexpected hex color "#757575"',
    '  src/components/ChatPanel.vue:58:3  Unexpected value "10px"',
  ])
})

test('the failure report names the file, the counts and the fix', () => {
  const result = compare({}, { 'src/views/New.vue': 1 })
  const text = formatReport(result, violationDetails(SAMPLE, toRelativePath, new Set()))
  assert.match(text, /New design-token violations/)
  assert.match(text, /src\/views\/New\.vue: 0 -> 1/)
  assert.match(text, /docs\/design-system\.md/)
})

test('a clean run reports the totals and nothing alarming', () => {
  const text = formatReport(compare({ 'src/a.vue': 2 }, { 'src/a.vue': 2 }))
  assert.match(text, /total: 2 design-token violation\(s\), baseline allows 2/)
  assert.doesNotMatch(text, /New design-token violations/)
})
