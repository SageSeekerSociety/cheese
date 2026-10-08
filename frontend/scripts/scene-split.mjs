#!/usr/bin/env node
// Split a route page into a container page and a presentational
// `<Page>View.vue`: the page keeps the route, the requests and the stores, the
// view takes props and gives events back.
//
//   node scripts/scene-split.mjs src/views/MyArchivedProjectsView.vue
//       print the plan — the props, v-models and emits the view would take, the
//       route reads it would rewrite, the imports that move, and everything it
//       refuses to guess.
//   node scripts/scene-split.mjs --json src/views/…      the same plan, as JSON
//   node scripts/scene-split.mjs --write src/views/…     write the three files
//
// `--write` refuses a page whose plan has anything under "needs a human": the
// tool stops rather than guesses (see scene-split-core.mjs for what it can and
// cannot prove). It writes the sibling `<Page>View.vue`, rewrites the page to
// render it, and — when the page has one route record that lacks it — adds
// `props: true` to that record. It never overwrites an existing view.
//
// After a write, run the three checks the split is graded by:
//
//   pnpm run lint:scenes                     the view has to come out grade A
//   pnpm run lint:catalog                    it has to be in the preview site
//   pnpm exec vitest run src/views/…         the page still renders what it did
//
// Why it stops at a plan: which values the page passes and what they are called
// is a product decision, not a mechanical one. What the tool can prove it
// writes; the rest it reports, so the split is finished by hand.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { analyze, formatReport, generate, toJson } from './scene-split-core.mjs'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

const argv = process.argv.slice(2)
const flags = new Set(argv.filter((a) => a.startsWith('--')))
const targets = argv.filter((a) => !a.startsWith('--'))
if (!targets.length) {
  console.error('usage: scene-split.mjs <src/views/…vue>… [--json] [--write]')
  process.exit(2)
}
if (flags.has('--write') && flags.has('--json')) {
  console.error('--write and --json are two different answers; pick one')
  process.exit(2)
}

const opts = { frontendDir: FRONTEND }
let failed = false

for (const target of targets) {
  const pagePath = path.resolve(FRONTEND, target)
  const rel = (p) => path.relative(FRONTEND, p)
  let plan
  try {
    plan = analyze(pagePath, opts)
  } catch (e) {
    console.error(`scene-split: ${target}: ${e.message}`)
    failed = true
    continue
  }
  if (flags.has('--json')) {
    process.stdout.write(`${JSON.stringify(toJson(plan), null, 2)}\n`)
    continue
  }
  if (!flags.has('--write')) {
    process.stdout.write(`${formatReport(plan, rel)}\n`)
    continue
  }
  if (plan.viewExists) {
    console.error(`scene-split: ${target}: ${rel(plan.viewPath)} already exists, not touching it`)
    failed = true
    continue
  }
  if (plan.unsafe.length) {
    console.error(
      `scene-split: ${target}: ${plan.unsafe.length} thing(s) need a human — resolve them, then split by hand:`
    )
    for (const u of plan.unsafe) console.error(`  L${u.line} ${u.what}`)
    failed = true
    continue
  }
  const { view, page, router } = generate(plan)
  fs.writeFileSync(plan.viewPath, view)
  fs.writeFileSync(plan.page, page)
  if (router) fs.writeFileSync(router.file, router.text)
  console.error(
    `scene-split: wrote ${rel(plan.viewPath)}, rewrote ${rel(plan.page)}${router ? `, added props: true in ${rel(router.file)}` : ''}`
  )
}

process.exit(failed ? 1 : 0)
