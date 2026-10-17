#!/usr/bin/env node
// Black-box contracts: invoke the public CLI against real git diffs.
// The last contract mutates the guard's comparison line and asserts these very
// assertions go red against it, so a silently over-broad exemption cannot pass.
import assert from 'node:assert/strict'
import { execFileSync, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const guard = process.env.COMPOSABLE_WATCH_GUARD
  ? path.resolve(process.env.COMPOSABLE_WATCH_GUARD)
  : fileURLToPath(new URL('./composable-watch-guard.mjs', import.meta.url))
const component = 'frontend/src/components/Panel.vue'
const composable = 'frontend/src/composables/usePanel.ts'

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

const baseScript = `import { ref, watch } from 'vue'

const props = defineProps<{ topicId: string; other: string }>()
const loading = ref(false)
watch(() => props.topicId, () => { loading.value = false })`

const baseEffectScript = `import { ref, watchEffect } from 'vue'

const props = defineProps<{ topicId: string }>()
const loading = ref(false)
watchEffect(() => { if (props.topicId) loading.value = true })`

const panelHead = `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ topicId: string; other: string }>()
const { loading } = usePanel({ topicId: () => props.topicId, other: () => props.other })`

const droppedComposable = `import { ref } from 'vue'

export function usePanel(scope: { topicId: () => string; other: () => string }) {
  const loading = ref(false)
  return { loading }
}`

function sfc(script) {
  return `<script setup lang="ts">\n${script}\n</script>\n\n<template>\n  <p>{{ loading }}</p>\n</template>\n`
}

function fixture(t, script = baseScript) {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'composable-watch-guard-'))
  t.after(() => fs.rmSync(repo, { recursive: true, force: true }))
  const git = (...args) =>
    execFileSync('git', ['-C', repo, '-c', `core.hooksPath=${os.devNull}`, ...args], { env, encoding: 'utf8' }).trim()
  const write = (file, content) => {
    fs.mkdirSync(path.dirname(path.join(repo, file)), { recursive: true })
    fs.writeFileSync(path.join(repo, file), content)
  }
  git('init', '--quiet')
  write(component, sfc(script))
  git('add', component)
  git('commit', '--quiet', '-m', 'baseline')
  const base = git('rev-parse', 'HEAD')
  return { repo, base, git, write }
}

function split(t, { script = baseScript, composableSource, head = panelHead, cli = guard } = {}) {
  const fixtureRun = fixture(t, script)
  fixtureRun.write(component, sfc(head))
  if (composableSource !== undefined) fixtureRun.write(composable, composableSource)
  const run = (...args) => {
    const result = spawnSync(process.execPath, [cli, '--repo', fixtureRun.repo, '--base', fixtureRun.base, ...args], {
      env,
      encoding: 'utf8',
      cwd: os.tmpdir(),
    })
    return { status: result.status, output: result.stdout + result.stderr }
  }
  return { ...fixtureRun, run }
}

function assertReportsLost(result) {
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, /LOST frontend\/src\/components\/Panel\.vue:6/)
  assert.match(result.output, /props\.topicId/)
  assert.match(result.output, /usePanel\.ts/)
  assert.match(result.output, /watch\(\(\) => toValue\(scope\.topicId\)/)
  assert.match(result.output, /waive: --allow 'frontend\/src\/components\/Panel\.vue:topicId'/)
}

const EQUIVALENT_ANCHOR = 'const isEquivalent = (available, lost) => available.has(lost)'
const ALLOW_ANCHOR =
  'const isWaived = (allows, file, key) => allows.includes(`${file}:${key}`) || allows.includes(`${file}:*`)'

function mutatedGuard(
  t,
  anchor = EQUIVALENT_ANCHOR,
  replacement = 'const isEquivalent = (available, lost) => available.size > 0 || available.has(lost)'
) {
  const source = fs.readFileSync(guard, 'utf8')
  assert.ok(source.includes(anchor), 'the mutation anchor moved; update composable-watch-guard.test.mjs')
  const mutated = source.replace(anchor, replacement)
  const file = path.join(
    path.dirname(guard),
    `.composable-watch-guard.mutated-${process.pid}-${mutatedGuard.seq++}.mjs`
  )
  fs.writeFileSync(file, mutated)
  t.after(() => fs.rmSync(file, { force: true }))
  return file
}
mutatedGuard.seq = 0

