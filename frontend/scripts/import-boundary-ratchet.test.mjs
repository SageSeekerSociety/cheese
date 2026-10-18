// Tests for the component boundary rule and its gate. Run by `pnpm run
// test:ratchet` (node --test scripts/*.test.mjs), locally and in frontend.yml.
//
// Three kinds of case, and the third is the one that matters:
//
//   1. the counting — a pure function, driven with fixtures, including the
//      shapes that must NOT be counted — and the report;
//   2. what the RULE fires on, driven through eslint's own API against the real
//      eslint.boundary.config.mjs, so a rule that drifted from the config, or a
//      config that stopped being applied, fails here;
//   3. plants — a component that violates the rule is written into the tree and
//      the gate is required to go red on it. A boundary nobody has watched fail
//      is not a boundary, and today's tree passes it with nothing planted.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'

import { BOUNDARY_RULE_ID, formatBoundaryReport, parseEslintJson } from './import-boundary-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CONFIG = resolve(ROOT, 'eslint.boundary.config.mjs')
const RATCHET = resolve(HERE, 'import-boundary-ratchet.mjs')

// ---------------------------------------------------------------------------
// 1. Counting, and what must not be counted.
// ---------------------------------------------------------------------------

const eslintJson = (results) => JSON.stringify(results)

describe('parseEslintJson', () => {
  it('counts only this rule, per file, relative to the given root', () => {
    const { counts, unjudged } = parseEslintJson(
      eslintJson([
        {
          filePath: '/repo/frontend/src/components/A.vue',
          messages: [
            { ruleId: BOUNDARY_RULE_ID, message: 'nope' },
            { ruleId: BOUNDARY_RULE_ID, message: 'also nope' },
            { ruleId: 'prettier/prettier', message: 'formatting' },
            { ruleId: 'vue/multi-word-component-names', message: 'naming' },
          ],
        },
        {
          filePath: '/repo/frontend/src/components/B.vue',
          messages: [{ ruleId: 'prettier/prettier', message: 'formatting' }],
        },
      ]),
      '/repo/frontend'
    )

    assert.deepEqual(counts, { 'src/components/A.vue': 2 })
    assert.deepEqual(unjudged, [])
  })

  // The count is what decides, but the failure report needs the line and the
  // rule: "A.vue: 0 -> 2" alone sends the reader to grep, and grep finds both
  // the new violation and any frozen one in the same file. These are eslint's
  // own line/column/message for this rule, and nothing else.
  it('keeps the line, column and text of each of this rule’s messages', () => {
    const { violations } = parseEslintJson(
      eslintJson([
        {
          filePath: '/repo/frontend/src/components/A.vue',
          messages: [
            { ruleId: BOUNDARY_RULE_ID, message: 'nope', line: 4, column: 1 },
            { ruleId: 'prettier/prettier', message: 'formatting', line: 1, column: 1 },
            { ruleId: BOUNDARY_RULE_ID, message: 'also nope', line: 9, column: 12 },
          ],
        },
      ]),
      '/repo/frontend'
    )

    assert.deepEqual(violations, [
      { file: 'src/components/A.vue', line: 4, column: 1, rule: BOUNDARY_RULE_ID, message: 'nope' },
      { file: 'src/components/A.vue', line: 9, column: 12, rule: BOUNDARY_RULE_ID, message: 'also nope' },
    ])
  })

  // A file eslint could not parse is not a violation, so it must not turn into
  // a line in the report either.
  it('keeps no violations for a file it could not judge', () => {
    const { violations } = parseEslintJson(
      eslintJson([
        {
          filePath: '/repo/frontend/src/components/Broken.vue',
          fatal: true,
          messages: [{ ruleId: null, message: 'Parsing error: Unexpected token', line: 1, column: 1 }],
        },
      ]),
      '/repo/frontend'
    )
    assert.deepEqual(violations, [])
  })

  it('does not count a file eslint could not parse', () => {
    const { counts, unjudged } = parseEslintJson(
      eslintJson([
        {
          filePath: '/repo/frontend/src/components/Broken.vue',
          fatal: true,
          messages: [{ ruleId: null, message: 'Parsing error: Unexpected token' }],
        },
      ]),
      '/repo/frontend'
    )

    assert.deepEqual(counts, {})
    assert.equal(unjudged.length, 1)
    assert.match(unjudged[0], /Broken\.vue: Parsing error/)
  })

  it('does not count a message with no rule id', () => {
    const { counts, unjudged } = parseEslintJson(
      eslintJson([
        {
          filePath: '/repo/frontend/src/components/Weird.vue',
          messages: [{ ruleId: null, message: 'Definition for rule x was not found' }],
        },
      ]),
      '/repo/frontend'
    )

    assert.deepEqual(counts, {})
    assert.deepEqual(unjudged, ['src/components/Weird.vue: Definition for rule x was not found'])
  })

  it('refuses output that is not a JSON array', () => {
    assert.throws(() => parseEslintJson(''), /Unexpected end of JSON input|JSON/)
    assert.throws(() => parseEslintJson('{"error": "boom"}'), /not a JSON array/)
  })
})

