// Tests for the direct-Web-Storage rule and its ratchet. Run by `pnpm run
// test:ratchet` (node --test scripts/*.test.mjs), locally and in frontend.yml.
//
// Three kinds of case, and the third is the one that matters:
//
//   1. the counting and the ratchet arithmetic — pure functions, driven with
//      fixtures, including the shapes that must NOT be counted;
//   2. what the RULE fires on, driven through eslint's own API against the real
//      eslint.browser-storage.config.mjs, so a rule that drifted from the config,
//      or a config that stopped being applied, fails here;
//   3. a plant — a file that reaches for storage is written into the tree and the
//      ratchet is required to go red on it, then green once the baseline is
//      allowed to know about it. A gate nobody has watched fail is not a gate,
//      and today's tree passes the ratchet with nothing planted in it.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'

import {
  compare,
  formatStorageReport,
  parseStorageEslintJson,
  STORAGE_RULE_ID,
} from './browser-storage-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CONFIG = resolve(ROOT, 'eslint.browser-storage.config.mjs')
const RATCHET = resolve(HERE, 'browser-storage-ratchet.mjs')
const BASELINE = resolve(ROOT, 'browser-storage-baseline.json')

// ---------------------------------------------------------------------------
// 1. Counting, and the arithmetic.
// ---------------------------------------------------------------------------

const eslintJson = (results) => JSON.stringify(results)

describe('parseStorageEslintJson', () => {
  it('counts only this rule, per file, relative to the given root', () => {
    const { counts, unjudged } = parseStorageEslintJson(
      eslintJson([
        {
          filePath: '/repo/frontend/src/lib/settings.ts',
          messages: [
            { ruleId: STORAGE_RULE_ID, message: 'nope' },
            { ruleId: STORAGE_RULE_ID, message: 'also nope' },
            { ruleId: 'prettier/prettier', message: 'formatting' },
          ],
        },
        {
          filePath: '/repo/frontend/src/other.ts',
          messages: [{ ruleId: 'boundary/no-api-or-router', message: 'another ratchet' }],
        },
      ]),
      '/repo/frontend'
    )

    assert.deepEqual(counts, { 'src/lib/settings.ts': 2 })
    assert.deepEqual(unjudged, [])
  })

  it('keeps the line, column and rule of each of its messages', () => {
    const { violations } = parseStorageEslintJson(
      eslintJson([
        {
          filePath: '/repo/frontend/src/views/Home.vue',
          messages: [
            { ruleId: STORAGE_RULE_ID, message: 'nope', line: 12, column: 3 },
            { ruleId: 'vue/require-default-prop', message: 'other', line: 1, column: 1 },
          ],
        },
      ]),
      '/repo/frontend'
    )

    assert.deepEqual(violations, [
      { file: 'src/views/Home.vue', line: 12, column: 3, rule: STORAGE_RULE_ID, message: 'nope' },
    ])
  })

  it('does not count a file eslint could not parse — it says so instead', () => {
    const { counts, unjudged } = parseStorageEslintJson(
      eslintJson([
        {
          filePath: '/repo/frontend/src/Broken.ts',
          fatal: true,
          messages: [{ ruleId: null, message: 'Parsing error: Unexpected token' }],
        },
      ]),
      '/repo/frontend'
    )

    assert.deepEqual(counts, {})
    assert.equal(unjudged.length, 1)
    assert.match(unjudged[0], /Broken\.ts: Parsing error/)
  })
})

describe('the ratchet arithmetic', () => {
  it('a file over its baseline is a regression; an absent file starts at 0', () => {
    const result = compare({ 'src/a.ts': 1 }, { 'src/a.ts': 2, 'src/b.ts': 1 })
    assert.equal(result.ok, false)
    assert.deepEqual(
      result.regressions.map((r) => [r.file, r.base, r.now]),
      [
        ['src/a.ts', 1, 2],
        ['src/b.ts', 0, 1],
      ]
    )
  })

  it('a file under its baseline is not a failure — paying debt down is allowed', () => {
    const result = compare({ 'src/a.ts': 3 }, { 'src/a.ts': 1 })
    assert.equal(result.ok, true)
    assert.deepEqual(result.regressions, [])
    assert.deepEqual(
      result.improvements.map((r) => [r.file, r.base, r.now]),
      [['src/a.ts', 3, 1]]
    )
  })
})

