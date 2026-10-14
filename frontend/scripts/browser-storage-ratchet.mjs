#!/usr/bin/env node
// Runs the direct-Web-Storage rule over src/ and compares the result against
// browser-storage-baseline.json.
//
//   node scripts/browser-storage-ratchet.mjs            check (exit 1 on any new reference)
//   node scripts/browser-storage-ratchet.mjs --update   rewrite the baseline downward;
//                                                       refuses to freeze a new file
//                                                       unless --freeze-new is typed too
//   node scripts/browser-storage-ratchet.mjs --update --freeze-new   accept new debt
//   node scripts/browser-storage-ratchet.mjs --json     one JSON record on stdout, same exit code
//   node scripts/browser-storage-ratchet.mjs --files <path>   judge just those paths
//
// The rule itself is declared in browser-storage-ratchet-core.mjs (as an eslint
// rule object) and applied by eslint.browser-storage.config.mjs; the counting
// and the ratchet arithmetic are pure functions there, unit-tested by
// browser-storage-ratchet.test.mjs. This file is only the I/O around them.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  compare,
  formatStorageReport,
  parseStorageEslintJson,
  STORAGE_FILES,
  tightenedBaseline,
} from './browser-storage-ratchet-core.mjs'
import { asJson, cannotJudge, emit, verdict } from './ratchet-report.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CONFIG = resolve(ROOT, 'eslint.browser-storage.config.mjs')

function options(name) {
  return process.argv.flatMap((arg, i) => (arg === name ? [process.argv[i + 1]] : []))
}

function option(name, fallback) {
  return options(name)[0] ?? fallback
}

const update = process.argv.includes('--update')
const freezeNew = process.argv.includes('--freeze-new')
const ID = 'fe-storage'
// Overridable so the self-test can judge a planted file against a throwaway
// baseline instead of editing the committed one.
const baselinePath = resolve(ROOT, option('--baseline', 'browser-storage-baseline.json'))

// Resolve the binary explicitly rather than trusting PATH: `pnpm run` puts
// node_modules/.bin there but `node scripts/...` does not, and an eslint that is
// merely missing must not look like a clean tree.
const LOCAL_BIN = resolve(ROOT, 'node_modules/.bin/eslint')
const BIN = existsSync(LOCAL_BIN) ? LOCAL_BIN : 'eslint'

// The scope is what the config declares rules for. Asking eslint for a JSON
// report rather than parsing its stylish output keeps the counts independent of
// line wrapping and of anyone's terminal width.
// src/**/*.{vue,ts,...} -> src: the directory the rule is declared for.
const ROOTS = [...new Set(STORAGE_FILES.map((p) => p.slice(0, p.indexOf('/**'))))]
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
  parsed = parseStorageEslintJson(run.stdout ?? '', ROOT)
} catch (error) {
  // eslint exited non-zero without usable JSON: a bad config, a missing parser,
  // a crash. Never a pass, and never a violation either.
  console.error(`eslint did not report a usable JSON result: ${error.message}`)
  console.error(output.trim() || '(no output)')
  cannotJudge({ id: ID }, `eslint did not report a usable JSON result: ${error.message}`)
}

if (parsed.unjudged.length) {
  // eslint saw the file and could not read it — a syntax error, a missing
  // parser, a plugin that threw. Counting those as "no direct storage" is how a
  // gate goes green over files it never parsed.
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

const result = compare(baseline, current)

if (update) {
  // Shrink-only, like the a11y ratchet and backend/scripts/boundary_baseline.py.
  // `tightenedBaseline` never raises a frozen count, but it does admit a file the
  // baseline has not seen yet — which is exactly where a new reference lands. So
  // an update that would freeze anything new refuses and says what; accepting new
  // debt is a decision someone makes on purpose, by typing --freeze-new.
  const regressed = new Set(result.regressions.map((r) => r.file))
  if (regressed.size && !freezeNew) {
    console.error(
      formatStorageReport(
        { ...result, improvements: [] },
        parsed.violations.filter((v) => regressed.has(v.file))
      )
    )
    console.error('')
    console.error('--update only lowers the baseline; it wrote nothing. Fix the above, or')
    console.error('freeze them deliberately with --freeze-new.')
    process.exit(1)
  }
  if (freezeNew) {
    for (const { file, base, now } of result.regressions) console.log(`freezing ${file}: ${base} -> ${now}`)
  }
  const next = freezeNew
    ? Object.fromEntries(Object.entries(current).sort(([a], [b]) => a.localeCompare(b)))
    : tightenedBaseline(baseline, current)
  writeFileSync(
    baselinePath,
    `${JSON.stringify(
      {
        _comment:
          'Frozen direct localStorage/sessionStorage references under src/. ' +
          'The ratchet blocks any NEW one; these are pre-existing and may only ' +
          'go down, by importing from src/lib/browserStorage.ts instead. ' +
          'Regenerate with `pnpm run lint:storage:update`, which refuses to ' +
          'freeze a new file unless --freeze-new is typed too. The rule and its ' +
          'messages live in scripts/browser-storage-ratchet-core.mjs.',
        files: next,
      },
      null,
      2
    )}\n`
  )
  const total = Object.values(next).reduce((a, b) => a + b, 0)
  console.log(`baseline updated: ${total} reference(s) across ${Object.keys(next).length} file(s)`)
  process.exit(0)
}

const scanned = (scan.length ? scan : ROOTS).join(', ')
// Only the files the ratchet is blocking: listing every frozen reference in the
// tree would bury the new one the report is about.
const regressed = new Set(result.regressions.map((r) => r.file))
const failureReport = formatStorageReport(
  result,
  parsed.violations.filter((v) => regressed.has(v.file))
)

if (asJson) {
  // Per-file counts, the shape the board groups by (docs/topics/棘轮页方案 3.1).
  // The report is not on stdout here — the collector parses stdout — but on
  // failure it goes to stderr, so a --json run still prints the line, the rule
  // and the fix.
  const details = Object.entries(current)
    .filter(([, count]) => count > 0)
    .map(([file, count]) => ({ file, count }))
  emit(verdict({ id: ID, result, details }))
  if (!result.ok) console.error(failureReport)
  process.exit(result.ok ? 0 : 1)
}

console.log(failureReport)
console.log(`scanned ${scanned} — ${currentTotal} reference(s)`)
process.exit(result.ok ? 0 : 1)
