#!/usr/bin/env node
// Runs `vue-tsc --noEmit` and compares the result against tsc-baseline.json.
//
//   node scripts/tsc-ratchet.mjs            check (exit 1 on any new error)
//   node scripts/tsc-ratchet.mjs --update   rewrite the baseline downward
//   node scripts/tsc-ratchet.mjs --json     one JSON record on stdout, same exit code
//
// `--project <tsconfig>` and `--baseline <file>` judge another project against
// another baseline. They exist so ratchet-report.test.mjs can run this script
// on a one-file fixture instead of type-checking the whole tree a second time.
//
// The comparison logic lives in tsc-ratchet-core.mjs and is unit-tested; this
// file is only the I/O around it.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { asJson, cannotJudge, emit, verdict } from './ratchet-report.mjs'
import { compare, formatReport, parseTscOutput, tightenedBaseline } from './tsc-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

function options(name) {
  return process.argv.flatMap((arg, i) => (arg === name ? [process.argv[i + 1]] : []))
}

function option(name, fallback) {
  return options(name)[0] ?? fallback
}

const BASELINE = resolve(ROOT, option('--baseline', 'tsc-baseline.json'))
const project = option('--project')
const update = process.argv.includes('--update')
const ID = 'vue-tsc'

// Resolve the binary explicitly rather than trusting PATH: `pnpm run` puts
// node_modules/.bin there but `node scripts/tsc-ratchet.mjs` does not, and a
// vue-tsc that is merely missing must not look like a clean type check.
const LOCAL_BIN = resolve(ROOT, 'node_modules/.bin/vue-tsc')
const BIN = existsSync(LOCAL_BIN) ? LOCAL_BIN : 'vue-tsc'

const run = spawnSync(BIN, ['--noEmit', ...(project ? ['-p', project] : [])], {
  cwd: ROOT,
  encoding: 'utf8',
  shell: process.platform === 'win32',
})

if (run.error) {
  console.error(`could not run vue-tsc: ${run.error.message}`)
  console.error('run `pnpm install --frozen-lockfile` first')
  cannotJudge({ id: ID }, `could not run vue-tsc: ${run.error.message}`)
}

const output = `${run.stdout ?? ''}${run.stderr ?? ''}`
const current = parseTscOutput(output)
const currentTotal = Object.values(current).reduce((a, b) => a + b, 0)

// vue-tsc exits non-zero when it reports errors — expected while the baseline is
// non-empty. A non-zero exit with NO parsed diagnostics is something else
// entirely (bad tsconfig, missing dependency, a crash) and must not be read as
// "clean": that is exactly how a broken type check passes for free.
if (run.status !== 0 && currentTotal === 0) {
  console.error('vue-tsc failed without reporting any diagnostic:')
  console.error(output.trim() || '(no output)')
  cannotJudge({ id: ID }, 'vue-tsc failed without reporting any diagnostic')
}

const baseline = existsSync(BASELINE) ? JSON.parse(readFileSync(BASELINE, 'utf8')).files ?? {} : {}

if (update) {
  const next = tightenedBaseline(baseline, current)
  writeFileSync(
    BASELINE,
    `${JSON.stringify(
      {
        _comment:
          'Frozen vue-tsc errors. The ratchet blocks any NEW error; these are ' +
          'pre-existing and may only go down. Regenerate with `pnpm run typecheck:update`.',
        files: next,
      },
      null,
      2
    )}\n`
  )
  const total = Object.values(next).reduce((a, b) => a + b, 0)
  console.log(`baseline updated: ${total} error(s) across ${Object.keys(next).length} file(s)`)
  process.exit(0)
}

const result = compare(baseline, current)

if (asJson) {
  // Per-file counts of everything still over its frozen count, passing or not:
  // the board groups the debt by file, and the diagnostics have no line numbers
  // here that the counts do not already carry.
  const details = Object.entries(current)
    .filter(([, count]) => count > 0)
    .map(([file, count]) => ({ file, count }))
  emit(verdict({ id: ID, result, details }))
  process.exit(result.ok ? 0 : 1)
}

console.log(formatReport(result))
process.exit(result.ok ? 0 : 1)
