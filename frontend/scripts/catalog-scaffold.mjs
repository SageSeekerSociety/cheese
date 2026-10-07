#!/usr/bin/env node
// Print the skeleton of a `/demo/catalog` entry for each component named.
//
//   node scripts/catalog-scaffold.mjs src/components/panels/PanelThreads.vue ...
//   node scripts/catalog-scaffold.mjs --pending src/components/panels/
//       every component under that prefix still in catalog-baseline.json
//
// It writes to stdout and nothing else: paste the imports and entries into a
// `src/views/demo/catalog*.ts` file, replace every `TODO(catalog)` with the
// real sentence and the placeholder args with the product's own shapes, then
// run `pnpm exec vitest run src/views/demo/catalog.spec.ts`. Why it stops at
// a skeleton: `catalog-scaffold-core.mjs`.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { scaffold } from './catalog-scaffold-core.mjs'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const BASELINE = path.join(FRONTEND, 'catalog-baseline.json')

function files(argv) {
  const at = argv.indexOf('--pending')
  if (at === -1) return argv
  const prefix = argv[at + 1] ?? 'src/'
  const { pending } = JSON.parse(fs.readFileSync(BASELINE, 'utf8'))
  return pending.filter((file) => file.startsWith(prefix))
}

const reader = {
  fileExists: (p) => fs.existsSync(p),
  readFile: (p) => (fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : undefined),
}

const targets = files(process.argv.slice(2)).map((f) => path.relative(FRONTEND, path.resolve(FRONTEND, f)))
if (!targets.length) {
  console.error('usage: catalog-scaffold.mjs <src/...vue>... | --pending <prefix>')
  process.exit(2)
}
const imports = []
const entries = []
for (const file of targets) {
  const source = fs.readFileSync(path.join(FRONTEND, file), 'utf8')
  const { importLine, entry } = scaffold({ file, source, fs: reader })
  imports.push(importLine)
  entries.push(entry)
}
process.stdout.write(`${imports.join('\n')}\n\n${entries.join('\n')}\n`)
