#!/usr/bin/env node
// Type-checks the frontend, after generating the API types it compiles against.
//
//   node scripts/typecheck.mjs            check (exit 1 on any new error)
//   node scripts/typecheck.mjs --update   rewrite the baseline downward
//
// Every argument is handed to scripts/tsc-ratchet.mjs, which does the judging
// and owns tsc-baseline.json.
//
// src/api/openapi.d.ts is generated from backend/openapi.json
// (scripts/gen-api-types.mjs) and is NOT committed. Generating it here, in the
// one place that reads it, is what keeps a backend route change out of the
// frontend's diff: while the file was committed, every route change rewrote
// 1.4 MB of it, and any two branches that each touched a route collided in it
// without either having a decision there. It costs about three seconds.
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
