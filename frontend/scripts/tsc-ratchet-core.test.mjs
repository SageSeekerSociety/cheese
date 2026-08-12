import assert from 'node:assert/strict'
import { test } from 'node:test'

import { compare, formatReport, parseTscOutput, tightenedBaseline } from './tsc-ratchet-core.mjs'

const SAMPLE = `
src/views/spaces/detail/PublishTask.vue(50,3): error TS2322: Type 'x' is not assignable.
  Types of property 'categoryId' are incompatible.
    Type 'undefined' is not assignable to type 'number'.
src/views/spaces/detail/PublishTask.vue(61,9): error TS18048: 'a' is possibly 'undefined'.
src/plugins/tiptap/index.ts(12,1): error TS2552: Cannot find name 'foo'.
`

test('counts one error per anchor line, not per continuation line', () => {
  assert.deepEqual(parseTscOutput(SAMPLE), {
    'src/views/spaces/detail/PublishTask.vue': 2,
    'src/plugins/tiptap/index.ts': 1,
  })
})

test('clean output parses to no errors', () => {
  assert.deepEqual(parseTscOutput(''), {})
  assert.deepEqual(parseTscOutput('all good\n'), {})
})

test('a new error in a previously clean file is a regression', () => {
  const result = compare({ 'a.vue': 1 }, { 'a.vue': 1, 'b.vue': 1 })
  assert.equal(result.ok, false)
  assert.deepEqual(result.regressions, [{ file: 'b.vue', base: 0, now: 1 }])
})

test('more errors in an already-failing file is a regression', () => {
  const result = compare({ 'a.vue': 1 }, { 'a.vue': 2 })
  assert.equal(result.ok, false)
  assert.deepEqual(result.regressions, [{ file: 'a.vue', base: 1, now: 2 }])
})

test('staying at the baseline passes', () => {
  assert.equal(compare({ 'a.vue': 2 }, { 'a.vue': 2 }).ok, true)
})

test('fixing errors passes and is reported as an improvement', () => {
  const result = compare({ 'a.vue': 2 }, { 'a.vue': 1 })
  assert.equal(result.ok, true)
  assert.deepEqual(result.improvements, [{ file: 'a.vue', base: 2, now: 1 }])
})

test('a file fixed to zero drops out of the tightened baseline', () => {
  assert.deepEqual(tightenedBaseline({ 'a.vue': 2, 'b.vue': 1 }, { 'b.vue': 1 }), {
    'b.vue': 1,
  })
})

// The failure mode this guards: `--update` right after adding errors would
// otherwise rewrite the baseline upward and silently bless the regression.
test('tightening never raises a count', () => {
  assert.deepEqual(tightenedBaseline({ 'a.vue': 1 }, { 'a.vue': 5 }), { 'a.vue': 1 })
})

test('the report names the offending file and both counts', () => {
  const text = formatReport(compare({ 'a.vue': 1 }, { 'a.vue': 3 }))
  assert.match(text, /a\.vue: 1 -> 3/)
  assert.match(text, /total: 3 error\(s\), baseline allows 1/)
})