test('reports a watch source the split left behind', (t) => {
  const { run } = split(t, { composableSource: droppedComposable })
  assertReportsLost(run())
})

test('passes once the composable watches the same source through its scope', (t) => {
  const { run } = split(t, {
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { topicId: () => string; other: () => string }) {
  const loading = ref(false)
  watch(() => toValue(scope.topicId), () => { loading.value = false })
  return { loading }
}`,
  })
  const result = run()
  assert.equal(result.status, 0, result.output)
  assert.match(result.output, /PASS \(composable watch guard\)/)
})

test('matches a nested prop path through the scope its call site passes', (t) => {
  // The real shape: `props.topic.id` in the component becomes a `topicId` getter
  // at the call site and `toValue(scope.topicId)` in the composable.
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ topic: { id: string } }>()
const loading = ref(false)
watch(() => props.topic.id, () => { loading.value = false })`
  const head = `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ topic: { id: string } }>()
const { loading } = usePanel({ topicId: () => props.topic.id })`
  const composableSource = `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { topicId: () => string }) {
  const loading = ref(false)
  watch(() => toValue(scope.topicId), () => { loading.value = false })
  return { loading }
}`
  const kept = split(t, { script, head, composableSource }).run()
  assert.equal(kept.status, 0, kept.output)

  const dropped = split(t, { script, head, composableSource: droppedComposable }).run()
  assert.equal(dropped.status, 1, dropped.output)
  assert.match(dropped.output, /topicId/)
})

test('matches a scope the call site passes as a getter of an object', (t) => {
  // The real shape in useArtifactVersionPreview: the call site hands over
  // `() => ({ topicId: props.topicId })` and the composable names it once,
  // `const s = toValue(source); return [s.topicId]`, before watching it.
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ topicId: string; other: string }>()
const loading = ref(false)
watch(() => [props.topicId, props.other], () => { loading.value = false })`
  const head = `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ topicId: string; other: string }>()
const { loading } = usePanel(() => ({ topicId: props.topicId, other: props.other }))`
  const composableSource = `import { ref, toValue, watch } from 'vue'

export function usePanel(source: { topicId: string; other: string }) {
  const loading = ref(false)
  watch(
    () => {
      const s = toValue(source)
      return [s.topicId, s.other]
    },
    () => { loading.value = false }
  )
  return { loading }
}`
  const kept = split(t, { script, head, composableSource }).run()
  assert.equal(kept.status, 0, kept.output)

  const dropped = split(t, { script, head, composableSource: droppedComposable }).run()
  assert.equal(dropped.status, 1, dropped.output)
  assert.match(dropped.output, /topicId/)
})

test('matches a composable whose first parameter is the watched value', (t) => {
  // useDeviceScreen: the call site passes `() => props.sid`, the composable
  // watches its parameter directly, `watch(() => toValue(sid), ...)`.
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ sid: string }>()
const loading = ref(false)
watch(() => props.sid, () => { loading.value = false })`
  const head = `import { useScreen } from '@/composables/useScreen'

const props = defineProps<{ sid: string }>()
const { loading } = useScreen(() => props.sid, { onSwitched: () => {} })`
  const kept = split(t, {
    script,
    head,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function useScreen(sid: { value: string }, hooks: { onSwitched?: () => void }) {
  const loading = ref(false)
  watch(() => toValue(sid), () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(kept.status, 0, kept.output)

  const dropped = split(t, {
    script,
    head,
    composableSource: `import { ref } from 'vue'

export function useScreen(sid: { value: string }) {
  const loading = ref(false)
  return { loading }
}`,
  }).run()
  assert.equal(dropped.status, 1, dropped.output)
  assert.match(dropped.output, /props\.sid/)
})

test('an unrelated watch in the composable does not stand in for the lost one', (t) => {
  const { run } = split(t, {
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { topicId: () => string; other: () => string }) {
  const loading = ref(false)
  watch(() => toValue(scope.other), () => { loading.value = false })
  return { loading }
}`,
  })
  assertReportsLost(run())
})

test('an injected exemption branch makes the lost-watch contract go red', (t) => {
  const fixtureRun = split(t, {
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { topicId: () => string; other: () => string }) {
  const loading = ref(false)
  watch(() => toValue(scope.other), () => { loading.value = false })
  return { loading }
}`,
  })
  assertReportsLost(fixtureRun.run())

  const mutated = mutatedGuard(t)
  const result = spawnSync(process.execPath, [mutated, '--repo', fixtureRun.repo, '--base', fixtureRun.base], {
    env,
    encoding: 'utf8',
    cwd: os.tmpdir(),
  })
  const shaped = { status: result.status, output: result.stdout + result.stderr }
  assert.equal(shaped.status, 0, 'the mutant must waive the loss, otherwise the mutation proved nothing')
  assert.throws(() => assertReportsLost(shaped), 'the contract must reject the over-broad exemption')
})

test('exempts only the source named with --allow', (t) => {
  const { run } = split(t, { composableSource: droppedComposable })
  const waived = run('--allow', `${component}:topicId`)
  assert.equal(waived.status, 0, waived.output)
  const wildcard = run('--allow', `${component}:*`)
  assert.equal(wildcard.status, 0, wildcard.output)
})

test('a wrong --allow does not waive a different source', (t) => {
  const { run } = split(t, { composableSource: droppedComposable })
  const wrongKey = run('--allow', `${component}:other`)
  assert.equal(wrongKey.status, 1, wrongKey.output)
  const wrongFile = run('--allow', 'frontend/src/components/Other.vue:*')
  assert.equal(wrongFile.status, 1, wrongFile.output)
})

test('an injected over-broad --allow makes the exemption contract go red', (t) => {
  const fixtureRun = split(t, { composableSource: droppedComposable })
  const mutantCases = [
    {
      allow: 'frontend/src/components/Other.vue:*',
      replacement:
        'const isWaived = (allows, file, key) => allows.includes(`${file}:${key}`) || allows.some((entry) => entry.endsWith(":*"))',
    },
    {
      allow: `${component}:other`,
      replacement:
        'const isWaived = (allows, file, key) => allows.some((entry) => entry === `${file}:${key}` || entry.startsWith(`${file}:`))',
    },
  ]
  for (const { allow, replacement } of mutantCases) {
    const baseline = fixtureRun.run('--allow', allow)
    assert.equal(baseline.status, 1, baseline.output)

    const mutated = mutatedGuard(t, ALLOW_ANCHOR, replacement)
    const result = spawnSync(
      process.execPath,
      [mutated, '--repo', fixtureRun.repo, '--base', fixtureRun.base, '--allow', allow],
      { env, encoding: 'utf8', cwd: os.tmpdir() }
    )
    const shaped = { status: result.status, output: result.stdout + result.stderr }
    assert.equal(shaped.status, 0, 'the mutant must waive the wrong allow, otherwise the mutation proved nothing')
    assert.throws(() => assert.equal(shaped.status, 1), 'the contract must reject the over-broad waiver')
  }
})

test('keeps a prop literally named value', (t) => {
  // TipTapViewer and RollingNumber both watch `props.value`; the split passes it as
  // `{ value: () => props.value }`, so `.value` must not be read as a ref unwrap.
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ value: string }>()
const loading = ref(false)
watch(() => props.value, () => { loading.value = false })`
  const head = `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ value: string }>()
const { loading } = usePanel({ value: () => props.value })`
  const carried = `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { value: () => string }) {
  const loading = ref(false)
  watch(() => toValue(scope.value), () => { loading.value = false })
  return { loading }
}`
  const kept = split(t, { script, head, composableSource: carried }).run()
  assert.equal(kept.status, 0, kept.output)

  const dropped = split(t, { script, head, composableSource: droppedComposable }).run()
  assert.equal(dropped.status, 1, dropped.output)
  assert.match(dropped.output, /waive: --allow 'frontend\/src\/components\/Panel\.vue:value'/)
})

test('a local ref with the same name as a lost prop does not stand in for it', (t) => {
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ topicId: string }>()
const topicId = ref('')
const loading = ref(false)
watch(() => props.topicId, () => { loading.value = false })
watch(topicId, () => { loading.value = false })`
  const head = `import { ref, watch } from 'vue'

const props = defineProps<{ topicId: string }>()
const topicId = ref('')
const loading = ref(false)
watch(topicId, () => { loading.value = false })`
  const result = split(t, { script, head }).run()
  assert.equal(result.status, 1, result.output)
  assert.match(result.output, /props\.topicId/)
})

