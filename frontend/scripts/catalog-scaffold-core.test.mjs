import assert from 'node:assert/strict'
import { test } from 'node:test'

import { guessNeeds, idOf, placeholder, propsOf, scaffold, TODO } from './catalog-scaffold-core.mjs'

const noFs = { fileExists: () => false, readFile: () => undefined }

const TYPED = `<script setup lang="ts">
import { useI18n } from 'vue-i18n'
interface Row { id: string }
const props = withDefaults(defineProps<{ rows: Row[]; title: string; error: string | null; count?: number; onPick: (id: string) => void }>(), { count: 3 })
const { t } = useI18n()
</script>
<template><v-list>{{ title }}</v-list></template>`

test('a type-only defineProps is read through the compiler, required and optional apart', () => {
  const props = propsOf(TYPED, 'Typed.vue', noFs)
  assert.deepEqual(
    props.map((p) => [p.name, p.required]),
    [
      ['rows', true],
      ['title', true],
      ['error', true],
      ['count', false],
      ['onPick', true],
    ]
  )
  assert.deepEqual(props.find((p) => p.name === 'error').types, ['String', 'null'])
})

test('imported prop types are resolved through the fs the caller hands in', () => {
  const files = { '/x/types.ts': 'export interface P { label: string; open?: boolean }' }
  const fs = { fileExists: (p) => p in files, readFile: (p) => files[p] }
  const source = `<script setup lang="ts">
import type { P } from './types'
defineProps<P>()
</script><template><b /></template>`
  assert.deepEqual(
    propsOf(source, '/x/Imported.vue', fs).map((p) => [p.name, p.required]),
    [
      ['label', true],
      ['open', false],
    ]
  )
})

test('the runtime-object form and a component with no props both work', () => {
  const runtime = `<script setup>defineProps({ size: { type: Number, required: true }, tone: String })</script><template><i /></template>`
  assert.deepEqual(
    propsOf(runtime, 'R.vue', noFs).map((p) => [p.name, p.types, p.required]),
    [
      ['size', ['Number'], true],
      ['tone', ['String'], false],
    ]
  )
  assert.deepEqual(propsOf('<template><hr /></template>', 'Plain.vue', noFs), [])
})

test('placeholders are of the declared kind, and nullable is null', () => {
  assert.equal(placeholder({ name: 'title', types: ['String'] }), "'title'")
  assert.equal(placeholder({ name: 'error', types: ['String', 'null'] }), 'null')
  assert.equal(placeholder({ name: 'rows', types: ['Array'] }), '[]')
  assert.equal(placeholder({ name: 'onPick', types: ['Function'] }), '() => {}')
})

test('needs are guessed from what the source visibly uses', () => {
  assert.deepEqual(guessNeeds(TYPED), ['vuetify', 'i18n'])
  assert.deepEqual(guessNeeds('<template><b>x</b></template>'), [])
})

test('ids are kebab case, acronyms included', () => {
  assert.equal(idOf('src/components/panels/PanelThreads.vue'), 'panel-threads')
  assert.equal(idOf('src/components/HTMLView.vue'), 'html-view')
})

test('the skeleton fills required args only and marks every sentence a person owes', () => {
  const { importLine, entry } = scaffold({ file: 'src/components/x/Typed.vue', source: TYPED, fs: noFs })
  assert.equal(importLine, "import Typed from '@/components/x/Typed.vue'")
  assert.match(entry, /rows: \[\],/)
  assert.match(entry, /error: null,/)
  assert.doesNotMatch(entry, /count: /)
  assert.match(entry, /\/\/ optional: count/)
  assert.equal(entry.split(TODO).length - 1, 3)
})
