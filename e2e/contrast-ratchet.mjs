#!/usr/bin/env node

// `fe-contrast` as the ratchet snapshot collector runs it: one command in, one
// JSON line out.
//
//   node contrast-ratchet.mjs --json
//
// The gate that blocks a pull request is tests/contrast-ratchet.spec.ts. This is
// the same walk — e2e/contrast-walk.mjs, over the rule and the baseline beside
// it in frontend/ — with the browser and the frontend started here instead of
// by Playwright's config, because the collector runs one process and reads its
// stdout. The two must not disagree about anything: what this prints as
// `actual` is what that gate turns red over.
//
// The pages are served by the dev server (`frontend/`, no production build).
// The measurement is of painted colours and computed styles, which the bundler
// does not touch, and a build would add minutes to a job that has none to
// spare. The three pages are the ones a visitor reads before signing in, so
// nothing here needs the backend.
//
// There is no `--update`: the baseline is written by the spec's documented
// `CONTRAST_UPDATE=1`, one place, and a second way to change it is a second way
// to change it by accident.

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { createServer } from 'node:net'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from '@playwright/test'

import {
  PAGES,
  THEMES,
  compare,
  formatContrastReport,
  tightenedBaseline,
  worstOf,
} from '../frontend/scripts/contrast-ratchet-core.mjs'
import { BETTER, asJson, cannotJudge, emit } from '../frontend/scripts/ratchet-report.mjs'
import { measure, readBaseline } from './contrast-walk.mjs'

const ID = 'fe-contrast'
const ROOT = fileURLToPath(new URL('..', import.meta.url))
const FRONTEND = join(ROOT, 'frontend')
const VITE_BIN = join(FRONTEND, 'node_modules', 'vite', 'bin', 'vite.js')

/** A cold dev server transforms the whole app on the first request, so a page
 *  gets far longer here than the 15s the gate allows a served build. */
const MOUNT_TIMEOUT = 120_000
const SERVER_TIMEOUT = 120_000
/** What we leave the collector: it kills a check at 1800s, and a walk cut off
 *  there would say nothing. */
const BUDGET = 900_000

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const tail = (text) =>
  text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(-3)
    .join(' | ')

/** A port nothing is listening on. Vite is started with --strictPort, so a race
 *  here is a clear failure rather than a second app on a shifted port. */
function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer()
    probe.unref()
    probe.on('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

/**
 * The frontend, as `vite` serves it in development. Its own process group, so
 * that shutting it down takes whatever it started with it.
 */
async function startServer() {
  if (!existsSync(VITE_BIN)) {
    throw new Error(`vite is not installed at ${VITE_BIN} — run pnpm install in frontend/`)
  }
  const port = await freePort()
  const origin = `http://127.0.0.1:${port}`
  const child = spawn(
    process.execPath,
    [VITE_BIN, '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    // stdout is dropped rather than piped: in --json mode the only thing this
    // process may write to its own stdout is the record.
    { cwd: FRONTEND, stdio: ['ignore', 'ignore', 'pipe'], detached: true },
  )
  let stderr = ''
  child.stderr.on('data', (chunk) => {
    stderr = (stderr + chunk.toString()).slice(-4000)
  })

  const stop = async () => {
    if (child.exitCode !== null) return
    const signalled = (signal) => {
      try {
        process.kill(-child.pid, signal)
      } catch {
        child.kill(signal)
      }
    }
    const exited = new Promise((resolve) => child.once('exit', resolve))
    signalled('SIGTERM')
    await Promise.race([exited, sleep(5_000)])
    signalled('SIGKILL')
  }

  const deadline = Date.now() + SERVER_TIMEOUT
  for (;;) {
    if (child.exitCode !== null) {
      throw new Error(`the dev server exited ${child.exitCode}: ${tail(stderr)}`)
    }
    try {
      const response = await fetch(`${origin}/`)
      if (response.ok) break
    } catch {
      // not up yet
    }
    if (Date.now() > deadline) {
      throw new Error(`the dev server did not answer on ${origin} within ${SERVER_TIMEOUT / 1000}s: ${tail(stderr)}`)
    }
    await sleep(250)
  }
  return { origin, stop }
}

/** Every page in every theme, walked by the code the gate runs. */
async function walk(origin) {
  const browser = await chromium.launch()
  const runs = []
  const deadline = Date.now() + BUDGET
  try {
    for (const theme of THEMES) {
      // A context per theme: the theme is planted by an init script, and the
      // scripts of one context accumulate rather than replace each other.
      const context = await browser.newContext({
        baseURL: origin,
        // Without it the app renders in English and the visitor bar the walk
        // waits for is never there.
        locale: 'zh-CN',
        serviceWorkers: 'block',
      })
      try {
        for (const path of PAGES) {
          if (Date.now() > deadline) {
            throw new Error(`the walk ran past its ${BUDGET / 1000}s budget, at ${path} [${theme}]`)
          }
          const page = await context.newPage()
          try {
            runs.push(...(await measure(page, path, theme, { timeout: MOUNT_TIMEOUT })))
          } finally {
            await page.close()
          }
        }
      } finally {
        await context.close()
      }
    }
  } finally {
    await browser.close()
  }
  return runs
}

/**
 * The record, from the same comparison the gate makes.
 *
 * `actual` and `frozen` are counts of frozen runs, not of violations: one entry
 * of the baseline is one selector, and it stands for every instance of it. What
 * the page is still owed is what `tightenedBaseline` keeps — the runs below
 * their floor and the runs that still carry instances the walker cannot read —
 * and it is computed with `freezeNew` so that a run which appeared below its
 * floor counts as the regression it is, instead of counting as nothing until
 * somebody freezes it.
 */
function judge(runs) {
  const baseline = readBaseline()
  const worst = worstOf(runs)
  const result = compare(baseline, worst)
  const owed = tightenedBaseline(baseline, worst, { freezeNew: true })
  const stale = Object.keys(baseline)
    .filter((key) => !(key in owed))
    .sort()
    .map((key) => ({
      file: key,
      frozen: baseline[key].ratio ?? null,
      actual: worst.has(key) ? worst.get(key).ratio : null,
      why: worst.has(key) ? 'the run clears its floor now' : 'gone from the page',
    }))
  const details = Object.keys(owed)
    .sort()
    .map((file) => ({
      file,
      ratio: owed[file].ratio,
      need: owed[file].need,
      unreadable: owed[file].unreadable,
    }))
  return {
    ok: result.ok,
    text: formatContrastReport(result),
    record: {
      id: ID,
      better: BETTER,
      status: result.ok ? 'pass' : 'fail',
      actual: Object.keys(owed).length,
      frozen: Object.keys(baseline).length,
      stale,
      details,
    },
  }
}

async function main() {
  let server = null
  let outcome = null
  try {
    server = await startServer()
    outcome = judge(await walk(server.origin))
  } catch (error) {
    outcome = { reason: error instanceof Error ? error.message : String(error) }
  }
  // Before anything exits: a check that leaked its server would hold the port
  // and the process group for the rest of the job.
  if (server) await server.stop()
  if (outcome.reason !== undefined) cannotJudge({ id: ID }, outcome.reason)
  if (asJson) emit(outcome.record)
  else console.log(outcome.text)
  process.exit(outcome.ok ? 0 : 1)
}

await main()
