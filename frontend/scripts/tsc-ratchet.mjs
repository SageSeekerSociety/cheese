#!/usr/bin/env node
// Runs `vue-tsc --noEmit` and fails on any error it reports.
//
//   node scripts/tsc-ratchet.mjs            check (exit 1 on any type error)
//   node scripts/tsc-ratchet.mjs --json     one JSON record on stdout, same exit code
//
// Failures print every diagnostic in full (to stderr with --json).
//
// `--project <tsconfig>` judges another project: the one-file fixtures the
// tests run instead of type-checking the whole tree a second time.
//
// The parsing lives in tsc-ratchet-core.mjs and is unit-tested; this file is
// only the I/O around it.
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { asJson, cannotJudge, emit, plainResult, verdict } from './ratchet-report.mjs'
import { formatReport, parseTscDiagnostics } from './tsc-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

const at = process.argv.indexOf('--project')
const project = at === -1 ? undefined : process.argv[at + 1]
const ID = 'vue-tsc'

// Resolve the binary explicitly rather than trusting PATH: `pnpm run` puts
// node_modules/.bin there but `node scripts/tsc-ratchet.mjs` does not, and a
// vue-tsc that is merely missing must not look like a clean type check.
const LOCAL_BIN = resolve(ROOT, 'node_modules/.bin/vue-tsc')
const BIN = existsSync(LOCAL_BIN) ? LOCAL_BIN : 'vue-tsc'

const run = spawnSync(BIN, ['--noEmit', '--pretty', 'false', ...(project ? ['-p', project] : [])], {
  cwd: ROOT,
  encoding: 'utf8',
  shell: process.platform === 'win32',
  maxBuffer: 64 * 1024 * 1024,
})

if (run.error) {
  console.error(`could not run vue-tsc: ${run.error.message}`)
  console.error('run `pnpm install --frozen-lockfile` first')
  cannotJudge({ id: ID }, `could not run vue-tsc: ${run.error.message}`)
}

const output = `${run.stdout ?? ''}${run.stderr ?? ''}`
const diagnostics = parseTscDiagnostics(output)

// A non-zero exit with NO parsed diagnostic is not a type error: a bad
// tsconfig, a missing dependency, a crash. It must not read as "clean" either,
// which is how a broken type check passes for free.
if (run.status !== 0 && diagnostics.length === 0) {
  console.error('vue-tsc failed without reporting any diagnostic:')
  console.error(output.trim() || '(no output)')
  if (run.signal) {
    console.error(
      `vue-tsc was killed by ${run.signal}, most likely out of memory: a whole-tree check needs more than a 3 GiB sandbox has.`
    )
    console.error("Leave the whole-tree check to the pull request's CI, which runs it on a runner with the memory.")
  }
  cannotJudge({ id: ID }, 'vue-tsc failed without reporting any diagnostic')
}

const counts = {}
for (const { file } of diagnostics) counts[file] = (counts[file] ?? 0) + 1
const result = plainResult(counts)

if (asJson) {
  // The human report is not on stdout here — the collector parses stdout — but
  // on failure it goes to stderr, so a --json run
  // still prints where and how to fix it.
  const details = Object.entries(counts).map(([file, count]) => ({ file, count }))
  emit(verdict({ id: ID, result, details }))
  if (!result.ok) console.error(formatReport(diagnostics))
  process.exit(result.ok ? 0 : 1)
}

console.log(formatReport(diagnostics))
process.exit(result.ok ? 0 : 1)
