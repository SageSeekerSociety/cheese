#!/usr/bin/env node
// Print the skeleton of a `/demo/catalog` entry for each component named.
//
//   node scripts/catalog-scaffold.mjs src/components/panels/PanelThreads.vue ...
//
// It writes to stdout and nothing else: paste the imports and entries into a
// `src/views/demo/catalog*.ts` file, replace every `TODO(catalog)` with the
// real sentence and every placeholder arg (`'TODO(catalog)'`, `todo(...)`) with
// the product's own shapes, then run
// `pnpm exec vitest run src/views/demo/catalog.spec.ts` — it fails while any
// marker is left. Why it stops at a skeleton: `catalog-scaffold-core.mjs`.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { bindingNames, scaffold, TODO_IMPORT } from './catalog-scaffold-core.mjs'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

const reader = {
  fileExists: (p) => fs.existsSync(p),
  readFile: (p) => (fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : undefined),
}

const targets = process.argv.slice(2).map((f) => path.relative(FRONTEND, path.resolve(FRONTEND, f)))
if (!targets.length) {
  console.error('usage: catalog-scaffold.mjs <src/...vue>...')
  process.exit(2)
}
const names = bindingNames(targets)
const imports = []
const entries = []
let usesTodo = false
for (const [i, file] of targets.entries()) {
  const source = fs.readFileSync(path.join(FRONTEND, file), 'utf8')
  const scaffolded = scaffold({ file, source, fs: reader, name: names[i] })
  imports.push(scaffolded.importLine)
  entries.push(scaffolded.entry)
  usesTodo ||= scaffolded.usesTodo
}
if (usesTodo) imports.unshift(TODO_IMPORT)
process.stdout.write(`${imports.join('\n')}\n\n${entries.join('\n')}\n`)