describe('the failure report', () => {
  it('names the line, the rule and the module to import instead', () => {
    const result = compare({ 'src/views/Home.vue': 1 }, { 'src/views/Home.vue': 2 })
    const text = formatStorageReport(result, [
      {
        file: 'src/views/Home.vue',
        line: 12,
        column: 3,
        rule: STORAGE_RULE_ID,
        message: 'Web Storage must not be reached for directly.',
      },
    ])

    assert.match(text, /src\/views\/Home\.vue: 1 -> 2/)
    assert.match(
      text,
      /src\/views\/Home\.vue:12:3 {2}storage\/no-direct-browser-storage {2}Web Storage must not be reached for directly\./
    )
    assert.match(text, /@\/lib\/browserStorage/)
  })

  it('stays a set of counts when no diagnostics are passed', () => {
    const text = formatStorageReport(compare({ 'src/a.ts': 1 }, { 'src/a.ts': 2 }))
    assert.match(text, /src\/a\.ts: 1 -> 2/)
    assert.doesNotMatch(text, /:12:3/)
  })
})

// ---------------------------------------------------------------------------
// 2. The rule, through the real config. One eslint instance, many sources: the
//    config is loaded and resolved once.
// ---------------------------------------------------------------------------

const PROBE = 'src/lib/StorageProbe.ts'

/** The storage violations eslint reports for `code` at `filePath`. */
async function violations(eslint, code, filePath = PROBE) {
  const [result] = await eslint.lintText(code, { filePath, warnIgnored: false })
  return (result?.messages ?? []).filter((m) => m.ruleId === STORAGE_RULE_ID)
}

