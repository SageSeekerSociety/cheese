#!/usr/bin/env node
// Every path this frontend passes to `request()` must be one the backend serves.
// The routes come from `backend/scripts/route_index.py --emit` — the same
// collector the addressing contract suite holds the app to — read from the file
// named by `--routes FILE`, or generated on the spot by running that command
// when no file is given. Nothing is committed in between: the index changes with
// every route and, as a tracked file, was touched by 64 PRs in a fortnight. Exits
// 1 on any judged path that matches no route, and 2 when it could not judge at
// all. The judgement itself lives in request-paths-core.mjs and is unit-tested;
// this file is only the I/O. It never writes.
import { spawnSync } from 'node:child_process'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { findOffenders, formatReport, shouldScan } from './request-paths-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const SRC = resolve(ROOT, 'src')
const BACKEND = resolve(ROOT, '../backend')

const argv = process.argv.slice(2)
const flag = argv.indexOf('--routes')
const routesFile = flag === -1 ? null : argv[flag + 1]
if (flag !== -1 && !routesFile) {
  console.error('cannot judge: --routes needs a file path')
  process.exit(2)
}

// The emit goes through `uv run`, so the child is the backend's own environment
// with the app importable; the profile wrapper inside it is what isolates the
// application's Settings. `cwd` is the backend rather than the default
// `working-directory: frontend` the workflow sets.
function generate() {
  const result = spawnSync('uv', ['run', 'python', '-m', 'scripts.route_index', '--emit'], {
    cwd: BACKEND,
    encoding: 'utf8',
  })
  if (result.error) {
    console.error(`cannot judge: could not run the route collector (${result.error.message})`)
    process.exit(2)
  }
  if (result.status !== 0) {
    console.error('cannot judge: the route collector failed')
    console.error((result.stderr || result.stdout || '').trim())
    process.exit(2)
  }
  return result.stdout
}

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const full = resolve(dir, name)
    if (statSync(full).isDirectory()) walk(full, out)
    else if (shouldScan(relative(ROOT, full))) out.push(full)
  }
  return out
}

const files = walk(SRC).map((full) => ({
  path: relative(ROOT, full).replaceAll('\\', '/'),
  text: readFileSync(full, 'utf8'),
}))

// One JSON object per line: the first line is the settings profile the index was
// collected under, not a route.
let text
if (routesFile) {
  try {
    text = readFileSync(routesFile, 'utf8')
  } catch (error) {
    // Exit 2, "could not judge" — the code the other checkers in this tree use.
    // An index that cannot be read is not an index that matched nothing, and a
    // gate that reports clean because it never got there protects nothing.
    console.error(`cannot judge: the route index could not be read (${error.message})`)
    process.exit(2)
  }
} else {
  text = generate()
}
let records
try {
  records = text
    .split('\n')
    .filter((line) => line.trim() !== '')
    .map((line) => JSON.parse(line))
} catch (error) {
  console.error(`cannot judge: the route index is not valid JSON lines (${error.message})`)
  process.exit(2)
}
if (records.length < 2) {
  console.error('cannot judge: the route index holds no routes')
  process.exit(2)
}

const result = findOffenders(files, records)
console.log(formatReport(result, routesFile || '`route_index --emit`'))
process.exit(result.offenders.length === 0 ? 0 : 1)
