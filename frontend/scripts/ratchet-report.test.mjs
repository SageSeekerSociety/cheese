// Tests for the --json contract the three frontend ratchets share. Run by
// `pnpm run test:ratchet` (node --test scripts/*.test.mjs), locally and in
// frontend.yml.
//
// The promise is narrow and deliberate: --json is a READ of a decision the
// checker has already made, so it must never move the exit code. Two ways that
// goes wrong, and both have a case here:
//
//   * a checker that wrote its record before comparing would exit 0 on a tree
//     it never judged — the failure the boundary plant below catches;
//   * a checker that could not run at all (missing binary, unreadable report)
//     must still exit 2, and must not print a record that reads like a verdict.
//
// Each real checker runs on a planted fixture, not on the whole tree: the
// frontend job already lints and type-checks the whole tree in its own steps,
// so judging it again here doubled the job's two longest steps. The fixtures
// carry a passing and a failing case for stylelint and a failing one for
// vue-tsc, and every record is held against the exit code the process really
// returned.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'

import { BETTER, verdict } from './ratchet-report.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

const sandbox = mkdtempSync(join(tmpdir(), 'ratchet-report-'))
after(() => rmSync(sandbox, { recursive: true, force: true }))

// ---------------------------------------------------------------------------
// 1. The record itself.
// ---------------------------------------------------------------------------

/** What compare() returns for a tree that went from 5 to 7, one file each way. */
const COMPARED = {
  ok: false,
  baselineTotal: 5,
  currentTotal: 7,
  regressions: [{ file: 'b.vue', base: 0, now: 3 }],
  improvements: [{ file: 'a.vue', base: 5, now: 4 }],
}

describe('verdict', () => {
  it('records the two totals as numbers and every stale allowance as a row', () => {
    assert.deepEqual(verdict({ id: 'fe-boundary', result: COMPARED }), {
      id: 'fe-boundary',
      better: BETTER,
      status: 'fail',
      actual: 7,
      frozen: 5,
      stale: [{ file: 'a.vue', frozen: 5, actual: 4 }],
      details: [],
    })
  })

  // The counters are numbers, not objects: the snapshot's shape is fixed in
  // docs/topics/棘轮页方案 (3.1), and a page that has to guess which of two
  // shapes it is reading is how "0" and "missing" start to look alike.
  it('judges a tree with no regression as pass', () => {
    const record = verdict({ id: 'x', result: { ...COMPARED, ok: true } })
    assert.equal(record.status, 'pass')
    assert.equal(record.actual, 7)
  })

  it('takes the better direction from the caller, for checks where more is better', () => {
    assert.equal(verdict({ id: 'x', better: 'up', result: COMPARED }).better, 'up')
  })

  it('caps details — one committed snapshot must not be the size of the tree', () => {
    const many = Array.from({ length: 500 }, (_, i) => ({ file: `f${i}.vue`, count: 1 }))
    assert.equal(verdict({ id: 'x', result: COMPARED, details: many }).details.length, 200)
  })
})

// ---------------------------------------------------------------------------
// 2. The failure path. Spawned, because `asJson` is read from argv once at
//    import: a test process that called emit() in-process would be testing the
//    runner's argv, not a checker's.
// ---------------------------------------------------------------------------

const FIXTURE = join(sandbox, 'cannot-judge.mjs')
writeFileSync(
  FIXTURE,
  `import { cannotJudge } from '${pathToFileURL(resolve(HERE, 'ratchet-report.mjs')).href}'\n` +
    `cannotJudge({ id: 'x' }, '  eslint is not installed  ')\n`
)

describe('cannotJudge', () => {
  const runFixture = (...args) => spawnSync(process.execPath, [FIXTURE, ...args], { cwd: ROOT, encoding: 'utf8' })

  it('exits 2 under --json, with a record that says why', () => {
    const { status, stdout, stderr } = runFixture('--json')
    assert.equal(status, 2, stderr)

    const lines = stdout.trim().split('\n')
    assert.equal(lines.length, 1, `expected exactly one line, got: ${stdout}`)
    assert.deepEqual(JSON.parse(lines[0]), {
      id: 'x',
      better: BETTER,
      status: 'cannot_judge',
      reason: 'eslint is not installed',
    })
  })

  // Without --json the default run is byte-for-byte what it was before: stdout
  // is empty and only the exit code carries the verdict.
  it('writes nothing on stdout without --json, and still exits 2', () => {
    const { status, stdout } = runFixture()
    assert.equal(status, 2)
    assert.equal(stdout, '')
  })
})