describe('the storage rule', () => {
  const eslint = new ESLint({ cwd: ROOT, overrideConfigFile: CONFIG })

  it('fires on a plain localStorage/sessionStorage reference', async () => {
    const cases = [
      `export const read = () => localStorage.getItem('k')\n`,
      `export const write = (v: string) => sessionStorage.setItem('k', v)\n`,
      `export const clear = () => {\n  localStorage.removeItem('k')\n}\n`,
    ]
    for (const code of cases) {
      const hit = await violations(eslint, code)
      assert.equal(hit.length, 1, `expected a violation for: ${code}`)
      assert.match(hit[0].message, /@\/lib\/browserStorage/)
    }
  })

  it('fires on the window / globalThis spellings of the same global', async () => {
    const cases = [
      `export const read = () => window.localStorage.getItem('k')\n`,
      `export const read = () => globalThis.sessionStorage.getItem('k')\n`,
      `export const read = () => window['localStorage'].getItem('k')\n`,
    ]
    for (const code of cases) {
      const hit = await violations(eslint, code)
      assert.equal(hit.length, 1, `expected a violation for: ${code}`)
    }
  })

  it('counts every reference in a file, not just the first', async () => {
    const hit = await violations(
      eslint,
      `export const both = () => {\n  localStorage.setItem('a', '1')\n  sessionStorage.setItem('b', '2')\n}\n`
    )
    assert.equal(hit.length, 2)
  })

  // The escape hatch a pattern-matching rule cannot close: aliasing the global
  // out of `window` and using the alias. There is no reason to write this except
  // to get around the gate, so it is counted.
  //
  // Deliberately over-counted, and this test says so: `{ localStorage }` and
  // `const { localStorage } = window` are the same node shape, and without
  // scope analysis the rule cannot tell them apart. The second is the escape
  // hatch, so the first is the price; the literal form is also the one nobody
  // writes by accident (zero files in this tree do).
  it('fires on destructuring the storage object out of window', async () => {
    const hit = await violations(eslint, `const { localStorage } = window\nexport const s = localStorage\n`)
    assert.ok(hit.length >= 1)
  })

  it('fires in a .vue file too', async () => {
    const hit = await violations(
      eslint,
      `<script setup lang="ts">\nconst saved = localStorage.getItem('k')\n</script>\n<template><div /></template>\n`,
      'src/views/StorageProbe.vue'
    )
    assert.equal(hit.length, 1)
  })

  // The other half of an identifier-based rule: things that merely say the word.
  it('leaves comments and plain strings alone', async () => {
    const cases = [
      `// falls back to localStorage when the quota is full\nexport const x = 1\n`,
      `export const KEY = 'localStorage'\nexport const y = KEY\n`,
      `export const z = JSON.stringify({ sessionStorage: false })\n`,
    ]
    for (const code of cases) {
      const hit = await violations(eslint, code)
      assert.deepEqual(hit, [], `expected no violation for: ${code}`)
    }
  })

  it('leaves a member name that is a label / type / field, not a reference, alone', async () => {
    const cases = [
      `export interface W {\n  localStorage: Storage | null\n}\n`,
      `export class Fake {\n  localStorage = undefined\n}\n`,
      `export const stub = { localStorage: undefined }\n`,
    ]
    for (const code of cases) {
      const hit = await violations(eslint, code)
      assert.deepEqual(hit, [], `expected no violation for: ${code}`)
    }
  })

  // @vueuse's useStorage is a safe wrapper and a correct answer for a reactive,
  // cross-tab preference; the rule is about reaching for the raw global. It just
  // cannot back module-level shared state, which is why the module exists.
  it('leaves vueuse useStorage alone', async () => {
    const hit = await violations(
      eslint,
      `import { useStorage } from '@vueuse/core'\nexport const width = useStorage('sidebar-width', 280)\n`
    )
    assert.deepEqual(hit, [])
  })

  it('leaves the one module that is supposed to touch the global alone', async () => {
    const hit = await violations(
      eslint,
      `export const raw = localStorage\nexport const s = sessionStorage\n`,
      'src/lib/browserStorage.ts'
    )
    assert.deepEqual(hit, [])
  })

  it('leaves spec files and src/test alone — they check the browser on purpose', async () => {
    const forSpec = await violations(
      eslint,
      `import { expect, it } from 'vitest'\nit('persists', () => {\n  expect(localStorage.getItem('k')).toBe('1')\n})\n`,
      'src/lib/storageProbe.spec.ts'
    )
    assert.deepEqual(forSpec, [])

    const forSetup = await violations(eslint, `globalThis.localStorage.clear()\n`, 'src/test/setup-storage.ts')
    assert.deepEqual(forSetup, [])
  })
})

// ---------------------------------------------------------------------------
// 3. The plant. A real file, in the real tree, judged by the real ratchet.
// ---------------------------------------------------------------------------

