import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { after, before, test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { analyze, eventName, generate, kebab } from './scene-split-core.mjs'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

// A page the tool can split: a prop, a v-model, an emit, a route param read in
// the script, a child component that moves, and a i18n name the view re-binds.
const WIDGET = `<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'

import BaseButton from './BaseButton.vue'

const route = useRoute()
const { t } = useI18n()
const id = computed(() => route.params.id)
const title = ref('')
const draft = ref('')

function save() {
  title.value = draft.value
}
</script>

<template>
  <div class="widget">
    <h2>{{ t('widget.title') }}</h2>
    <p>{{ title }} {{ id }}</p>
    <BaseButton v-model:draft="draft" @click="save" />
  </div>
</template>
`

const BUTTON = `<script setup lang="ts">
defineProps<{ tone?: string }>()
</script>
<template><button><slot /></button></template>
`

// A page the tool refuses to split mechanically: the template renders a slot.
const SLOTTED = `<script setup lang="ts">
const title = 1
</script>
<template><div><slot name="body">{{ title }}</slot></div></template>
`

// No <script setup>: the split only reads script-setup pages.
const NO_SETUP = `<script lang="ts">
export default { name: 'Legacy' }
</script>
<template><div /></template>
`

const ROUTER = `import { createRouter, createWebHistory } from 'vue-router'

export const routes = [{ path: '/w/:id', name: 'w', component: () => import('../views/Widget.vue') }]

export const router = createRouter({ history: createWebHistory(), routes })
`

let root

function write(rel, text) {
  const p = path.join(root, rel)
  fs.mkdirSync(path.dirname(p), { recursive: true })
  fs.writeFileSync(p, text)
}

before(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'scene-split-'))
  // The type checker resolves `vue` by walking up from the virtual file, so the
  // fixture tree needs a node_modules to walk into.
  fs.symlinkSync(path.join(FRONTEND, 'node_modules'), path.join(root, 'node_modules'))
  write('src/views/Widget.vue', WIDGET)
  write('src/views/BaseButton.vue', BUTTON)
  write('src/views/Slotted.vue', SLOTTED)
  write('src/views/Legacy.vue', NO_SETUP)
  write('src/router/index.ts', ROUTER)
})

after(() => fs.rmSync(root, { recursive: true, force: true }))

const opts = () => ({
  frontendDir: FRONTEND,
  tsconfig: path.join(FRONTEND, 'tsconfig.app.json'),
  srcDir: path.join(root, 'src'),
  routerDir: path.join(root, 'src/router'),
})

const pagePath = (name) => path.join(root, 'src/views', name)

test('kebab and event names drop the on/handle prefix', () => {
  assert.equal(kebab('saveDraft'), 'save-draft')
  assert.equal(kebab('HTMLParser'), 'html-parser')
  assert.equal(eventName('onAvatarPicked'), 'avatar-picked')
  assert.equal(eventName('handleRow'), 'row')
  assert.equal(eventName('save'), 'save')
})

test('a page plans out its props, v-model, emits, moved imports and route reads', () => {
  const plan = analyze(pagePath('Widget.vue'), opts())
  assert.deepEqual(
    plan.props.map((p) => [p.name, p.kind, p.type.type]),
    [
      ['title', 'value', 'string'],
      ['id', 'value', 'string | string[]'],
    ]
  )
  assert.deepEqual(
    plan.models.map((m) => [m.name, m.type.type]),
    [['draft', 'string']]
  )
  assert.deepEqual(
    plan.emits.map((e) => [e.name, e.event]),
    [['save', 'save']]
  )
  assert.deepEqual(plan.i18n, ['t'])
  assert.deepEqual(
    plan.moved.map((m) => m.source),
    ['./BaseButton.vue']
  )
  assert.deepEqual(plan.unsafe, [])
})

test('a plain route.params.X in the script is rewritten and the record gets props', () => {
  const plan = analyze(pagePath('Widget.vue'), opts())
  assert.equal(plan.route.rewritable, true)
  assert.deepEqual(
    plan.route.params.map((p) => p.key),
    ['id']
  )
  assert.deepEqual(
    plan.route.records.map((r) => [r.path, r.hasProps]),
    [['/w/:id', false]]
  )
})

test('the generated view takes props, models and emits and keeps the markup', () => {
  const { view } = generate(analyze(pagePath('Widget.vue'), opts()))
  assert.match(view, /defineProps<\{\n\s+title: string\n\s+id: string \| string\[\]\n\}>\(\)/)
  assert.match(view, /const emit = defineEmits<\{\n\s+save: \[\]\n\}>\(\)/)
  assert.match(view, /const draft = defineModel<string>\('draft', \{ required: true \}\)/)
  assert.match(view, /const \{ t \} = useI18n\(\)/)
  assert.match(view, /import BaseButton from '\.\/BaseButton\.vue'/)
  assert.match(view, /<BaseButton v-model:draft="draft" @click="emit\('save'\)" \/>/)
})

test('the generated page keeps the route, drops what moved, and renders the view', () => {
  const { page, router } = generate(analyze(pagePath('Widget.vue'), opts()))
  assert.match(page, /const props = defineProps<\{ id: string \}>\(\)/)
  assert.match(page, /const id = computed\(\(\) => props\.id\)/)
  assert.doesNotMatch(page, /useRoute|route\.params/)
  assert.doesNotMatch(page, /BaseButton/)
  assert.match(page, /<WidgetView\n\s+:title="title"\n\s+:id="id"\n\s+v-model:draft="draft"\n\s+@save="save"\n\s+\/>/)
  assert.match(router.text, /component: \(\) => import\('\.\.\/views\/Widget\.vue'\), props: true/)
})

test('a slot in the template is reported, not guessed', () => {
  const plan = analyze(pagePath('Slotted.vue'), opts())
  assert.ok(
    plan.unsafe.some((u) => /<slot>/.test(u.what)),
    JSON.stringify(plan.unsafe)
  )
})

test('a page without <script setup> is refused outright', () => {
  assert.throws(() => analyze(pagePath('Legacy.vue'), opts()), /no <script setup>/)
})
