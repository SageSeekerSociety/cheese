#!/usr/bin/env node
// Runs stylelint and compares the design-system rules against
// stylelint-baseline.json.
//
//   node scripts/stylelint-ratchet.mjs            check (exit 1 on any new violation)
//   node scripts/stylelint-ratchet.mjs --update   rewrite the baseline downward
//
// The comparison logic lives in stylelint-ratchet-core.mjs and is unit-tested;
// this file is only the I/O around it. Structure deliberately mirrors
// tsc-ratchet.mjs — including the two failure modes that matter:
//
//   * a stylelint that is merely MISSING must not look like a clean tree, and
//   * a non-zero exit with no parsed report is a crash, not "zero violations".
//
// It never passes --fix. A gate that rewrites the checkout can exit 0 on a
// violation it silently repaired; `pnpm run lint:style:fix` is the writing form.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  compare,
  formatReport,
  parseStylelintReport,
  tightenedBaseline,
  violationDetails,
} from './stylelint-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const BASELINE = resolve(ROOT, 'stylelint-baseline.json')
const update = process.argv.includes('--update')

const TARGETS = 'src/**/*.{vue,css,scss}'

// Resolve the binary explicitly rather than trusting PATH: `pnpm run` puts
// node_modules/.bin there but `node scripts/stylelint-ratchet.mjs` does not, and
// a stylelint that is merely missing must not read as a clean check.
const LOCAL_BIN = resolve(ROOT, 'node_modules/.bin/stylelint')
const BIN = existsSync(LOCAL_BIN) ? LOCAL_BIN : 'stylelint'

// --output-file rather than reading stdout: stylelint prints the report on a
// stream that varies with exit status, and a report interleaved with warnings
// (browserslist, deprecations) does not parse.
const REPORT_PATH = resolve(tmpdir(), `stylelint-ratchet-${process.pid}.json`)

const run = spawnSync(BIN, [TARGETS, '--formatter', 'json', '--output-file', REPORT_PATH], {
  cwd: ROOT,
  encoding: 'utf8',
  shell: process.platform === 'win32',
  maxBuffer: 64 * 1024 * 1024,
})

if (run.error) {
  console.error(`could not run stylelint: ${run.error.message}`)
  console.error('run `pnpm install --frozen-lockfile` first')
  process.exit(2)
}

let report
try {
  report = JSON.parse(readFileSync(REPORT_PATH, 'utf8'))
} catch (error) {
  // stylelint exits 2 for "lint problems found" AND for its own failures. With
  // no readable report we cannot tell a clean tree from a crash, and guessing
  // "clean" is exactly how a broken gate passes for free.
  console.error('stylelint produced no readable report:')
  console.error((run.stderr || run.stdout || '').trim() || `(no output) — ${error.message}`)
  process.exit(2)
} finally {
  try {
    unlinkSync(REPORT_PATH)
  } catch {
    // best-effort cleanup of a temp file; never a reason to fail the gate
  }
}

const toRelativePath = (source) => relative(ROOT, source)
const current = parseStylelintReport(report, toRelativePath)

const baseline = existsSync(BASELINE) ? JSON.parse(readFileSync(BASELINE, 'utf8')).files ?? {} : {}

if (update) {
  const next = tightenedBaseline(baseline, current)
  writeFileSync(
    BASELINE,
    `${JSON.stringify(
      {
        _comment:
          'Frozen design-token violations (hardcoded colours, off-ladder radii). The ' +
          'ratchet blocks any NEW one; these are pre-existing and may only go down. ' +
          'Regenerate with `pnpm run lint:style:update`. See docs/design-system.md.',
        files: next,
      },
      null,
      2
    )}\n`
  )
  const total = Object.values(next).reduce((a, b) => a + b, 0)
  console.log(`baseline updated: ${total} violation(s) across ${Object.keys(next).length} file(s)`)
  process.exit(0)
}

const result = compare(baseline, current)
const details = result.ok
  ? []
  : violationDetails(report, toRelativePath, new Set(result.regressions.map((r) => r.file)))
console.log(formatReport(result, details))
process.exit(result.ok ? 0 : 1)
