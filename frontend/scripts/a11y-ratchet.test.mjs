// Tests for the accessibility ratchet. Run by `pnpm run test:ratchet`
// (node --test scripts/*.test.mjs), locally and in frontend.yml.
//
//   1. the counting and what must not be counted — pure functions on fixtures;
//   2. what the rules fire on, through eslint's own API against the real
//      eslint.a11y.config.mjs, so a config that stopped applying the plugin
//      fails here rather than going quietly green;
//   3. a plant — a file that breaks a rule is written into the tree and the
//      ratchet must go red on it, name the file and the rule, refuse to freeze
//      it on a plain --update, and go green once it is frozen on purpose.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'

import { A11Y_RULE_PREFIX, countsByRule, parseEslintJson } from './a11y-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CONFIG = resolve(ROOT, 'eslint.a11y.config.mjs')
const RATCHET = resolve(HERE, 'a11y-ratchet.mjs')
const BASELINE = resolve(ROOT, 'a11y-baseline.json')

// ---------------------------------------------------------------------------
// 1. Counting.
// ---------------------------------------------------------------------------

describe('parseEslintJson', () => {
  it('counts only the accessibility rules, per file, relative to the root', () => {
    const { counts, violations, unjudged } = parseEslintJson(
      JSON.stringify([
        {
          filePath: '/repo/frontend/src/views/A.vue',
          messages: [
            { ruleId: 'vuejs-accessibility/no-autofocus', line: 3, column: 5, message: 'autofocus' },
            { ruleId: 'vuejs-accessibility/label-has-for', line: 9, column: 1, message: 'label' },
            { ruleId: 'prettier/prettier', line: 1, column: 1, message: 'formatting' },
          ],
        },
        { filePath: '/repo/frontend/src/views/B.vue', messages: [{ ruleId: 'vue/no-unused-vars', message: 'x' }] },
      ]),
      '/repo/frontend'
    )
    assert.deepEqual(counts, { 'src/views/A.vue': 2 })
    assert.deepEqual(
      violations.map((v) => `${v.file}:${v.line} ${v.rule}`),
      ['src/views/A.vue:3 vuejs-accessibility/no-autofocus', 'src/views/A.vue:9 vuejs-accessibility/label-has-for']
    )
    assert.deepEqual(unjudged, [])
    assert.deepEqual(countsByRule(violations), {
      'vuejs-accessibility/no-autofocus': 1,
      'vuejs-accessibility/label-has-for': 1,
    })
  })

  it('does not count a file eslint could not read as clean', () => {
    const { counts, unjudged } = parseEslintJson(
      JSON.stringify([
        {
          filePath: '/repo/frontend/src/Broken.vue',
          fatal: true,
          messages: [{ ruleId: null, message: 'Parsing error: Unexpected token' }],
        },
      ]),
      '/repo/frontend'
    )
    assert.deepEqual(counts, {})
    assert.match(unjudged[0], /src\/Broken\.vue: Parsing error/)
  })

  it('refuses output that is not a JSON array', () => {
    assert.throws(() => parseEslintJson('', '/r'), /JSON/)
    assert.throws(() => parseEslintJson('{"error": "boom"}', '/r'), /not a JSON array/)
  })
})

// ---------------------------------------------------------------------------
// 2. The rules, through the real config.
// ---------------------------------------------------------------------------

async function a11yRules(eslint, template) {
  const code = `<script setup lang="ts">\nconst go = () => 1\n</script>\n\n<template>\n${template}\n</template>\n`
  const [result] = await eslint.lintText(code, { filePath: 'src/components/A11yProbe.vue', warnIgnored: false })
  assert.equal(result.fatal ?? false, false, JSON.stringify(result.messages))
  return result.messages.filter((m) => String(m.ruleId).startsWith(A11Y_RULE_PREFIX)).map((m) => m.ruleId)
}

describe('the accessibility config', () => {
  const eslint = new ESLint({ cwd: ROOT, overrideConfigFile: CONFIG })

  it('flags a clickable <div> that a keyboard cannot reach', async () => {
    const rules = await a11yRules(eslint, '  <div @click="go">open</div>')
    assert.ok(rules.includes('vuejs-accessibility/click-events-have-key-events'), rules.join())
    assert.ok(rules.includes('vuejs-accessibility/no-static-element-interactions'), rules.join())
  })

  it('leaves a <button> doing the same thing alone', async () => {
    assert.deepEqual(await a11yRules(eslint, '  <button type="button" @click="go">open</button>'), [])
  })

  it('flags an image with no alt text', async () => {
    assert.deepEqual(await a11yRules(eslint, '  <img src="/x.png" />'), ['vuejs-accessibility/alt-text'])
  })
})