test('another component the same change touched may carry the watcher', (t) => {
  const head = `import { ref } from 'vue'

const props = defineProps<{ topicId: string }>()
const loading = ref(false)`
  const fixtureRun = split(t, { head, composableSource: undefined })
  fixtureRun.write(
    'frontend/src/components/Peer.vue',
    sfc(`import { watch } from 'vue'

const props = defineProps<{ topicId: string }>()
watch(() => props.topicId, () => {})`)
  )
  const result = fixtureRun.run()
  assert.equal(result.status, 0, result.output)
})

test('recognizes renamed, namespace, and vue-demi imports', (t) => {
  const scripts = [
    `import { ref, watch as w } from 'vue'

const props = defineProps<{ topicId: string }>()
const loading = ref(false)
w(() => props.topicId, () => { loading.value = false })`,
    `import { ref } from 'vue'
import * as Vue from 'vue'

const props = defineProps<{ topicId: string }>()
const loading = ref(false)
Vue.watch(() => props.topicId, () => { loading.value = false })`,
    `import { ref, watch } from 'vue-demi'

const props = defineProps<{ topicId: string }>()
const loading = ref(false)
watch(() => props.topicId, () => { loading.value = false })`,
  ]
  for (const script of scripts) {
    const result = split(t, { script, composableSource: droppedComposable }).run()
    assert.equal(result.status, 1, result.output)
    assert.match(result.output, /props\.topicId/)
  }
})

