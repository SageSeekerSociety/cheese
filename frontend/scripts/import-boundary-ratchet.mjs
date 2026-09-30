#!/usr/bin/env node
// Runs the component boundary rule over src/components and compares the result
// against import-boundary-baseline.json.
//
//   node scripts/import-boundary-ratchet.mjs            check (exit 1 on any new violation)
//   node scripts/import-boundary-ratchet.mjs --update   rewrite the baseline downward
//   node scripts/import-boundary-ratchet.mjs --json     one JSON record on stdout, same exit code
//
// The rule itself is declared in import-boundary-ratchet-core.mjs (as an eslint
// rule object) and applied by eslint.boundary.config.mjs; the counting and the
// ratchet arithmetic are pure functions there, unit-tested by
// import-boundary-ratchet.test.mjs. This file is only the I/O around them.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  BOUNDARY_FILES,
  compare,
  formatBoundaryReport,
  parseEslintJson,
  tightenedBaseline,
} from './import-boundary-ratchet-core.mjs'
import { asJson, cannotJudge, emit, verdict } from './ratchet-report.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CONFIG = resolve(ROOT, 'eslint.boundary.config.mjs')

function options(name) {
  return process.argv.flatMap((arg, i) => (arg === name ? [process.argv[i + 1]] : []))
}

function option(name, fallback) {
  return options(name)[0] ?? fallback
}

const update = process.argv.includes('--update')
const ID = 'fe-boundary'
// Overridable so the self-test can judge a planted file against a throwaway
// baseline instead of editing the committed one.
const baselinePath = resolve(ROOT, option('--baseline', 'import-boundary-baseline.json'))

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

const current = parsed.counts
const currentTotal = Object.values(current).reduce((a, b) => a + b, 0)

const baseline = existsSync(baselinePath) ? JSON.parse(readFileSync(baselinePath, 'utf8')).files ?? {} : {}

if (update) {
  const next = tightenedBaseline(baseline, current)
  writeFileSync(
    baselinePath,
    `${JSON.stringify(
      {
        _comment:
          'Frozen src/components -> API layer / vue-router imports. The ratchet ' +
          'blocks any NEW violation; these are pre-existing and may only go down. ' +
          'Regenerate with `pnpm run lint:boundary:update`. The rule and its ' +
          'messages live in scripts/import-boundary-ratchet-core.mjs.',
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
const scanned = (scan.length ? scan : ROOTS).join(', ')

if (asJson) {
  // Per-file counts, not the violations themselves: eslint's JSON does not
  // carry a line number this check keeps, and a count per file is what the
  // board groups by anyway.
  const details = Object.entries(current)
    .filter(([, count]) => count > 0)
    .map(([file, count]) => ({ file, count }))
  emit(verdict({ id: ID, result, details }))
  process.exit(result.ok ? 0 : 1)
}

console.log(formatBoundaryReport(result))
console.log(`scanned ${scanned} — ${currentTotal} violation(s)`)
process.exit(result.ok ? 0 : 1)
