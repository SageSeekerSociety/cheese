#!/usr/bin/env node
// Black-box fixtures: invoke the public CLI against real git diffs.
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const cli = fileURLToPath(new URL('./sfc-attribute-diff.mjs', import.meta.url))
const parent = 'frontend/src/Panel.vue'
const child = 'frontend/src/PanelButton.vue'
const env = {
  ...process.env,
  GIT_CONFIG_NOSYSTEM: '1',
  GIT_CONFIG_GLOBAL: os.devNull,
  GIT_AUTHOR_NAME: 'Fixture',
  GIT_AUTHOR_EMAIL: 'fixture@example.invalid',
  GIT_COMMITTER_NAME: 'Fixture',
  GIT_COMMITTER_EMAIL: 'fixture@example.invalid',
}
delete env.GIT_DIR
delete env.GIT_WORK_TREE
delete env.GIT_INDEX_FILE

function fixture(t, template = '<button :aria-expanded="panel.isOpen?.() ?? false" />') {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'sfc-attribute-diff-'))
  t.after(() => fs.rmSync(repo, { recursive: true, force: true }))
  const git = (...args) =>
    execFileSync('git', ['-C', repo, '-c', `core.hooksPath=${os.devNull}`, ...args], { env, encoding: 'utf8' }).trim()
  const write = (file, content) => {
    fs.mkdirSync(path.dirname(path.join(repo, file)), { recursive: true })
    fs.writeFileSync(path.join(repo, file), content)
  }
  git('init', '--quiet')
  // Deliberately large old script: the split really shrinks the original SFC.
  write(
    parent,
    `<script setup>\n${'// implementation moved out\n'.repeat(15)}</script>\n<template>\n${template}\n</template>\n`
  )
  git('add', parent)
  git('commit', '--quiet', '-m', 'baseline')
  const base = git('rev-parse', 'HEAD')
  const split = (markup, imports = "import PanelButton from './PanelButton.vue'", usage = '<PanelButton />') => {
    write(parent, `<script setup>\n${imports}\n</script>\n<template>${usage}</template>\n`)
    write(child, `<template>\n${markup}\n</template>\n`)
  }
  const run = (...args) => {
    const result = spawnSync(process.execPath, [cli, '--repo', repo, '--base', base, ...args], {
      env,
      encoding: 'utf8',
      cwd: os.tmpdir(),
    })
    return { status: result.status, output: result.stdout + result.stderr }
  }
  return { repo, git, write, base, split, run }
}

const before = 'panel.isOpen?.() ?? false'
const bad = 'panel.isOpen ? panel.isOpen() : undefined'

// test:ratchet discovers this test in CI. Check the real commit as well as fixtures:
// synthetic tests alone would prove the checker works without guarding any PR.
test('repository split contract on the committed tree (CI)', () => {
  const repo = path.resolve(path.dirname(cli), '../..')
  const result = spawnSync(process.execPath, [cli, '--repo', repo, '--head', 'HEAD'], { env, encoding: 'utf8' })
  assert.equal(result.status, 0, result.stdout + result.stderr)
})

test('the discovered CI test fails on a real violating commit and passes after repair', (t) => {
  const f = fixture(t)
  f.git('update-ref', 'refs/remotes/origin/main', f.base)
  f.split(`<button :aria-expanded="${bad}" />`)
  f.write('frontend/scripts/sfc-attribute-diff.mjs', fs.readFileSync(cli, 'utf8'))
  f.write('frontend/scripts/sfc-attribute-diff.test.mjs', fs.readFileSync(fileURLToPath(import.meta.url), 'utf8'))
  fs.symlinkSync(path.resolve(path.dirname(cli), '../node_modules'), path.join(f.repo, 'frontend/node_modules'), 'dir')
  f.git('add', parent, child, 'frontend/scripts/sfc-attribute-diff.mjs', 'frontend/scripts/sfc-attribute-diff.test.mjs')
  f.git('commit', '--quiet', '-m', 'violating split')
  // This is a separate runner, not a recursive node:test child of this file.
  const runnerEnv = { ...env }
  delete runnerEnv.NODE_TEST_CONTEXT
  const run = () =>
    spawnSync(
      process.execPath,
      [
        '--test',
        '--test-name-pattern=^repository split contract',
        path.join(f.repo, 'frontend/scripts/sfc-attribute-diff.test.mjs'),
      ],
      { env: runnerEnv, encoding: 'utf8', cwd: f.repo }
    )
  const broken = run()
  assert.equal(broken.status, 1, broken.stdout + broken.stderr)
  assert.match(broken.stdout + broken.stderr, /lost \?\? false/)
  f.split(`<button :aria-expanded="${before}" />`)
  f.git('add', parent, child)
  f.git('commit', '--quiet', '-m', 'preserve fallback')
  const fixed = run()
  assert.equal(fixed.status, 0, fixed.stdout + fixed.stderr)
})

