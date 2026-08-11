#!/usr/bin/env node
// Runs `vue-tsc --noEmit` and compares the result against tsc-baseline.json.
//
//   node scripts/tsc-ratchet.mjs            check (exit 1 on any new error)
//   node scripts/tsc-ratchet.mjs --update   rewrite the baseline downward
//
// The comparison logic lives in tsc-ratchet-core.mjs and is unit-tested; this
// file is only the I/O around it.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { compare, formatReport, parseTscOutput, tightenedBaseline } from './tsc-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const BASELINE = resolve(ROOT, 'tsc-baseline.json')
const update = process.argv.includes('--update')

// Resolve the binary explicitly rather than trusting PATH: `pnpm run` puts
// node_modules/.bin there but `node scripts/tsc-ratchet.mjs` does not, and a
// vue-tsc that is merely missing must not look like a clean type check.
const LOCAL_BIN = resolve(ROOT, 'node_modules/.bin/vue-tsc')
const BIN = existsSync(LOCAL_BIN) ? LOCAL_BIN : 'vue-tsc'

const run = spawnSync(BIN, ['--noEmit'], {
  cwd: ROOT,
  encoding: 'utf8',
  shell: process.platform === 'win32',
})

if (run.error) {
  console.error(`could not run vue-tsc: ${run.error.message}`)
  console.error('run `pnpm install --frozen-lockfile` first')
  process.exit(2)
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
  process.exit(2)
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
console.log(formatReport(result))
process.exit(result.ok ? 0 : 1)