describe('the failure report', () => {
  // The lines the editor would underline, and the chapter that says why.
  it('names the line and the rule, and points at the architecture chapter', () => {
    const text = formatBoundaryReport([
      {
        file: 'src/components/A.vue',
        line: 7,
        column: 1,
        rule: BOUNDARY_RULE_ID,
        message: 'A component under src/components must not call the API layer.',
      },
    ])

    assert.match(
      text,
      /src\/components\/A\.vue:7:1 {2}boundary\/no-api-or-router {2}A component under src\/components must not call the API layer\./
    )
    assert.match(text, /\.claude\/rules\/architecture\.md/)
  })
})

// ---------------------------------------------------------------------------
// 2. The rule, through the real config. One eslint instance, many sources: the
//    config is loaded and resolved once.
// ---------------------------------------------------------------------------

const COMPONENT = 'src/components/BoundaryProbe.vue'

/** The boundary violations eslint reports for `code` at `filePath`. */
async function violations(eslint, code, filePath) {
  const [result] = await eslint.lintText(code, { filePath, warnIgnored: false })
  return (result?.messages ?? []).filter((m) => m.ruleId === BOUNDARY_RULE_ID)
}

describe('the boundary rule', () => {
  const eslint = new ESLint({ cwd: ROOT, overrideConfigFile: CONFIG })

  it('fires on the API layer', async () => {
    for (const specifier of ['@/api', '@/services/account', '@/network/api']) {
      const hit = await violations(
        eslint,
        `<script setup lang="ts">\nimport { fetchThing } from '${specifier}'\n</script>\n`,
        COMPONENT
      )
      assert.equal(hit.length, 1, `expected a violation for ${specifier}`)
      assert.match(hit[0].message, /must not call the API layer/)
    }
  })

  it('fires on vue-router', async () => {
    const hit = await violations(
      eslint,
      `<script setup lang="ts">\nimport { useRouter } from 'vue-router'\nconst router = useRouter()\n</script>\n`,
      COMPONENT
    )
    assert.equal(hit.length, 1)
    assert.match(hit[0].message, /must not navigate/)
  })

  it('fires on a .ts helper inside components/', async () => {
    const hit = await violations(
      eslint,
      `import { api } from '@/api'\nexport const go = () => api.get('/x')\n`,
      'src/components/helpers/probe.ts'
    )
    assert.equal(hit.length, 1)
  })

  // The alias (`@/api`) is one spelling of the same dependency as a relative
  // path (`../api`). A rule that matched the string would count the first and
  // miss the second — which is what it did until #2122's follow-up: 34+
  // components reached the API layer by relative path and were never counted.
  it('fires on a relative import of the API layer, at any depth', async () => {
    const cases = [
      ['src/components/BoundaryProbe.vue', '../api'],
      ['src/components/panels/BoundaryProbe.vue', '../../api'],
      ['src/components/panels/doc/BoundaryProbe.vue', '../../../api'],
      ['src/components/BoundaryProbe.vue', '../services/account'],
      ['src/components/BoundaryProbe.vue', '../network/api/users'],
      ['src/components/panels/BoundaryProbe.vue', '../../services/account'],
    ]
    for (const [filePath, specifier] of cases) {
      const hit = await violations(
        eslint,
        `<script setup lang="ts">\nimport { thing } from '${specifier}'\nconst x = thing\n</script>\n`,
        filePath
      )
      assert.equal(hit.length, 1, `expected a violation for ${specifier} from ${filePath}`)
      assert.match(hit[0].message, /must not call the API layer/)
    }
  })

  // `import()` is the same dependency as `import … from`, and the rule resolves
  // it the same way. (No shipped component does this today — the only dynamic
  // imports of the API layer are in `__tests__/*.test.ts`, which are ignored —
  // so this costs the baseline nothing and closes the hole before someone
  // routes around the rule with it.)
  it('fires on a dynamic import of the API layer', async () => {
    const hit = await violations(
      eslint,
      `<script setup lang="ts">\nconst load = () => import('../api')\nvoid load\n</script>\n`,
      COMPONENT
    )
    assert.equal(hit.length, 1)
    assert.match(hit[0].message, /must not call the API layer/)
  })

  it('fires on a relative path that lands on vue-router', async () => {
    const hit = await violations(
      eslint,
      `<script setup lang="ts">\nimport { useRouter } from '../../node_modules/vue-router'\nconst r = useRouter()\n</script>\n`,
      COMPONENT
    )
    assert.equal(hit.length, 1)
    assert.match(hit[0].message, /must not navigate/)
  })

  // The other half of judging by target rather than by string: a path that
  // merely *looks* like the API layer — a component-local `./services/`
  // directory (src/components/chat/services/*) — is not one, and must stay
  // legal. A glob over the specifier would red these.
  it('leaves a component-local ./services/ directory alone', async () => {
    const hit = await violations(
      eslint,
      `<script setup lang="ts">\nimport { MarkdownRenderer } from './services/markdownRenderer'\nconst x = MarkdownRenderer\n</script>\n`,
      'src/components/chat/BoundaryProbe.vue'
    )
    assert.deepEqual(hit, [])
  })

  it('leaves a component that renders from props alone', async () => {
    const hit = await violations(
      eslint,
      `<script setup lang="ts">\ndefineProps<{ title: string }>()\nemit('done')\n</script>\n<template><div>{{ title }}</div></template>\n`,
      COMPONENT
    )
    assert.deepEqual(hit, [])
  })

  // A type binding from the API layer is erased before anything runs, so it
  // cannot fetch — the thing this half of the rule is about. Leaving it counted
  // made the shape the rule asks for unrepresentable: a component that draws
  // from props alone names its props' types, and a server record's type lives
  // in the API layer with no house re-export to reach it through
  // (architecture.md, principle 1: "the shape of the props is on you").
  // 21 of the 95 frozen violations were this and nothing else.
  it('leaves a type-only import of the API layer alone', async () => {
    const inComponent = [
      `import type { User } from '@/network/api/users/types'\nexport type P = { u: User }\n`,
      `import { type User } from '@/network/api/users/types'\nexport type P = { u: User }\n`,
    ]
    for (const code of inComponent) {
      const hit = await violations(eslint, `<script setup lang="ts">\n${code}</script>\n`, COMPONENT)
      assert.deepEqual(hit, [], `expected no violation for: ${code}`)
    }

    const asHelper = [
      `export type { User } from '@/network/api/users/types'\n`,
      `import type { User } from '../api'\nexport type P = { u: User }\n`,
    ]
    for (const code of asHelper) {
      const hit = await violations(eslint, code, 'src/components/helpers/probe.ts')
      assert.deepEqual(hit, [], `expected no violation for: ${code}`)
    }
  })

  // The other half keeps counting it, on purpose: AGENTS.md tells a component to
  // type a `to` prop as NavTarget rather than RouteLocationRaw, and NavTarget is
  // what lets the same component render with no router at all. The exemption
  // above is for the boundary that has no such alternative.
  it('still fires on a type-only import of vue-router', async () => {
    const hit = await violations(
      eslint,
      `<script setup lang="ts">\nimport type { RouteLocationRaw } from 'vue-router'\nexport type P = { to: RouteLocationRaw }\n</script>\n`,
      COMPONENT
    )
    assert.equal(hit.length, 1)
    assert.match(hit[0].message, /must not navigate/)
  })

  it('still fires when one binding of the declaration is a value', async () => {
    const asComponent = [
      `<script setup lang="ts">\nimport { type User, fetchUsers } from '@/network/api/users'\nvoid fetchUsers\n</script>\n`,
      `<script setup lang="ts">\nimport * as api from '@/api'\nvoid api\n</script>\n`,
      `<script setup lang="ts">\nconst load = () => import('@/api')\nvoid load\n</script>\n`,
    ]
    for (const code of asComponent) {
      const hit = await violations(eslint, code, COMPONENT)
      assert.equal(hit.length, 1, `expected a violation for: ${code}`)
    }

    const reExport = await violations(eslint, `export * from '@/api'\n`, 'src/components/helpers/probe.ts')
    assert.equal(reExport.length, 1)
  })

  it('leaves src/views alone — that is where fetching and routing belong', async () => {
    const hit = await violations(
      eslint,
      `<script setup lang="ts">\nimport { useRouter } from 'vue-router'\nimport { api } from '@/api'\n</script>\n`,
      'src/views/BoundaryProbe.vue'
    )
    assert.deepEqual(hit, [])
  })

  it('leaves a spec file in components/ alone — tests mount routers on purpose', async () => {
    const hit = await violations(
      eslint,
      `import { createRouter } from 'vue-router'\nimport { vi } from 'vitest'\nvi.mock('@/api', () => ({}))\n`,
      'src/components/probe.spec.ts'
    )
    assert.deepEqual(hit, [])
  })
})