test('matches the scope shapes a call site can pass', (t) => {
  const destructured = split(t, {
    head: `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ topicId: string; other: string }>()
const { loading } = usePanel({ topicId: () => props.topicId, other: () => props.other })`,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel({ topicId: tid }: { topicId: () => string; other: () => string }) {
  const loading = ref(false)
  watch(() => toValue(tid), () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(destructured.status, 0, destructured.output)

  const whole = split(t, {
    head: `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ topicId: string; other: string }>()
const { loading } = usePanel(props)`,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { topicId: string; other: string }) {
  const loading = ref(false)
  watch(() => toValue(scope.topicId), () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(whole.status, 0, whole.output)
})

test('matches a scope property the call site wraps in computed', (t) => {
  const head = `import { computed } from 'vue'
import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ topicId: string; other: string }>()
const { loading } = usePanel({ topicId: computed(() => props.topicId), other: () => props.other })`
  const carried = split(t, {
    head,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { topicId: import('vue').ComputedRef<string>; other: () => string }) {
  const loading = ref(false)
  watch(() => toValue(scope.topicId), () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(carried.status, 0, carried.output)

  const dropped = split(t, { head, composableSource: droppedComposable }).run()
  assert.equal(dropped.status, 1, dropped.output)
})

test('matches a destructured parameter when the call site passes props whole', (t) => {
  const head = `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ topicId: string; other: string }>()
const { loading } = usePanel(props)`
  const carried = split(t, {
    head,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel({ topicId, other }: { topicId: string; other: string }) {
  const loading = ref(false)
  watch(() => toValue(topicId), () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(carried.status, 0, carried.output)
})

test('matches a parameter that is a ref watched through .value', (t) => {
  const script = `import { ref, watch } from 'vue'

const topicId = ref('')
const loading = ref(false)
watch(() => topicId.value, () => { loading.value = false })`
  const head = `import { ref } from 'vue'
import { usePanel } from '@/composables/usePanel'

const topicId = ref('')
const { loading } = usePanel(topicId)`
  const carried = split(t, {
    script,
    head,
    composableSource: `import { ref, watch, type Ref } from 'vue'

export function usePanel(sid: Ref<string>) {
  const loading = ref(false)
  watch(() => sid.value, () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(carried.status, 0, carried.output)

  const dropped = split(t, { script, head, composableSource: undefined }).run()
  assert.equal(dropped.status, 1, dropped.output)
  assert.match(dropped.output, /local to the component/)
})

test('keeps a nested prop path whole on both sides', (t) => {
  // DocEditLayer watches `props.rewrite.phase.value`, a ref held inside a prop. The
  // port has to name the same path, so both sides spell out the trailing `.value`.
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ rewrite: { phase: { value: string } } }>()
const loading = ref(false)
watch(() => props.rewrite.phase.value, () => { loading.value = false })`
  const head = `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ rewrite: { phase: { value: string } } }>()
const { loading } = usePanel({ rewrite: () => props.rewrite })`
  const carried = split(t, {
    script,
    head,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { rewrite: () => { phase: { value: string } } }) {
  const loading = ref(false)
  watch(() => toValue(scope.rewrite).phase.value, () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(carried.status, 0, carried.output)
})

test('does not let a prop object stand in for the value field it watches', (t) => {
  // `props.config` is an object and `props.config.value` is one field of it. Watching
  // the object is not watching the field, so the port must keep the `.value` too.
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ config: { value: string } }>()
const loading = ref(false)
watch(() => props.config.value, () => { loading.value = false })`
  const head = `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ config: { value: string } }>()
const { loading } = usePanel({ config: () => props.config })`
  const carried = split(t, {
    script,
    head,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { config: () => { value: string } }) {
  const loading = ref(false)
  watch(() => toValue(scope.config).value, () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(carried.status, 0, carried.output)

  const dropped = split(t, {
    script,
    head,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { config: () => { value: string } }) {
  const loading = ref(false)
  watch(() => toValue(scope.config), () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(dropped.status, 1, dropped.output)
  assert.match(dropped.output, /config\.value/)
})

test('a ref nested in a prop needs the same path on both sides', (t) => {
  // `props.state.value.topicId` and `props.state.topicId` are different sources.
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ state: { value: { topicId: string } } }>()
const loading = ref(false)
watch(() => props.state.value.topicId, () => { loading.value = false })`
  const head = (source) => `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ state: { value: { topicId: string } } }>()
const { loading } = usePanel({ topicId: () => ${source} })`
  const composableSource = `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { topicId: () => string }) {
  const loading = ref(false)
  watch(() => toValue(scope.topicId), () => { loading.value = false })
  return { loading }
}`
  const carried = split(t, { script, head: head('props.state.value.topicId'), composableSource }).run()
  assert.equal(carried.status, 0, carried.output)

  const dropped = split(t, { script, head: head('props.state.topicId'), composableSource }).run()
  assert.equal(dropped.status, 1, dropped.output)
  assert.match(dropped.output, /state\.value\.topicId/)
})

test('a destructured scope binding keeps the .value it watches', (t) => {
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ config: { value: string } }>()
const loading = ref(false)
watch(() => props.config.value, () => { loading.value = false })`
  const head = `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ config: { value: string } }>()
const { loading } = usePanel({ config: () => props.config })`
  const carried = split(t, {
    script,
    head,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel({ config }: { config: () => { value: string } }) {
  const loading = ref(false)
  watch(() => toValue(config).value, () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(carried.status, 0, carried.output)

  const dropped = split(t, {
    script,
    head,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel({ config }: { config: () => { value: string } }) {
  const loading = ref(false)
  watch(() => toValue(config), () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(dropped.status, 1, dropped.output)
  assert.match(dropped.output, /config\.value/)
})

test('a value parameter handed a prop expression holds the value, not a ref', (t) => {
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ config: { value: string } }>()
const loading = ref(false)
watch(() => props.config.value, () => { loading.value = false })`
  const head = `import { usePanel } from '@/composables/usePanel'

const props = defineProps<{ config: { value: string } }>()
const { loading } = usePanel(() => props.config)`
  const composable = (watched) => `import { ref, toValue, watch } from 'vue'

export function usePanel(config: () => { value: string }) {
  const loading = ref(false)
  watch(() => ${watched}, () => { loading.value = false })
  return { loading }
}`
  const carried = split(t, { script, head, composableSource: composable('toValue(config).value') }).run()
  assert.equal(carried.status, 0, carried.output)

  const dropped = split(t, { script, head, composableSource: composable('toValue(config)') }).run()
  assert.equal(dropped.status, 1, dropped.output)
  assert.match(dropped.output, /config\.value/)
})

test('follows a prop destructured into a local before watching it', (t) => {
  const script = `import { ref, watch } from 'vue'

const props = defineProps<{ topicId: string }>()
const { topicId } = props
const loading = ref(false)
watch(() => topicId, () => { loading.value = false })`
  const carried = split(t, {
    script,
    composableSource: `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { topicId: () => string; other: () => string }) {
  const loading = ref(false)
  watch(() => toValue(scope.topicId), () => { loading.value = false })
  return { loading }
}`,
  }).run()
  assert.equal(carried.status, 0, carried.output)

  const dropped = split(t, { script, composableSource: droppedComposable }).run()
  assert.equal(dropped.status, 1, dropped.output)
})

test('--composable names a target that did not change', (t) => {
  const target = 'frontend/src/lib/panel-helper.ts'
  const fixtureRun = split(t, { head: panelHead, composableSource: undefined })
  fixtureRun.write(
    target,
    `import { ref, toValue, watch } from 'vue'

export function usePanel(scope: { topicId: () => string; other: () => string }) {
  const loading = ref(false)
  watch(() => toValue(scope.topicId), () => { loading.value = false })
  return { loading }
}`
  )
  const result = fixtureRun.run('--composable', target)
  assert.equal(result.status, 0, result.output)
})

test('auto-detects a changed useX.ts outside composables/', (t) => {
  const target = 'frontend/src/lib/usePanelHelper.ts'
  const fixtureRun = split(t, {
    head: `import { usePanelHelper } from '@/lib/usePanelHelper'

const props = defineProps<{ topicId: string; other: string }>()
const { loading } = usePanelHelper({ topicId: () => props.topicId, other: () => props.other })`,
    composableSource: undefined,
  })
  fixtureRun.write(
    target,
    `import { ref, toValue, watch } from 'vue'

export const usePanelHelper = (scope: { topicId: () => string; other: () => string }) => {
  const loading = ref(false)
  watch(() => toValue(scope.topicId), () => { loading.value = false })
  return { loading }
}`
  )
  const result = fixtureRun.run()
  assert.equal(result.status, 0, result.output)
})

test('reports a watchEffect whose body the split dropped', (t) => {
  const dropped = split(t, { script: baseEffectScript, composableSource: droppedComposable })
  const lost = dropped.run()
  assert.equal(lost.status, 1, lost.output)
  assert.match(lost.output, /watchEffect/)
  assert.match(lost.output, /watchEffect body with the code/)

  const carried = split(t, {
    script: baseEffectScript,
    composableSource: `import { ref, toValue, watchEffect } from 'vue'

export function usePanel(scope: { topicId: () => string; other: () => string }) {
  const loading = ref(false)
  watchEffect(() => { if (toValue(scope.topicId)) loading.value = true })
  return { loading }
}`,
  })
  const kept = carried.run()
  assert.equal(kept.status, 0, kept.output)
})

test('ignores a watch that is not the one from vue', (t) => {
  const script = `import { ref } from 'vue'
import { watch } from './local-watch'

const props = defineProps<{ topicId: string }>()
const loading = ref(false)
watch(() => props.topicId, () => { loading.value = false })`
  const { run } = split(t, {
    script,
    composableSource: droppedComposable,
  })
  const result = run()
  assert.equal(result.status, 0, result.output)
})

test('cannot judge when the base ref does not exist', (t) => {
  const { repo } = fixture(t)
  const result = spawnSync(process.execPath, [guard, '--repo', repo, '--base', 'no-such-ref'], {
    env,
    encoding: 'utf8',
    cwd: os.tmpdir(),
  })
  assert.equal(result.status, 2, result.stdout + result.stderr)
  assert.match(result.stdout + result.stderr, /CANNOT JUDGE/)
})
