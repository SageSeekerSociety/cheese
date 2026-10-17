#!/usr/bin/env node
// Type-checks the frontend, after generating the API types it compiles against.
//
//   node scripts/typecheck.mjs            check (exit 1 on any new error)
//   node scripts/typecheck.mjs --update   rewrite the baseline downward
//
// Every argument is handed to scripts/tsc-ratchet.mjs, which does the judging
// and owns tsc-baseline.json.
//
// src/api/openapi.d.ts is generated (scripts/gen-api-types.mjs) and is NOT
// committed, and the document it is generated from is not committed either:
// gen-api-types.mjs asks the backend for it, which builds it from the app
// instead of keeping a copy. Generating the types here, in the one place that
// reads them, is what keeps a backend route change out of the frontend's diff:
// while either half was committed — 1.4 MB of types, 1.8 MB of document — every
// route change rewrote both, and any two branches that each touched a route
// collided in them without either having a decision there. What a route change
// leaves in the diff today is one line: the digest of the document
// (backend/openapi.sha256), which is the path the path map watches to bring this
// check to that change at all. Asking the backend
// costs about 25 seconds and needs its Python environment (uv, the interpreter,
// the app's dependencies); everything that runs this has it, and when it does
// not, gen-api-types.mjs says so in those words rather than reporting errors in
// types that were never generated.
//
// Everything that runs the ratchet goes through this file, because everything
// that runs it needs the types present: `pnpm run typecheck` (the CI job, the
// pre-commit hook, `task fe:check`), the narrow changed-files project the fast
// tier builds in .github/scripts/ci_fast_checks.py, and the post-merge
// collector — .claude/scripts/ratchet-snapshot.py runs this rather than
// scripts/tsc-ratchet.mjs for exactly this reason: the collector installs
// pnpm and runs the checks on a fresh checkout, where the types have never
// been generated.
import { spawnSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))

// `process.execPath` is the node running this file, not whatever `node` PATH
// resolves to. stdio is inherited so the child's report reaches whoever called
// this — including the `--json` record the fast tier's collector parses.
function run(script, args) {
  const result = spawnSync(process.execPath, [resolve(HERE, script), ...args], { stdio: 'inherit' })
  if (result.error) {
    console.error(`could not run ${script}: ${result.error.message}`)
    process.exit(1)
  }
  if (result.status !== 0) process.exit(result.status ?? 1)
}

run('gen-api-types.mjs', [])
run('tsc-ratchet.mjs', process.argv.slice(2))
