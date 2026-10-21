#!/usr/bin/env node
// Runs stylelint's design-token rules with zero tolerance.
//
//   node scripts/stylelint-ratchet.mjs            check (exit 1 on any violation)
//   node scripts/stylelint-ratchet.mjs --json     one JSON record on stdout, same exit code
//
// `--files <glob>` (repeatable) judges fixtures without linting the whole tree.
//
// Report parsing lives in stylelint-ratchet-core.mjs. Two failures must stay
// distinguishable from a clean tree:
//
//   * a stylelint that is merely MISSING must not look like a clean tree, and
//   * a non-zero exit with no parsed report is a crash, not "zero violations".
//
// It never passes --fix. A gate that rewrites the checkout can exit 0 on a
// violation it silently repaired; `pnpm run lint:style:fix` is the writing form.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { asJson, cannotJudge, emit, plainResult, verdict } from './ratchet-report.mjs'
import { formatReport, parseStylelintReport, syntaxErrors, violationDetails } from './stylelint-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')

function options(name) {
  return process.argv.flatMap((arg, i) => (arg === name ? [process.argv[i + 1]] : []))
}

const ID = 'stylelint-tokens'

for (let i = 2; i < process.argv.length; i++) {
  const arg = process.argv[i]
  if (arg === '--files' && process.argv[i + 1]) i++
  else if (arg !== '--json') cannotJudge({ id: ID }, `unknown option: ${arg}`)
}

// The docs site (docs/site) takes its colours from src/style.css too, so its one
// stylesheet is held to the same rules. It sits outside this directory, where
// stylelint would not find this config on its own; hence --config below.
const TARGETS = ['src/**/*.{vue,css,scss}', '../docs/site/src/style.css']

// Resolve the binary explicitly rather than trusting PATH: `pnpm run` puts
// node_modules/.bin there but `node scripts/stylelint-ratchet.mjs` does not, and
// a stylelint that is merely missing must not read as a clean check.
const LOCAL_BIN = resolve(ROOT, 'node_modules/.bin/stylelint')
const BIN = existsSync(LOCAL_BIN) ? LOCAL_BIN : 'stylelint'

// --output-file rather than reading stdout: stylelint prints the report on a
// stream that varies with exit status, and a report interleaved with warnings
// (browserslist, deprecations) does not parse.
const REPORT_PATH = resolve(tmpdir(), `stylelint-ratchet-${process.pid}.json`)

const targets = options('--files').length ? options('--files') : TARGETS

const run = spawnSync(
  BIN,
  [...targets, '--config', 'stylelint.config.cjs', '--formatter', 'json', '--output-file', REPORT_PATH],
  {
    cwd: ROOT,
    encoding: 'utf8',
    shell: process.platform === 'win32',
    maxBuffer: 64 * 1024 * 1024,
  }
)

if (run.error) {
  console.error(`could not run stylelint: ${run.error.message}`)
  console.error('run `pnpm install --frozen-lockfile` first')
  cannotJudge({ id: ID }, `could not run stylelint: ${run.error.message}`)
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
  cannotJudge({ id: ID }, `stylelint produced no readable report: ${error.message}`)
} finally {
  try {
    unlinkSync(REPORT_PATH)
  } catch {
    // best-effort cleanup of a temp file; never a reason to fail the gate
  }
}

const toRelativePath = (source) => relative(ROOT, source)

// Before counting anything: a file stylelint could not parse counts as zero
// violations, which looks clean. Stop here instead.
const broken = syntaxErrors(report, toRelativePath)
if (broken.length) {
  console.error('stylelint could not parse these files:')
  console.error(broken.join('\n'))
  cannotJudge({ id: ID }, `stylelint could not parse ${broken.length} file(s)`)
}

const current = parseStylelintReport(report, toRelativePath)

const result = plainResult(current)

if (asJson) {
  // Keep the board's per-file breakdown; a plain check has frozen=0.
  const details = Object.entries(current)
    .filter(([, count]) => count > 0)
    .map(([file, count]) => ({ file, count }))
  emit(verdict({ id: ID, result, details }))
  process.exit(result.ok ? 0 : 1)
}

const details = violationDetails(report, toRelativePath, new Set(Object.keys(current)))
console.log(formatReport(result, details))
process.exit(result.ok ? 0 : 1)
