#!/usr/bin/env node
// Every path this frontend passes to `request()` must be one the backend serves.
// Reads the routes from backend/scripts/route_index.json — the same artifact
// `backend/tests/contract/test_api_addressing_contract.py` holds the app to — and
// exits 1 on any judged path that matches no route. The judgement itself lives in
// request-paths-core.mjs and is unit-tested; this file is only the I/O. It never
// writes.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { findOffenders, formatReport, shouldScan } from './request-paths-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const SRC = resolve(ROOT, 'src')
const ROUTES = resolve(ROOT, '../backend/scripts/route_index.json')

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
let records
try {
  records = readFileSync(ROUTES, 'utf8')
    .split('\n')
    .filter((line) => line.trim() !== '')
    .map((line) => JSON.parse(line))
} catch (error) {
  // Exit 2, "could not judge" — the code the other checkers in this tree use.
  // An index that cannot be read is not an index that matched nothing, and a
  // gate that reports clean because it never got there protects nothing.
  console.error(`cannot judge: ${ROUTES} could not be read (${error.message})`)
  process.exit(2)
}
if (records.length < 2) {
  console.error(`cannot judge: ${ROUTES} holds no routes`)
  process.exit(2)
}

const result = findOffenders(files, records)
console.log(formatReport(result))
process.exit(result.offenders.length === 0 ? 0 : 1)
