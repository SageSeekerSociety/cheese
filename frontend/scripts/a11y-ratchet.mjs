#!/usr/bin/env node
// Runs the accessibility rules over src/**/*.vue and compares the result
// against a11y-baseline.json.
//
//   node scripts/a11y-ratchet.mjs            check (exit 1 on any new violation)
//   node scripts/a11y-ratchet.mjs --update   rewrite the baseline downward; refuses
//                                            (exit 1, writes nothing) if anything is new
//   node scripts/a11y-ratchet.mjs --update --freeze-new
//                                            also freeze the new ones, listing each
//   node scripts/a11y-ratchet.mjs --json     one JSON record on stdout, same exit code
//
// `--files <path>` (repeatable) and `--baseline <file>` judge other files
// against another baseline; they exist so a11y-ratchet.test.mjs can judge a
// planted file without linting the whole tree or touching the committed
// baseline.
//
// The rules are applied by eslint.a11y.config.mjs; the counting and the
// ratchet arithmetic are pure functions in a11y-ratchet-core.mjs. This file is
// only the I/O around them, shaped like import-boundary-ratchet.mjs.
import { spawnSync } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  A11Y_TARGETS,
  compare,
  countsByRule,
  formatA11yReport,
  parseEslintJson,
  tightenedBaseline,
} from './a11y-ratchet-core.mjs'
import { asJson, cannotJudge, emit, verdict } from './ratchet-report.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CONFIG = resolve(ROOT, 'eslint.a11y.config.mjs')

function options(name) {
  return process.argv.flatMap((arg, i) => (arg === name ? [process.argv[i + 1]] : []))
}

function option(name, fallback) {
  return options(name)[0] ?? fallback
}

const update = process.argv.includes('--update')
const freezeNew = process.argv.includes('--freeze-new')
const ID = 'fe-a11y'
const baselinePath = resolve(ROOT, option('--baseline', 'a11y-baseline.json'))

// Resolve the binary explicitly rather than trusting PATH: `pnpm run` puts
// node_modules/.bin there but `node scripts/...` does not, and an eslint that
// is merely missing must not look like a clean tree.
const LOCAL_BIN = resolve(ROOT, 'node_modules/.bin/eslint')
const BIN = existsSync(LOCAL_BIN) ? LOCAL_BIN : 'eslint'

const scan = options('--files')
const targets = scan.length ? scan : A11Y_TARGETS

const run = spawnSync(BIN, ['--config', CONFIG, '--format', 'json', '--no-color', ...targets], {
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
  // eslint exited without usable JSON: a bad config, a missing plugin, a
  // crash. Never a pass, and never a violation either.
  console.error(`eslint did not report a usable JSON result: ${error.message}`)
  console.error(output.trim() || '(no output)')
  cannotJudge({ id: ID }, `eslint did not report a usable JSON result: ${error.message}`)
}

if (parsed.unjudged.length) {
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
const baseline = existsSync(baselinePath) ? JSON.parse(readFileSync(baselinePath, 'utf8')).files ?? {} : {}

const result = compare(baseline, current)

if (update) {
  // Shrink-only, like every other ratchet here. `tightenedBaseline`
  // never raises a frozen count, but it does admit a file the baseline has not
  // seen yet — which is exactly where a new violation lands. So an update that
  // would freeze anything new refuses and says what; accepting new debt is a
  // decision someone makes on purpose, by typing --freeze-new.
  if (result.regressions.length && !freezeNew) {
    const regressed = new Set(result.regressions.map((r) => r.file))
    console.error(
      formatA11yReport(
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
          'Frozen eslint-plugin-vuejs-accessibility violations per file. The ratchet ' +
          'blocks any NEW one; these are pre-existing and may only go down. ' +
          'Regenerate with `pnpm run lint:a11y:update`. The rules are in ' +
          'eslint.a11y.config.mjs.',
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

if (asJson) {
  const details = Object.entries(current)
    .filter(([, count]) => count > 0)
    .map(([file, count]) => ({ file, count }))
  emit(verdict({ id: ID, result, details }))
  process.exit(result.ok ? 0 : 1)
}

const regressed = new Set(result.regressions.map((r) => r.file))
console.log(
  formatA11yReport(
    result,
    parsed.violations.filter((v) => regressed.has(v.file))
  )
)
const byRule = Object.entries(countsByRule(parsed.violations))
  .map(([rule, n]) => `${rule.slice(rule.indexOf('/') + 1)} ${n}`)
  .join(', ')
console.log(`scanned ${targets.join(', ')}${byRule ? ` — by rule: ${byRule}` : ''}`)
process.exit(result.ok ? 0 : 1)
