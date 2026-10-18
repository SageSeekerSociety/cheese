import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { compare, formatReport, parseTscDiagnostics, tightenedBaseline } from './tsc-ratchet-core.mjs'

const SAMPLE = `
src/views/spaces/detail/PublishTask.vue(50,3): error TS2322: Type 'x' is not assignable.
  Types of property 'categoryId' are incompatible.
    Type 'undefined' is not assignable to type 'number'.
src/views/spaces/detail/PublishTask.vue(61,9): error TS18048: 'a' is possibly 'undefined'.
src/plugins/tiptap/index.ts(12,1): error TS2552: Cannot find name 'foo'.
`

test('clean output parses to no diagnostics', () => {
  assert.deepEqual(parseTscDiagnostics(''), [])
  assert.deepEqual(parseTscDiagnostics('all good\n'), [])
})

// An anchor line with nothing after the code's colon is still an error: a
// parser that required a message would silently drop it and pass the tree.
test('an anchor line with no message is still a diagnostic', () => {
  assert.deepEqual(parseTscDiagnostics('src/a.ts(1,1): error TS1005:\n'), [
    { file: 'src/a.ts', line: 1, column: 1, code: 'TS1005', message: '' },
  ])
})

// The parser keeps the anchor line whole — the file, the line, the column, the
// TS code and the message — so the failure report is a list of the actual
// errors rather than a count that sends people to grep.
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
  assert.deepEqual(parseTscDiagnostics(output), [
    {
      file: 'src/a.ts',
      line: 3,
      column: 2,
      code: 'TS2322',
      message: 'incompatible\n  first explanation\n    nested explanation',
    },
    { file: 'src/b.ts', line: 4, column: 5, code: 'TS2552', message: 'missing' },
  ])
})

test('the report prints every error at its file(line,col) with its explanation', () => {
  const text = formatReport(parseTscDiagnostics(SAMPLE))
  assert.match(text, /src\/views\/spaces\/detail\/PublishTask\.vue\(50,3\): error TS2322: Type 'x' is not assignable\./)
  assert.ok(
    text.includes(
      "  Types of property 'categoryId' are incompatible.\n    Type 'undefined' is not assignable to type 'number'."
    )
  )
  assert.match(text, /src\/plugins\/tiptap\/index\.ts\(12,1\): error TS2552: Cannot find name 'foo'\./)
  assert.match(text, /total: 3 error\(s\)/)
})

test('a missing PropType names the type-only import that fixes it', () => {
  const text = formatReport(parseTscDiagnostics("src/FooSub.ts(1,20): error TS2304: Cannot find name 'PropType'."))
  assert.ok(text.includes("import type { PropType } from 'vue'"))
})

// `compare` and `tightenedBaseline` are the per-file arithmetic the stylelint,
// i18n-comment and global-fallback ratchets import from here.
test('compare: a new or a raised count is a regression, a lowered one an improvement', () => {
  const result = compare({ 'a.vue': 2, 'c.vue': 2 }, { 'a.vue': 3, 'b.vue': 1, 'c.vue': 1 })
  assert.equal(result.ok, false)
  assert.deepEqual(result.regressions, [
    { file: 'a.vue', base: 2, now: 3 },
    { file: 'b.vue', base: 0, now: 1 },
  ])
  assert.deepEqual(result.improvements, [{ file: 'c.vue', base: 2, now: 1 }])
  assert.equal(compare({ 'a.vue': 2 }, { 'a.vue': 2 }).ok, true)
})

test('tightenedBaseline never raises a count and drops a file fixed to zero', () => {
  assert.deepEqual(tightenedBaseline({ 'a.vue': 1, 'b.vue': 2 }, { 'a.vue': 5 }), { 'a.vue': 1 })
})

// Exercise the actual CLI on a one-file project, never the whole frontend.
function withProject(source, check) {
  const dir = mkdtempSync(join(tmpdir(), 'tsc-check-'))
  try {
    const project = join(dir, 'tsconfig.json')
    writeFileSync(
      project,
      JSON.stringify({
        compilerOptions: { strict: true, noEmit: true, skipLibCheck: true, types: [], pretty: true },
        files: ['planted.ts'],
      })
    )
    writeFileSync(join(dir, 'planted.ts'), source)
    const cli = fileURLToPath(new URL('./tsc-ratchet.mjs', import.meta.url))
    check((...args) => {
      const run = spawnSync(process.execPath, [cli, '--project', project, ...args], {
        encoding: 'utf8',
        timeout: 30_000,
      })
      assert.ifError(run.error)
      return run
    })
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
}

test('CLI passes a project with no type error', () => {
  withProject('export const n: number = 1\n', (run) => {
    const plain = run()
    assert.equal(plain.status, 0, `${plain.stdout}\n${plain.stderr}`)
    const json = run('--json')
    assert.equal(json.status, 0, `${json.stdout}\n${json.stderr}`)
    assert.equal(JSON.parse(json.stdout).status, 'pass')
  })
})

// One error is enough: there is no allowance to stay within. A missing compiler
// (exit 2) must not pass as the expected rejection, which is why the status is
// checked to be exactly 1.
test('CLI fails on a single error and shows its code, location and full explanation', () => {
  const source = ["const source = { count: 'not a number' }", 'export const target: { count: number } = source'].join(
    '\n'
  )
  withProject(source, (run) => {
    for (const json of [false, true]) {
      const result = run(...(json ? ['--json'] : []))
      assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`)
      const report = json ? result.stderr : result.stdout
      assert.match(report, /planted\.ts\(2,14\): error TS2322:/)
      assert.ok(report.includes("Type '{ count: string; }' is not assignable to type '{ count: number; }'."))
      assert.ok(
        report.includes(
          "\n  Types of property 'count' are incompatible.\n    Type 'string' is not assignable to type 'number'."
        )
      )
      if (json) {
        assert.equal(result.stdout.trim().split('\n').length, 1)
        const record = JSON.parse(result.stdout)
        assert.equal(record.status, 'fail')
        assert.equal(record.actual, 1)
        assert.equal(record.frozen, 0)
        assert.doesNotMatch(result.stdout, /TS2322/)
      }
    }
  })
})

test('CLI fails on a missing type import and names the fix', () => {
  withProject('export type Value = PropType<string>\n', (run) => {
    const result = run()
    assert.equal(result.status, 1, `${result.stdout}\n${result.stderr}`)
    assert.ok(result.stdout.includes("import type { PropType } from 'vue'"))
  })
})