// ---------------------------------------------------------------------------
// 3. The plant. A real file, in the real tree, judged by the real gate.
// ---------------------------------------------------------------------------

describe('the gate on a planted violation', () => {
  const PROBE = resolve(ROOT, 'src/components/__boundaryProbe.vue')

  after(() => rmSync(PROBE, { force: true }))

  const runGate = (...args) => spawnSync(process.execPath, [RATCHET, ...args], { cwd: ROOT, encoding: 'utf8' })

  it('passes a component that renders from props', () => {
    try {
      writeFileSync(
        PROBE,
        `<script setup lang="ts">\ndefineProps<{ title: string }>()\n</script>\n<template><div>{{ title }}</div></template>\n`
      )
      const clean = runGate('--files', 'src/components/__boundaryProbe.vue')
      assert.equal(clean.status, 0, `${clean.stdout}${clean.stderr}`)
    } finally {
      rmSync(PROBE, { force: true })
    }
  })

  it('fails on a single violation, in a file it has never seen', () => {
    try {
      writeFileSync(
        PROBE,
        `<script setup lang="ts">\nimport { api } from '@/api'\nconst x = api\n</script>\n<template><div /></template>\n`
      )
      const blocked = runGate('--files', 'src/components/__boundaryProbe.vue')
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      // eslint's own line:col and rule id — the diagnostic a reader needs to fix
      // it without grepping.
      assert.match(blocked.stdout, /__boundaryProbe\.vue:\d+:\d+ {2}boundary\/no-api-or-router {2}/)
    } finally {
      rmSync(PROBE, { force: true })
    }
  })

  // The same plant, spelled as a relative path. This is the end-to-end proof
  // that the gate — not just the rule — counts the relative form.
  it('fails on a planted RELATIVE import of the API layer too', () => {
    try {
      writeFileSync(
        PROBE,
        `<script setup lang="ts">\nimport { api } from '../api'\nconst x = api\n</script>\n<template><div /></template>\n`
      )
      const blocked = runGate('--files', 'src/components/__boundaryProbe.vue')
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      assert.match(blocked.stdout, /__boundaryProbe\.vue:\d+:\d+ {2}boundary\/no-api-or-router/)
    } finally {
      rmSync(PROBE, { force: true })
    }
  })
})