test('intentional violation fails with both source locations and the repair; preserving fallback passes', (t) => {
  const f = fixture(t)
  f.split(`<button :aria-expanded="${bad}" />`)
  const broken = f.run()
  assert.equal(broken.status, 1, broken.output)
  assert.match(broken.output, /Panel\.vue:19 -> frontend\/src\/PanelButton\.vue:2/)
  assert.match(broken.output, /preserve \?\? false/)
  f.split(`<button :aria-expanded="${before}" />`)
  const fixed = f.run()
  assert.equal(fixed.status, 0, fixed.output)
  assert.match(fixed.output, /1 split parent/)
})

for (const attr of ['aria-expanded', 'aria-pressed', 'disabled', 'readonly', 'hidden', 'tabindex', 'data-open']) {
  for (const [original, changed, repair] of [
    [before, bad, /\?\? false/],
    ["panel.isOpen?.() ?? 'false'", bad, /\?\? 'false'/],
    ['Boolean(panel.isOpen?.())', 'panel.isOpen ? Boolean(panel.isOpen()) : undefined', /Boolean\(\.\.\.\)/],
  ]) {
    test(`${attr}: ${original} cannot lose its explicit fallback`, (t) => {
      const f = fixture(t, `<button v-bind:${attr}="${original}" />`)
      f.split(`<button :${attr}="${changed}" />`)
      const result = f.run()
      assert.equal(result.status, 1, result.output)
      assert.match(result.output, repair)
    })
  }
}

for (const anchor of ['id="a"', ':key="\'a\'"']) {
  for (const reversed of [false, true]) {
    test(`stable anchors outrank unchanged unanchored siblings: ${anchor}, reversed=${reversed}`, (t) => {
      const f = fixture(t, `<button ${anchor} :aria-expanded="${before}" />`)
      const siblings = [`<button :aria-expanded="${before}" />`, `<button ${anchor} :aria-expanded="${bad}" />`]
      f.split((reversed ? siblings.reverse() : siblings).join('\n'))
      const result = f.run()
      assert.equal(result.status, 1, result.output)
      assert.match(result.output, /1 regression/)
    })
  }
}

test('stable anchors outrank unchanged siblings in another new child', (t) => {
  const f = fixture(t, `<button id="a" :aria-expanded="${before}" />`)
  f.split(
    `<button :aria-expanded="${before}" />`,
    "import PanelButton from './PanelButton.vue'; import Other from './Other.vue'",
    '<PanelButton /><Other />'
  )
  f.write('frontend/src/Other.vue', `<template><button id="a" :aria-expanded="${bad}" /></template>`)
  const result = f.run()
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, /Other\.vue:1/)
})

test('stable anchors do not compare a changed payload to an unrelated unanchored control', (t) => {
  const f = fixture(t, `<button id="a" :aria-expanded="${before}" />`)
  f.split(`<button id="a" :aria-expanded="other.isOpen?.() ?? false" /><button :aria-expanded="${bad}" />`)
  const result = f.run()
  assert.equal(result.status, 0, result.output)
})

test('matches each anchored element, not the attribute count or first same-tag element', (t) => {
  const f = fixture(t, `<button id="a" :aria-expanded="${before}" />\n<button id="b" :aria-expanded="${before}" />`)
  f.split(`<button id="b" :aria-expanded="${bad}" />\n<button id="a" :aria-expanded="${before}" />`)
  const result = f.run()
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, /PanelButton\.vue:2/)
  assert.match(result.output, /1 regression/)
})

test('dynamic keys keep a repaired sibling from masking another element losing its fallback', (t) => {
  const f = fixture(
    t,
    `<button :key="'a'" :aria-expanded="${before}" />\n<button :key="'b'" :aria-expanded="${bad}" />`
  )
  f.split(`<button :key="'b'" :aria-expanded="${before}" />\n<button :key="'a'" :aria-expanded="${bad}" />`)
  const result = f.run()
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, /1 regression/)
})

test('a new unrelated element with the same payload is not mistaken for the old element', (t) => {
  const f = fixture(t, `<button id="a" :aria-expanded="${before}" />`)
  f.split(`<button id="b" :aria-expanded="${bad}" />`)
  assert.equal(f.run().status, 0)
})

