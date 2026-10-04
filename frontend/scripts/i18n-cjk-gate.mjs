#!/usr/bin/env node
// Scans src/ for Chinese typed straight into templates and scripts. Exits 1 on
// any line that carries it without an `i18n-data:` annotation, and on any
// annotation that gives no reason or excuses nothing. Rule: docs/i18n.md §5.
//
// The scan itself lives in i18n-cjk-gate-core.mjs and is unit-tested; this
// file is only the I/O around it. It never writes.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { formatReport, scanTree, shouldScan } from './i18n-cjk-gate-core.mjs'

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

const files = walk(SRC).map((full) => ({
  path: relative(ROOT, full).replaceAll('\\', '/'),
  text: readFileSync(full, 'utf8'),
}))

const result = scanTree(files)
console.log(formatReport(result))
process.exit(result.ok ? 0 : 1)
