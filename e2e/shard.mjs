#!/usr/bin/env node
// Splits the e2e suite into CI shards of similar duration.
//
//   node shard.mjs <index>/<total> <out-file>
//       Writes the tests of shard <index> (1-based) as a Playwright --test-list.
//   node shard.mjs --update <playwright-results.json>...
//       Rewrites shard-durations.json from JSON reports of earlier CI runs.
//
// `playwright test --shard` cuts the ordered test list into runs of equal
// count, and this suite's slow tests sit together near the front of that
// order, so the first shards took about twice as long as the last. Here every
// shard computes the same assignment from the same inputs: the current test
// list and the recorded durations. A unit goes whole to one shard, the way
// --shard keeps it: a single test in a file marked `mode: 'parallel'`, the
// whole file otherwise. Units are placed longest first on the lightest shard,
// so each test is in exactly one shard. A test with no recorded duration
// counts as the median one, so new tests are placed too.
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(fileURLToPath(import.meta.url))
const DURATIONS = resolve(ROOT, 'shard-durations.json')

/** Every test in a Playwright JSON report: key, file and seconds (all attempts). */
function testsOf(report) {
  const out = []
  const walk = (suite, titles, file) => {
    file = suite.file ?? file
    const own = suite.title && suite.title !== suite.file ? [...titles, suite.title] : titles
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) {
        const seconds = (test.results ?? []).reduce((sum, r) => sum + (r.duration ?? 0), 0) / 1000
        const key = [`[${test.projectName}]`, file, ...own, spec.title].join(' › ')
        out.push({ key, file, project: test.projectName, seconds })
      }
    }
    for (const child of suite.suites ?? []) walk(child, own, file)
  }
  for (const suite of report.suites ?? []) walk(suite, [], undefined)
  return out
}

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

if (process.argv[2] === '--update') {
  const samples = new Map()
  for (const path of process.argv.slice(3)) {
    for (const test of testsOf(JSON.parse(readFileSync(path, 'utf8')))) {
      if (test.seconds > 0) samples.set(test.key, [...(samples.get(test.key) ?? []), test.seconds])
    }
  }
  const durations = Object.fromEntries(
    [...samples].sort(([a], [b]) => a.localeCompare(b)).map(([key, s]) => [key, Math.round(median(s) * 10) / 10])
  )
  writeFileSync(DURATIONS, `${JSON.stringify(durations, null, 2)}\n`)
  console.log(`${Object.keys(durations).length} tests written to ${DURATIONS}`)
  process.exit(0)
}

const [spec, outFile] = process.argv.slice(2)
const match = /^(\d+)\/(\d+)$/.exec(spec ?? '')
if (!match || !outFile) {
  console.error('usage: shard.mjs <index>/<total> <out-file> | --update <report.json>...')
  process.exit(2)
}
const [index, total] = [Number(match[1]), Number(match[2])]
if (index < 1 || index > total) {
  console.error(`shard ${spec}: index must be between 1 and ${total}`)
  process.exit(2)
}

const listed = testsOf(
  JSON.parse(
    execFileSync(resolve(ROOT, 'node_modules/.bin/playwright'), ['test', '--list', '--reporter=json'], {
      cwd: ROOT,
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
    })
  )
)
if (listed.length === 0) {
  console.error('playwright test --list found no tests')
  process.exit(1)
}

// --test-list matches a line against a test's title path as a prefix, so a
// key that is a prefix of another key would pull that test into this shard too.
const keys = new Set(listed.map((t) => t.key))
for (const key of keys) {
  const parts = key.split(' › ')
  for (let n = parts.length - 1; n >= 3; n--) {
    if (keys.has(parts.slice(0, n).join(' › '))) {
      console.error(`"${parts.slice(0, n).join(' › ')}" is a prefix of "${key}"; --test-list cannot tell them apart`)
      process.exit(1)
    }
  }
}

const recorded = JSON.parse(readFileSync(DURATIONS, 'utf8'))
const fallback = median(Object.values(recorded))
const parallelFiles = new Set(
  listed
    .map((t) => t.file)
    .filter((file) => /describe\.configure\(\s*\{[^}]*mode:\s*['"]parallel['"]/.test(readFileSync(resolve(ROOT, 'tests', file), 'utf8')))
)

const units = new Map()
for (const test of listed) {
  const id = parallelFiles.has(test.file) ? test.key : `[${test.project}] › ${test.file}`
  const unit = units.get(id) ?? { id, keys: [], seconds: 0 }
  unit.keys.push(test.key)
  unit.seconds += recorded[test.key] ?? fallback
  units.set(id, unit)
}

const shards = Array.from({ length: total }, () => ({ seconds: 0, keys: [] }))
for (const unit of [...units.values()].sort((a, b) => b.seconds - a.seconds || a.id.localeCompare(b.id))) {
  const lightest = shards.reduce((best, s, i) => (s.seconds < shards[best].seconds ? i : best), 0)
  shards[lightest].seconds += unit.seconds
  shards[lightest].keys.push(...unit.keys)
}

const mine = shards[index - 1]
writeFileSync(outFile, `${mine.keys.join('\n')}\n`)
console.log(
  `shard ${spec}: ${mine.keys.length} of ${listed.length} tests, about ${Math.round(mine.seconds)} s ` +
    `(all shards: ${shards.map((s) => Math.round(s.seconds)).join(', ')} s)`
)
