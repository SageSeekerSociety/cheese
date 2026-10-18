import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  DESIGN_RULES,
  formatReport,
  parseStylelintReport,
  syntaxErrors,
  violationDetails,
} from './stylelint-ratchet-core.mjs'
import { plainResult } from './ratchet-report.mjs'

const ROOT = '/repo/frontend'
const toRelativePath = (source) => source.replace(`${ROOT}/`, '')

/**
 * A stylelint --formatter json report. Mixes design rules with the ungated
 * noise (property order, radius, class naming) that dominates this tree, because
 * separating the two is the whole job of parseStylelintReport.
 */
const SAMPLE = [
  {
    source: `${ROOT}/src/components/ChatPanel.vue`,
    warnings: [
      { line: 12, column: 3, rule: 'color-no-hex', text: 'Unexpected hex color "#757575"' },
      { line: 40, column: 5, rule: 'order/properties-order', text: 'Expected "color" to come before "background"' },
      {
        line: 58,
        column: 3,
        rule: 'declaration-property-value-disallowed-list',
        text: 'Unexpected value "rgb(0 0 0)"',
      },
      { line: 61, column: 3, rule: 'declaration-property-value-allowed-list', text: 'Unexpected value "10px"' },
    ],
  },
  {
    source: `${ROOT}/src/views/OverviewView.vue`,
    warnings: [{ line: 7, column: 9, rule: 'color-named', text: 'Unexpected named color "white"' }],
  },
  {
    // A file whose only findings are ungated rules must not appear at all —
    // otherwise the report records a 0 that looks like a tracked file.
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

test('the gated rule set is the three colour rules', () => {
  // Formatting and naming remain outside the colour gate.
  assert.deepEqual([...DESIGN_RULES].sort(), [
    'color-named',
    'color-no-hex',
    'declaration-property-value-disallowed-list',
  ])
})

test('violationDetails lists the offending lines for selected files only', () => {
  const details = violationDetails(SAMPLE, toRelativePath, new Set(['src/components/ChatPanel.vue']))
  assert.deepEqual(details, [
    '  src/components/ChatPanel.vue:12:3  Unexpected hex color "#757575"',
    '  src/components/ChatPanel.vue:58:3  Unexpected value "rgb(0 0 0)"',
  ])
})

test('the failure report names the file, the count and the fix', () => {
  const result = plainResult({ 'src/views/OverviewView.vue': 1 })
  const text = formatReport(result, violationDetails(SAMPLE, toRelativePath, new Set(['src/views/OverviewView.vue'])))
  assert.match(text, /Design-token violations/)
  assert.match(text, /src\/views\/OverviewView.vue:7:9/)
  assert.match(text, /total: 1 design-token violation/)
  assert.match(text, /docs\/design-system\.md/)
})

test('a clean run reports the totals and nothing alarming', () => {
  const text = formatReport(plainResult({}))
  assert.match(text, /total: 0 design-token violation\(s\)/)
  assert.doesNotMatch(text, /Design-token violations/)
})

// 一个读不出来的文件贡献 0 条违规，对棘轮来说长得像「变好了」。这是真实发生过的：
// 一个没闭合的花括号带着全绿的测试、类型检查和 stylelint 进了提交。
test('a file stylelint could not parse is reported, not counted as clean', () => {
  const broken = [
    {
      source: '/repo/frontend/src/components/Room.vue',
      warnings: [{ line: 42, rule: 'CssSyntaxError', text: 'Unclosed block (CssSyntaxError)' }],
    },
  ]
  assert.deepEqual(parseStylelintReport(broken, toRelativePath), {})
  assert.deepEqual(syntaxErrors(broken, toRelativePath), [
    '  src/components/Room.vue:42 Unclosed block (CssSyntaxError)',
  ])
})

test('a tree that parses reports no syntax errors', () => {
  assert.deepEqual(syntaxErrors(SAMPLE, toRelativePath), [])
})
