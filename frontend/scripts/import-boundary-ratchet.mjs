#!/usr/bin/env node
// Runs the component boundary rule over src/components and fails on any
// violation.
//
//   node scripts/import-boundary-ratchet.mjs            check (exit 1 on any violation)
//   node scripts/import-boundary-ratchet.mjs --json     one JSON record on stdout, same exit code
//   node scripts/import-boundary-ratchet.mjs --files <path>   judge just those paths
//
// The rule itself is declared in import-boundary-ratchet-core.mjs (as an eslint
// rule object) and applied by eslint.boundary.config.mjs; the counting is a
// pure function there, unit-tested by import-boundary-ratchet.test.mjs. This
// file is only the I/O around them.
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { BOUNDARY_FILES, formatBoundaryReport, parseEslintJson } from './import-boundary-ratchet-core.mjs'
import { asJson, cannotJudge, emit, plainResult, verdict } from './ratchet-report.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CONFIG = resolve(ROOT, 'eslint.boundary.config.mjs')

function options(name) {
  return process.argv.flatMap((arg, i) => (arg === name ? [process.argv[i + 1]] : []))
}

const ID = 'fe-boundary'

// Resolve the binary explicitly rather than trusting PATH: `pnpm run` puts
// node_modules/.bin there but `node scripts/...` does not, and an eslint that
// is merely missing must not look like a clean tree.
const LOCAL_BIN = resolve(ROOT, 'node_modules/.bin/eslint')
const BIN = existsSync(LOCAL_BIN) ? LOCAL_BIN : 'eslint'

// The scope is the directories the config actually declares rules for. Asking
// eslint for a JSON report rather than parsing its stylish output keeps the
// counts independent of line wrapping and of anyone's terminal width.
// src/components/** -> src/components: the directories the rule is declared for.
const ROOTS = [...new Set(BOUNDARY_FILES.map((p) => p.slice(0, p.indexOf('/**'))))]
// `--files` narrows the scan to named paths. It exists so the self-test can
// judge one planted file without paying for the whole tree, and it is honest
// about what it did: the report below says how much was scanned.
const scan = options('--files')

const run = spawnSync(BIN, ['--config', CONFIG, '--format', 'json', '--no-color', ...(scan.length ? scan : ROOTS)], {
  cwd: ROOT,
  encoding: 'utf8',
  shell: process.platform === 'win32',
  maxBuffer: 64 * 1024 * 1024,
})

if (run.error) {
  console.error(`could not run eslint: ${run.error.message}`)
  console.error('run `pnpm install --frozen-lockfile` first')
  cannotJudge({ id: ID }, `could not run eslint: ${run.error.message}`)
}

const output = `${run.stdout ?? ''}${run.stderr ?? ''}`

let parsed
try {
  parsed = parseEslintJson(run.stdout ?? '', ROOT)
} catch (error) {
  // eslint exited non-zero without usable JSON: a bad config, a missing parser,
  // a crash. Never a pass, and never a violation either.
  console.error(`eslint did not report a usable JSON result: ${error.message}`)
  console.error(output.trim() || '(no output)')
  cannotJudge({ id: ID }, `eslint did not report a usable JSON result: ${error.message}`)
}

if (parsed.unjudged.length) {
  // eslint saw the file and could not read it — a syntax error, a missing
  // parser, a plugin that threw. Counting those as "no boundary violation" is
  // how a gate goes green over files it never parsed.
  console.error('eslint could not judge these files, so neither can this check:')
  for (const line of parsed.unjudged) console.error(`  ${line}`)
  cannotJudge({ id: ID }, `eslint could not judge ${parsed.unjudged.length} file(s)`)
}

if (run.status === 2) {
  console.error('eslint exited 2 (configuration or internal error):')
  console.error(output.trim() || '(no output)')
  cannotJudge({ id: ID }, 'eslint exited 2 (configuration or internal error)')
}

const result = plainResult(parsed.counts)
const scanned = (scan.length ? scan : ROOTS).join(', ')

if (asJson) {
  // Per-file counts, the shape the board groups by (docs/topics/棘轮页方案 3.1).
  // The report is not on stdout here — the collector parses stdout — but on
  // failure it goes to stderr, so a --json run still prints the line, the rule
  // and the fix.
  const details = Object.entries(parsed.counts).map(([file, count]) => ({ file, count }))
  emit(verdict({ id: ID, result, details }))
  if (!result.ok) console.error(formatBoundaryReport(parsed.violations))
  process.exit(result.ok ? 0 : 1)
}

console.log(formatBoundaryReport(parsed.violations))
console.log(`scanned ${scanned}`)
process.exit(result.ok ? 0 : 1)
