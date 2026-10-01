#!/usr/bin/env node
// Scans src/ for Chinese typed straight into templates and scripts, and
// compares each file against i18n-cjk-baseline.json.
//
//   node scripts/i18n-cjk-ratchet.mjs            check (exit 1 on any new Chinese)
//   node scripts/i18n-cjk-ratchet.mjs --update   rewrite the baseline downward
//
// The scan itself lives in i18n-cjk-ratchet-core.mjs and is unit-tested; this
// file is only the I/O around it. Structure deliberately mirrors
// tsc-ratchet.mjs and stylelint-ratchet.mjs — including the failure mode that
// matters: the tree is read with the same filters the baseline was written
// with, so "I forgot to scan that directory" cannot read as "no Chinese there".
//
// It never writes to src/. `--update` only rewrites the baseline.
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { compare, formatReport, scanSource, scanTree, shouldScan, tightenedBaseline } from './i18n-cjk-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const SRC = resolve(ROOT, 'src')
const BASELINE = resolve(ROOT, 'i18n-cjk-baseline.json')
const update = process.argv.includes('--update')

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

const files = walk(SRC).map((full) => ({
  path: relative(ROOT, full).replaceAll('\\', '/'),
  text: readFileSync(full, 'utf8'),
}))

const current = scanTree(files)

const baseline = existsSync(BASELINE) ? JSON.parse(readFileSync(BASELINE, 'utf8')).files ?? {} : {}

if (update) {
  const next = tightenedBaseline(baseline, current)
  writeFileSync(
    BASELINE,
    `${JSON.stringify(
      {
        _comment:
          'Frozen hardcoded Chinese: lines in src/ carrying a Chinese string literal ' +
          'or template text instead of going through the i18n catalog. The ratchet ' +
          'blocks any NEW one; these are pre-existing and may only go down. ' +
          'Regenerate with `pnpm run lint:i18n:update`. See docs/i18n.md §5.',
        files: next,
      },
      null,
      2
    )}\n`
  )
  const total = Object.values(next).reduce((a, b) => a + b, 0)
  console.log(`baseline updated: ${total} line(s) across ${Object.keys(next).length} file(s)`)
  process.exit(0)
}

const result = compare(baseline, current)

const byPath = new Map(files.map((f) => [f.path, f.text]))
const samples = new Map()
for (const { file } of result.regressions) {
  const text = byPath.get(file)
  if (!text) continue
  const lines = text.split('\n')
  samples.set(
    file,
    scanSource(file, text).map((line) => ({
      line,
      text: (lines[line - 1] ?? '').trim().slice(0, 110),
    }))
  )
}

console.log(formatReport(result, samples))
process.exit(result.ok ? 0 : 1)