// ---------------------------------------------------------------------------
// 3. The real checkers, byte for byte: one JSON line, and an exit code the
//    record agrees with.
// ---------------------------------------------------------------------------

const runChecker = (script, ...args) =>
  spawnSync(process.execPath, [resolve(HERE, script), ...args], { cwd: ROOT, encoding: 'utf8' })

const STATUS_OF_EXIT = { 0: 'pass', 1: 'fail', 2: 'cannot_judge' }

/** The one record `script --json` printed, held against the code it exited with. */
function agreedRecord(script, id, ...args) {
  const run = runChecker(script, '--json', ...args)
  const lines = run.stdout.trim().split('\n')
  assert.equal(lines.length, 1, `expected exactly one JSON line: ${run.stdout}${run.stderr}`)

  const record = JSON.parse(lines[0])
  assert.equal(record.id, id)
  assert.equal(record.better, BETTER)
  assert.equal(
    record.status,
    STATUS_OF_EXIT[run.status],
    `exit ${run.status} but the record says ${record.status}: ${run.stderr}`
  )

  if (run.status === 2) assert.equal(typeof record.reason, 'string')
  else {
    assert.equal(typeof record.actual, 'number')
    assert.equal(typeof record.frozen, 'number')
    assert.ok(Array.isArray(record.stale) && Array.isArray(record.details))
  }
  return record
}

describe('--json on the real checkers', () => {
  // The plant, judged by the real ratchet against a scratch baseline: both modes
  // must reach the same verdict on a component that imports the API layer.
  it('agrees with the default mode on a planted boundary violation', () => {
    const probe = resolve(ROOT, 'src/components/__ratchetReportProbe.vue')
    const args = ['--files', 'src/components/__ratchetReportProbe.vue', '--baseline', join(sandbox, 'planted.json')]
    try {
      writeFileSync(
        probe,
        `<script setup lang="ts">\nimport { api } from '@/api'\nconst x = api\n</script>\n<template><div /></template>\n`
      )

      const plain = runChecker('import-boundary-ratchet.mjs', ...args)
      const json = runChecker('import-boundary-ratchet.mjs', '--json', ...args)

      assert.equal(plain.status, 1, `${plain.stdout}${plain.stderr}`)
      assert.equal(json.status, plain.status, `${json.stdout}${json.stderr}`)
      assert.equal(JSON.parse(json.stdout).status, 'fail')
    } finally {
      rmSync(probe, { force: true })
    }
  })

  // A baseline with nothing frozen in it, so any finding in a fixture is new.
  const emptyBaseline = join(sandbox, 'empty-baseline.json')
  writeFileSync(emptyBaseline, '{"files":{}}\n')

  it('stylelint-tokens: the record is the run, passing and failing', () => {
    const clean = join(sandbox, 'clean.css')
    const planted = join(sandbox, 'planted.css')
    writeFileSync(clean, 'a { margin: 0; }\n')
    writeFileSync(planted, 'a { color: #123456; }\n')
    const args = (file) => ['--files', file, '--baseline', emptyBaseline]

    assert.equal(agreedRecord('stylelint-ratchet.mjs', 'stylelint-tokens', ...args(clean)).status, 'pass')
    assert.equal(agreedRecord('stylelint-ratchet.mjs', 'stylelint-tokens', ...args(planted)).status, 'fail')
  })

  it('vue-tsc: the record is the run, on a planted type error', () => {
    const project = join(sandbox, 'tsc-fixture')
    mkdirSync(project)
    writeFileSync(
      join(project, 'tsconfig.json'),
      '{"compilerOptions":{"strict":true,"noEmit":true,"skipLibCheck":true,"types":[]},"files":["planted.ts"]}\n'
    )
    writeFileSync(join(project, 'planted.ts'), "export const n: number = 'not a number'\n")
    const args = ['--project', join(project, 'tsconfig.json'), '--baseline', emptyBaseline]

    assert.equal(agreedRecord('tsc-ratchet.mjs', 'vue-tsc', ...args).status, 'fail')
  })
})