// ---------------------------------------------------------------------------
// 3. The plant.
// ---------------------------------------------------------------------------

describe('the ratchet on a planted violation', () => {
  const PROBE_REL = 'src/components/__a11yProbe.vue'
  const PROBE = resolve(ROOT, PROBE_REL)
  const sandbox = mkdtempSync(join(tmpdir(), 'a11y-ratchet-'))

  after(() => {
    rmSync(PROBE, { force: true })
    rmSync(sandbox, { recursive: true, force: true })
  })

  const runRatchet = (...args) => spawnSync(process.execPath, [RATCHET, ...args], { cwd: ROOT, encoding: 'utf8' })
  // Line 6, column 3: a clickable <div> — two violations, one element.
  const CLICKABLE_DIV =
    '<script setup lang="ts">\nconst go = () => 1\n</script>\n\n<template>\n  <div @click="go">x</div>\n</template>\n'
  const frozen = (path) => JSON.parse(readFileSync(path, 'utf8')).files

  it('blocks it by name and rule, will not launder it, and accepts it only when frozen on purpose', () => {
    const scratch = join(sandbox, 'baseline.json')
    try {
      writeFileSync(PROBE, CLICKABLE_DIV)

      const blocked = runRatchet('--files', PROBE_REL, '--baseline', scratch)
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      assert.match(blocked.stdout, /__a11yProbe\.vue: 0 -> 2/)
      assert.match(blocked.stdout, /__a11yProbe\.vue:6:3 {2}vuejs-accessibility\/click-events-have-key-events/)

      // A plain --update must not swallow it.
      const laundered = runRatchet('--files', PROBE_REL, '--baseline', scratch, '--update')
      assert.equal(laundered.status, 1, `${laundered.stdout}${laundered.stderr}`)
      assert.match(laundered.stderr, /wrote nothing/)
      assert.throws(() => readFileSync(scratch), /ENOENT/)

      const frozenNew = runRatchet('--files', PROBE_REL, '--baseline', scratch, '--update', '--freeze-new')
      assert.equal(frozenNew.status, 0, `${frozenNew.stdout}${frozenNew.stderr}`)
      assert.match(frozenNew.stdout, /freezing src\/components\/__a11yProbe\.vue: 0 -> 2/)
      assert.deepEqual(frozen(scratch), { [PROBE_REL]: 2 })

      const green = runRatchet('--files', PROBE_REL, '--baseline', scratch)
      assert.equal(green.status, 0, `${green.stdout}${green.stderr}`)
    } finally {
      rmSync(PROBE, { force: true })
    }
  })

  it('--update lowers a count that went down and never raises one', () => {
    const scratch = join(sandbox, 'baseline-shrink.json')
    try {
      // Frozen at 5, now 2: the update writes 2.
      writeFileSync(PROBE, CLICKABLE_DIV)
      writeFileSync(scratch, JSON.stringify({ files: { [PROBE_REL]: 5, 'src/gone.vue': 3 } }))
      const lowered = runRatchet('--files', PROBE_REL, '--baseline', scratch, '--update')
      assert.equal(lowered.status, 0, `${lowered.stdout}${lowered.stderr}`)
      assert.deepEqual(frozen(scratch), { [PROBE_REL]: 2 })

      // Frozen at 1, now 2: the update refuses and leaves the file as it was.
      writeFileSync(scratch, JSON.stringify({ files: { [PROBE_REL]: 1 } }))
      const raised = runRatchet('--files', PROBE_REL, '--baseline', scratch, '--update')
      assert.equal(raised.status, 1, `${raised.stdout}${raised.stderr}`)
      assert.deepEqual(frozen(scratch), { [PROBE_REL]: 1 })
    } finally {
      rmSync(PROBE, { force: true })
    }
  })

  it('the committed baseline is keyed by paths that match on any machine', () => {
    const files = frozen(BASELINE)
    for (const [key, count] of Object.entries(files)) {
      assert.match(key, /^src\/.+\.vue$/, `baseline key is not a src/ .vue path: ${key}`)
      assert.ok(Number.isInteger(count) && count > 0, `${key}: ${count}`)
    }
  })
})