test('unchanged duplicate does not mask a second element losing the fallback', (t) => {
  const f = fixture(t, `<button :aria-expanded="${before}" />\n<button :aria-expanded="${before}" />`)
  f.split(`<button :aria-expanded="${before}" />\n<button :aria-expanded="${bad}" />`)
  const result = f.run()
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, /1 regression/)
})

test('traverses newly added grandchildren with kebab-case usage and alias imports', (t) => {
  const f = fixture(t)
  f.split('<inner-button />')
  f.write(child, `<script setup>import InnerButton from '@/Inner.vue'</script>\n<template><inner-button /></template>`)
  f.write('frontend/src/Inner.vue', `<template><button :aria-expanded="${bad}" /></template>`)
  const result = f.run()
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, /Inner\.vue:1/)
})

test('unrelated and unused new components do not cause findings', (t) => {
  const f = fixture(t)
  f.split(`<button :aria-expanded="${before}" />`)
  f.write('frontend/src/Unrelated.vue', `<template><button :aria-expanded="${bad}" /></template>`)
  f.write(
    parent,
    `<script setup>import Unrelated from './Unrelated.vue'; import PanelButton from './PanelButton.vue'</script>\n<template><PanelButton /></template>`
  )
  assert.equal(f.run().status, 0)
})

test('retained parent bindings also participate in the per-element check', (t) => {
  const f = fixture(t)
  f.split('<span />', undefined, `<PanelButton /><button :aria-expanded="${bad}" />`)
  assert.equal(f.run().status, 1)
})

for (const fixed of [
  before,
  '(panel.isOpen ? panel.isOpen() : undefined) ?? false',
  'Boolean(panel.isOpen?.())',
  'panel.isOpen ? panel.isOpen() : false',
]) {
  test(`preserved fallback passes: ${fixed}`, (t) => {
    const f = fixture(t)
    f.split(`<button :aria-expanded="${fixed}" />`)
    const result = f.run()
    assert.equal(result.status, 0, result.output)
  })
}

test('AST handles multiline bindings, comments, parentheses, type assertions and reverse ternaries', (t) => {
  const f = fixture(t, '<button\n :aria-expanded="(panel.isOpen?.() as boolean) ?? false" />')
  f.split('<button\n :aria-expanded="!panel.isOpen ? void 0 : (panel.isOpen(/* payload */))" />')
  const result = f.run()
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, /PanelButton\.vue:3/)
})

test('does not interpret fallback text inside a string as an expression', (t) => {
  const f = fixture(t, '<button :title="\'literal ?? false\'" />')
  f.split('<button :title="visible ? \'literal ?? false\' : undefined" />')
  assert.equal(f.run().status, 0)
})

test('committed comparison reads exactly the requested head, not a fixed working tree', (t) => {
  const f = fixture(t)
  f.split(`<button :aria-expanded="${bad}" />`)
  f.git('add', parent, child)
  f.git('commit', '--quiet', '-m', 'broken split')
  const head = f.git('rev-parse', 'HEAD')
  f.split(`<button :aria-expanded="${before}" />`)
  assert.equal(f.run('--head', head).status, 1)
  assert.equal(f.run().status, 0)
})

test('staged comparison uses the index, not unstaged repairs', (t) => {
  const f = fixture(t)
  f.split(`<button :aria-expanded="${bad}" />`)
  f.git('add', parent, child)
  f.split(`<button :aria-expanded="${before}" />`)
  assert.equal(f.run('--staged').status, 1)
  assert.equal(f.run().status, 0)
})

test('invalid refs and malformed templates cannot silently pass', (t) => {
  const f = fixture(t)
  assert.equal(f.run('--head', 'does-not-exist').status, 2)
  f.split('<button :aria-expanded="broken ?" />')
  const result = f.run()
  assert.equal(result.status, 2, result.output)
  assert.match(result.output, /CANNOT JUDGE/)
})

test('files with spaces and Unicode names are compared without shell splitting', (t) => {
  const f = fixture(t)
  const renamed = 'frontend/src/按钮 child.vue'
  f.split('<span />', "import Child from './按钮 child.vue'", '<Child />')
  f.write(renamed, `<template><button :aria-expanded="${bad}" /></template>`)
  assert.equal(f.run().status, 1)
})

test('a change without a shrinking parent or without a new child is outside this bounded check', (t) => {
  const f = fixture(t)
  f.split(`<button :aria-expanded="${bad}" />`)
  f.write(
    parent,
    `<script setup>import PanelButton from './PanelButton.vue'\n${'// new implementation\n'.repeat(60)}</script><template><PanelButton /></template>`
  )
  assert.equal(f.run().status, 0)
  f.write(parent, `<template><button :aria-expanded="${bad}" /></template>`)
  assert.equal(f.run().status, 0)
})