describe('the ratchet on a planted reference', () => {
  const PLANT = resolve(ROOT, 'src/__storageProbe.ts')
  const sandbox = mkdtempSync(join(tmpdir(), 'storage-ratchet-'))
  const scratchBaseline = join(sandbox, 'baseline.json')

  after(() => {
    rmSync(PLANT, { force: true })
    rmSync(sandbox, { recursive: true, force: true })
  })

  const runRatchet = (...args) => spawnSync(process.execPath, [RATCHET, ...args], { cwd: ROOT, encoding: 'utf8' })

  it('blocks it, will not launder it, and freezes it only on purpose', () => {
    try {
      writeFileSync(
        PLANT,
        `export const read = () => localStorage.getItem('k')\nexport const write = (v: string) => sessionStorage.setItem('k', v)\n`
      )

      // Against an empty baseline the planted file is a new reference.
      const blocked = runRatchet('--files', 'src/__storageProbe.ts', '--baseline', scratchBaseline)
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      assert.match(blocked.stdout, /__storageProbe\.ts: 0 -> 2/)
      // And the report carries eslint's own line:col and rule id, not only the
      // count — the diagnostic a reader needs to fix it without grepping.
      assert.match(blocked.stdout, /__storageProbe\.ts:\d+:\d+ {2}storage\/no-direct-browser-storage {2}/)
      // Both references are reported — one line each — not just the count.
      assert.equal(blocked.stdout.match(/storage\/no-direct-browser-storage/g)?.length, 2)
      // With the way out named.
      assert.match(blocked.stdout, /@\/lib\/browserStorage/)

      // A plain --update must not swallow it: the baseline is written downward
      // only, and this would raise it.
      const laundered = runRatchet('--files', 'src/__storageProbe.ts', '--baseline', scratchBaseline, '--update')
      assert.equal(laundered.status, 1, `${laundered.stdout}${laundered.stderr}`)
      assert.match(laundered.stderr, /--update only lowers the baseline/)
      assert.match(laundered.stderr, /__storageProbe\.ts: 0 -> 2/)
      assert.throws(() => readFileSync(scratchBaseline), /ENOENT/)

      // Accepting new debt is a separate, deliberate act: --freeze-new freezes it,
      // and the same run then goes green.
      const update = runRatchet(
        '--files',
        'src/__storageProbe.ts',
        '--baseline',
        scratchBaseline,
        '--update',
        '--freeze-new'
      )
      assert.equal(update.status, 0, `${update.stdout}${update.stderr}`)
      assert.match(update.stdout, /freezing src\/__storageProbe\.ts: 0 -> 2/)
      assert.equal(JSON.parse(readFileSync(scratchBaseline, 'utf8')).files['src/__storageProbe.ts'], 2)

      const after = runRatchet('--files', 'src/__storageProbe.ts', '--baseline', scratchBaseline)
      assert.equal(after.status, 0, `${after.stdout}${after.stderr}`)
    } finally {
      rmSync(PLANT, { force: true })
    }
  })

  it('--update lowers a count that went down and never raises one', () => {
    const scratch = join(sandbox, 'baseline-shrink.json')
    try {
      writeFileSync(
        PLANT,
        `export const read = () => localStorage.getItem('k')\nexport const write = (v: string) => sessionStorage.setItem('k', v)\n`
      )
      // Frozen at 5, now 2: the update writes 2, and needs no --freeze-new.
      writeFileSync(scratch, JSON.stringify({ files: { 'src/__storageProbe.ts': 5, 'src/gone.ts': 3 } }))
      const lowered = runRatchet('--files', 'src/__storageProbe.ts', '--baseline', scratch, '--update')
      assert.equal(lowered.status, 0, `${lowered.stdout}${lowered.stderr}`)
      assert.deepEqual(JSON.parse(readFileSync(scratch, 'utf8')).files, { 'src/__storageProbe.ts': 2 })

      // Frozen at 1, now 2: the update refuses and leaves the file as it was.
      writeFileSync(scratch, JSON.stringify({ files: { 'src/__storageProbe.ts': 1 } }))
      const raised = runRatchet('--files', 'src/__storageProbe.ts', '--baseline', scratch, '--update')
      assert.equal(raised.status, 1, `${raised.stdout}${raised.stderr}`)
      assert.deepEqual(JSON.parse(readFileSync(scratch, 'utf8')).files, { 'src/__storageProbe.ts': 1 })
    } finally {
      rmSync(PLANT, { force: true })
    }
  })

  it('the committed baseline still describes the tree it was written against', () => {
    // Not a full re-run (that is the `lint:storage` step in frontend.yml): this
    // only asserts the file is readable and every key is a path under src/, so a
    // baseline written from absolute paths — the shape that silently matches
    // nothing on another machine — fails here. An empty baseline is not an error
    // here: zero references is where the migration ends, and a baseline of zero
    // refuses every new one. What an accidentally emptied baseline would let
    // through, the `lint:storage` step already refuses.
    const files = JSON.parse(readFileSync(BASELINE, 'utf8')).files
    for (const key of Object.keys(files)) {
      assert.match(key, /^src\//, `baseline key is not a src path: ${key}`)
      assert.equal(typeof files[key], 'number')
    }
  })
})
