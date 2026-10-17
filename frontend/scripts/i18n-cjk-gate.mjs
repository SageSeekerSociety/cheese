#!/usr/bin/env node
// Scans src/ for Chinese typed straight into templates and scripts. Exits 1 on
// any line that carries copy without an `i18n-data:` annotation, on any
// annotation that gives no reason or excuses nothing, and on any NEW Chinese
// HTML comment in a template. Rule: docs/i18n.md §5.
//
//   node scripts/i18n-cjk-gate.mjs            check (exit 1 on any new finding)
//   node scripts/i18n-cjk-gate.mjs --update   rewrite i18n-comments-baseline.json
//                                             downward; refuses to freeze a new
//                                             file unless --freeze-new is typed
//                                             too, and refuses to write at all
//                                             while a line is refused outright
//   node scripts/i18n-cjk-gate.mjs --update --freeze-new   accept new debt
//
// The scan itself lives in i18n-cjk-gate-core.mjs and is unit-tested; this file
// is only the I/O around it.
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  commentCounts,
  compare,
  formatCommentReport,
  formatReport,
  scanTree,
  shouldScan,
  splitFindings,
  tightenedBaseline,
} from './i18n-cjk-gate-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const SRC = resolve(ROOT, 'src')

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = resolve(dir, name)
    if (statSync(full).isDirectory()) {
      walk(full, out)
    } else if (shouldScan(relative(ROOT, full))) {
      out.push(full)
    }
  }
  return out
}

function option(name, fallback) {
  const at = process.argv.indexOf(name)
  return at === -1 ? fallback : process.argv[at + 1] ?? fallback
}

const update = process.argv.includes('--update')
// Accepting new debt is a decision someone makes on purpose, by typing this —
// the same guard the storage and a11y ratchets put on their own updates.
const freezeNew = process.argv.includes('--freeze-new')
const baselinePath = resolve(ROOT, option('--baseline', 'i18n-comments-baseline.json'))

const files = walk(SRC).map((full) => ({
  path: relative(ROOT, full).replaceAll('\\', '/'),
  text: readFileSync(full, 'utf8'),
}))

const result = scanTree(files)
// The two halves of a scan: what the gate refuses outright, and the commented
// Chinese that is counted against the baseline.
const { hard, comments } = splitFindings(result.problems)
const baseline = existsSync(baselinePath) ? JSON.parse(readFileSync(baselinePath, 'utf8')).files ?? {} : {}
const comparison = compare(baseline, commentCounts(comments))

if (update) {
  // A run that refuses a line must not exit 0 from its writing form: whoever
  // reached for --update to lower the count would take the zero for the tree
  // being fine. One line still carries its copy and its comment as one finding
  // (see checkSource), which also makes the count read low enough to look like
  // an improvement — so nothing is written while anything is refused.
  const hardFiles = Object.keys(hard)
  if (hardFiles.length) {
    console.error(formatReport({ ok: false, problems: hard, annotated: result.annotated }))
    console.error('')
    console.error(`${hardFiles.length} file(s) above are refused outright; the baseline was not`)
    console.error('rewritten. Fix those, then run --update again.')
    process.exit(1)
  }
  // Shrink-only, like every other ratchet here: `tightenedBaseline` never raises
  // a frozen count, but it does admit a file the baseline has not seen yet —
  // which is exactly where a new comment lands. New debt is a decision someone
  // makes on purpose, by typing --freeze-new.
  const regressed = new Set(comparison.regressions.map((r) => r.file))
  if (regressed.size && !freezeNew) {
    console.error(formatCommentReport({ ...comparison, improvements: [] }, comments))
    console.error('')
    console.error('--update only lowers the baseline; it wrote nothing. Fix the above, or')
    console.error('freeze them deliberately with --freeze-new.')
    process.exit(1)
  }
  if (freezeNew) {
    for (const { file, base, now } of comparison.regressions) console.log(`freezing ${file}: ${base} -> ${now}`)
  }
  const counts = commentCounts(comments)
  const next = freezeNew
    ? Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)))
    : tightenedBaseline(baseline, counts)
  writeFileSync(
    baselinePath,
    `${JSON.stringify(
      {
        _comment:
          'Frozen Chinese HTML comments in <template> blocks under src/. The ratchet blocks ' +
          'any NEW one; these are pre-existing and may only go down, by moving the ' +
          'explanation into the <script> or <style> block or writing it in English. ' +
          'Regenerate with `pnpm run lint:i18n:update`, which refuses to freeze a new file ' +
          'unless --freeze-new is typed too. The rule and its messages live in ' +
          'scripts/i18n-cjk-gate-core.mjs; why a comment counts at all is there too.',
        files: next,
      },
      null,
      2
    )}\n`
  )
  const total = Object.values(next).reduce((a, b) => a + b, 0)
  console.log(`baseline updated: ${total} comment(s) across ${Object.keys(next).length} file(s)`)
  process.exit(0)
}

const hardOk = Object.keys(hard).length === 0
console.log(formatReport({ ok: hardOk, problems: hard, annotated: result.annotated }))
console.log(formatCommentReport(comparison, comments))
process.exit(hardOk && comparison.ok ? 0 : 1)
