#!/usr/bin/env node
// A component calls a bare name it binds nothing for, and a module in this tree
// declares that name and exports it to nobody: the call does not fail — it
// reaches a browser global of the same name and does nothing. Why no other
// check in this repo sees it is the first half of
// global-fallback-ratchet-core.mjs.
//
//   node scripts/check-global-fallback-ratchet.mjs                      judge the tree
//   node scripts/check-global-fallback-ratchet.mjs --json               one JSON record on stdout
//   node scripts/check-global-fallback-ratchet.mjs --files <path> ...   judge only these components
//   node scripts/check-global-fallback-ratchet.mjs --sources <path> ... declare only these modules
//
// Three answers, the same three every gate in this directory gives, through the
// same reporter (ratchet-report.mjs): 0 nothing found, 1 a finding, 2 could not
// judge. There is no baseline and no --update, for the reason written beside
// `compare` in the core: this rule found nothing the day it was written.
//
// The narrowing flags exist for the self-test, which plants one composable and
// one component and judges that pair in under a second instead of 1700 files.
// A narrowed run says what it scanned, so it cannot pass for a full one.
//
// The rule and the parsing live in global-fallback-ratchet-core.mjs; the cases
// that must fire and the ones that must not are in
// test-global-fallback-ratchet.mjs, which `pnpm run test:ratchet` runs.
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { compare, countByFile, formatGlobalFallbackReport, judgeTree } from './global-fallback-ratchet-core.mjs'
import { asJson, cannotJudge, emit, verdict } from './ratchet-report.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
// The id the snapshot collector and this check's own CI step know it by. Only
// reached in --json mode; the human run is this file and nothing else.
const ID = 'fe-global-fallback'

/** Every value after `name`, up to the next flag: `--files a.vue b.vue`. */
function options(name) {
  const argv = process.argv
  const found = []
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] !== name) continue
    for (let j = i + 1; j < argv.length && !argv[j].startsWith('--'); j += 1) found.push(argv[j])
  }
  return found
}

const files = options('--files')
const sources = options('--sources')

const { violations, unjudged } = judgeTree({
  root: ROOT,
  ...(files.length ? { components: files } : {}),
  ...(sources.length ? { sources } : {}),
})

if (unjudged.length) {
  // A .vue the parser refuses could hold a finding, and a .ts it refuses could
  // hold the declaration a finding is about. Either way the honest answer is
  // that this run judged nothing, never that it found nothing.
  console.error('these files could not be parsed, so this check cannot judge the tree:')
  for (const { path, message } of unjudged) console.error(`  ${path}: ${message}`)
  cannotJudge({ id: ID }, `could not parse ${unjudged.length} file(s): ${unjudged[0].message}`)
}

// No baseline: every finding is a regression. `compare` is still the arithmetic
// — it is what the reporter reads, and it is why `ok`, `regressions` and
// `currentTotal` mean here what they mean in the four gates beside this one.
const result = compare({}, countByFile(violations))
const details = violations.map(({ file, line, column, name, where, definedIn }) => ({
  file,
  line,
  column,
  name,
  where,
  definedIn,
}))
const report = formatGlobalFallbackReport(result, details)

if (asJson) {
  // One record on stdout and nothing else — the collector parses stdout. On a
  // failure the human report still goes to stderr, so a --json run says which
  // call, in which file, reached which global.
  emit(verdict({ id: ID, result, details }))
  if (!result.ok) console.error(report)
  process.exit(result.ok ? 0 : 1)
}

console.log(report)
console.log(
  `scanned ${files.length ? files.join(', ') : 'every component under src'} against ` +
    `${sources.length ? sources.join(', ') : 'every module under src'} — ${result.currentTotal} finding(s)`
)
process.exit(result.ok ? 0 : 1)
