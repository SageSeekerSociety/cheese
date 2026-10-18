// Tests for the direct-Web-Storage rule and its gate. Run by `pnpm run
// test:ratchet` (node --test scripts/*.test.mjs), locally and in frontend.yml.
//
// Three kinds of case, and the third is the one that matters:
//
//   1. the counting — a pure function, driven with fixtures, including the
//      shapes that must NOT be counted — and the report;
//   2. what the RULE fires on, driven through eslint's own API against the real
//      eslint.browser-storage.config.mjs, so a rule that drifted from the config,
//      or a config that stopped being applied, fails here;
//   3. a plant — a file that reaches for storage is written into the tree and the
//      gate is required to go red on it. A gate nobody has watched fail is not a
//      gate, and today's tree passes it with nothing planted in it.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'

import { formatStorageReport, parseStorageEslintJson, STORAGE_RULE_ID } from './browser-storage-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CONFIG = resolve(ROOT, 'eslint.browser-storage.config.mjs')
const RATCHET = resolve(HERE, 'browser-storage-ratchet.mjs')

// ---------------------------------------------------------------------------
// 1. Counting, and the report.
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

describe('the failure report', () => {
  it('names the line, the rule and the module to import instead', () => {
    const text = formatStorageReport([
      {
        file: 'src/views/Home.vue',
        line: 12,
        column: 3,
        rule: STORAGE_RULE_ID,
        message: 'Web Storage must not be reached for directly.',
      },
    ])
    assert.match(
      text,
      /src\/views\/Home\.vue:12:3 {2}storage\/no-direct-browser-storage {2}Web Storage must not be reached for directly\./
    )
    assert.match(text, /@\/lib\/browserStorage/)
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
// 3. The plant. A real file, in the real tree, judged by the real gate.
// ---------------------------------------------------------------------------

describe('the gate on a planted reference', () => {
  const PLANT = resolve(ROOT, 'src/__storageProbe.ts')

  after(() => rmSync(PLANT, { force: true }))

  const runGate = (...args) => spawnSync(process.execPath, [RATCHET, ...args], { cwd: ROOT, encoding: 'utf8' })

  it('passes a file that reaches storage only through a parameter', () => {
    try {
      writeFileSync(PLANT, 'export const read = (s: Storage): string | null => s.getItem("k")\n')
      const clean = runGate('--files', 'src/__storageProbe.ts')
      assert.equal(clean.status, 0, `${clean.stdout}${clean.stderr}`)
    } finally {
      rmSync(PLANT, { force: true })
    }
  })

  it('fails on a single reference, in a file it has never seen, and names each one', () => {
    try {
      writeFileSync(
        PLANT,
        `export const read = () => localStorage.getItem('k')\nexport const write = (v: string) => sessionStorage.setItem('k', v)\n`
      )
      const blocked = runGate('--files', 'src/__storageProbe.ts')
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      // eslint's own line:col and rule id, one line per reference.
      assert.match(blocked.stdout, /__storageProbe\.ts:\d+:\d+ {2}storage\/no-direct-browser-storage {2}/)
      assert.equal(blocked.stdout.match(/storage\/no-direct-browser-storage/g)?.length, 2)
      // With the way out named.
      assert.match(blocked.stdout, /@\/lib\/browserStorage/)
    } finally {
      rmSync(PLANT, { force: true })
    }
  })
})
