import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { compare, formatReport, parseTscDiagnostics, parseTscOutput, tightenedBaseline } from './tsc-ratchet-core.mjs'

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

// The counting regex is untouched by the diagnostics parser: an anchor line
// with nothing after the code's colon still counts, and its message is empty.
// (Making the message mandatory in ERROR_LINE would silently drop such a line
// from the count — the judgement this keeps.)
test('an anchor line with no message still counts', () => {
  assert.deepEqual(parseTscOutput('src/a.ts(1,1): error TS1005:\n'), { 'src/a.ts': 1 })
  assert.deepEqual(parseTscDiagnostics('src/a.ts(1,1): error TS1005:\n'), [
    { file: 'src/a.ts', line: 1, column: 1, code: 'TS1005', message: '' },
  ])
})

// The diagnostics parser keeps the anchor line whole — the file, the line, the
// column, the TS code and the message — so the failure report can be a list of
// the actual errors rather than a count that sends people to grep.
test('parseTscDiagnostics keeps each anchor line whole', () => {
  assert.deepEqual(parseTscDiagnostics(SAMPLE), [
    {
      file: 'src/views/spaces/detail/PublishTask.vue',
      line: 50,
      column: 3,
      code: 'TS2322',
      message:
        "Type 'x' is not assignable.\n  Types of property 'categoryId' are incompatible.\n    Type 'undefined' is not assignable to type 'number'.",
    },
    {
      file: 'src/views/spaces/detail/PublishTask.vue',
      line: 61,
      column: 9,
      code: 'TS18048',
      message: "'a' is possibly 'undefined'.",
    },
    {
      file: 'src/plugins/tiptap/index.ts',
      line: 12,
      column: 1,
      code: 'TS2552',
      message: "Cannot find name 'foo'.",
    },
  ])
})

// The failure this guards: a bare `PublishTask.vue: 1 -> 2` sends the reader to
// grep, which finds the pre-existing error on line 50 as well as the new one on
// line 61. Printing the raw vue-tsc `file(line,col): error TSxxxx:` lines is
// what lets them tell the two apart without a second run.
test('the report prints the raw vue-tsc file(line,col) errors for a regressed file', () => {
  // One error frozen already, two in the tree now: PublishTask.vue regressed.
  const baseline = { 'src/views/spaces/detail/PublishTask.vue': 1 }
  const current = parseTscOutput(SAMPLE)
  const text = formatReport(compare(baseline, current), parseTscDiagnostics(SAMPLE))

  assert.match(text, /src\/views\/spaces\/detail\/PublishTask\.vue\(50,3\): error TS2322: Type 'x' is not assignable\./)
  assert.match(
    text,
    /src\/views\/spaces\/detail\/PublishTask\.vue\(61,9\): error TS18048: 'a' is possibly 'undefined'\./
  )
  assert.ok(
    text.includes(
      "  Types of property 'categoryId' are incompatible.\n    Type 'undefined' is not assignable to type 'number'."
    )
  )
  // The new error in a previously clean file is listed too.
  assert.match(text, /src\/plugins\/tiptap\/index\.ts\(12,1\): error TS2552: Cannot find name 'foo'\./)
  // And the fix points at the doc chapter, not only at the counts.
  assert.match(text, /docs\/manual\/dev\/frontend\.md/)
})

// A diagnostic in a file that did NOT regress is not this run's problem, so it
// is not printed — the report lists the files the ratchet blocks, no wider.
test('the report lists only the diagnostics in the files that regressed', () => {
  // PublishTask.vue is at its frozen 2 (no regression); tiptap/index.ts is new.
  const baseline = { 'src/views/spaces/detail/PublishTask.vue': 2 }
  const text = formatReport(compare(baseline, parseTscOutput(SAMPLE)), parseTscDiagnostics(SAMPLE))
  assert.match(text, /src\/plugins\/tiptap\/index\.ts\(12,1\): error TS2552/)
  assert.doesNotMatch(text, /error TS2322/)
  assert.doesNotMatch(text, /categoryId/)
})

test('diagnostic explanations end at a blank or an unindented line', () => {
  const output = [
    '  unrelated preamble',
    'src\\a.ts(3,2): error TS2322: incompatible',
    '  first explanation',
    '    nested explanation',
    '',
    '  unrelated footer',
    'src/b.ts(4,5): error TS2552: missing',
    'compiler summary',
    '  unrelated summary detail',
  ].join('\r\n')
  const diagnostics = parseTscDiagnostics(output)
  assert.deepEqual(diagnostics, [
    {
      file: 'src/a.ts',
      line: 3,
      column: 2,
      code: 'TS2322',
      message: 'incompatible\n  first explanation\n    nested explanation',
    },
    { file: 'src/b.ts', line: 4, column: 5, code: 'TS2552', message: 'missing' },
  ])
  assert.deepEqual(parseTscOutput(output), { 'src/a.ts': 1, 'src/b.ts': 1 })
})

// Exercise the actual CLI with one intentionally invalid file, never the whole
// frontend. A missing compiler (exit 2) must not pass as the expected rejection.
test('CLI failures show the TS code, location and full explanation in both output modes', () => {
  const dir = mkdtempSync(join(tmpdir(), 'tsc-ratchet-diagnostics-'))
  try {
    const project = join(dir, 'tsconfig.json')
    const baseline = join(dir, 'baseline.json')
    const frozen = `${JSON.stringify({ files: {} })}\n`
    writeFileSync(baseline, frozen)
    writeFileSync(
      project,
      JSON.stringify({
        compilerOptions: { strict: true, noEmit: true, skipLibCheck: true, types: [], pretty: true },
        files: ['planted.ts'],
      })
    )
    writeFileSync(
      join(dir, 'planted.ts'),
      ["const source = { count: 'not a number' }", 'export const target: { count: number } = source'].join('\n')
    )
    const cli = fileURLToPath(new URL('./tsc-ratchet.mjs', import.meta.url))
    for (const json of [false, true]) {
      const run = spawnSync(
        process.execPath,
        [cli, '--project', project, '--baseline', baseline, ...(json ? ['--json'] : [])],
        { encoding: 'utf8', timeout: 30_000 }
      )
      assert.ifError(run.error)
      assert.equal(run.status, 1, `${run.stdout}\n${run.stderr}`)
      const report = json ? run.stderr : run.stdout
      assert.match(report, /planted\.ts\(2,14\): error TS2322:/)
      assert.ok(report.includes("Type '{ count: string; }' is not assignable to type '{ count: number; }'."))
      assert.ok(
        report.includes(
          "\n  Types of property 'count' are incompatible.\n    Type 'string' is not assignable to type 'number'."
        )
      )
      if (json) {
        assert.equal(run.stdout.trim().split('\n').length, 1)
        assert.equal(JSON.parse(run.stdout).status, 'fail')
        assert.doesNotMatch(run.stdout, /TS2322/)
      }
    }
    assert.equal(readFileSync(baseline, 'utf8'), frozen)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
