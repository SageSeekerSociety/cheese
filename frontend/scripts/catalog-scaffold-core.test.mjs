import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  bindingNames,
  guessNeeds,
  idOf,
  placeholder,
  propsOf,
  scaffold,
  TODO,
  TODO_IMPORT,
} from './catalog-scaffold-core.mjs'

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

test('placeholders are of the declared kind, nullable is null, and every one carries the marker', () => {
  assert.equal(placeholder({ name: 'title', types: ['String'] }), `'${TODO}'`)
  assert.equal(placeholder({ name: 'error', types: ['String', 'null'] }), 'todo(null)')
  assert.equal(placeholder({ name: 'count', types: ['Number'] }), 'todo(0)')
  assert.equal(placeholder({ name: 'open', types: ['Boolean'] }), 'todo(false)')
  assert.equal(placeholder({ name: 'rows', types: ['Array'] }), 'todo([])')
  assert.equal(placeholder({ name: 'onPick', types: ['Function'] }), 'todo(() => {})')
  assert.equal(placeholder({ name: 'meta', types: ['Object'] }), 'todo({})')
  assert.equal(placeholder({ name: 'anything', types: [] }), 'todo(undefined)')
})

test('the todo import points at the module that defines it', async () => {
  const { readFile } = await import('node:fs/promises')
  const from = /from '\.\/(\w+)'/.exec(TODO_IMPORT)[1]
  const source = await readFile(new URL(`../src/views/demo/${from}.ts`, import.meta.url), 'utf8')
  assert.match(source, /export function todo</)
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
  assert.match(entry, /rows: todo\(\[\]\),/)
  assert.match(entry, /error: todo\(null\),/)
  assert.doesNotMatch(entry, /count: /)
  assert.match(entry, /\/\/ optional: count/)
  // about、格名、格的说明三句，加上 title 那个字符串参数。
  assert.equal(entry.split(TODO).length - 1, 4)
})

test('every required arg the skeleton writes is marked, so filling in the sentences alone is not enough', () => {
  const { entry, usesTodo } = scaffold({ file: 'src/components/x/Typed.vue', source: TYPED, fs: noFs })
  const args = /args: \{\n([\s\S]*?)\n {4}\},/
    .exec(entry)[1]
    .split('\n')
    .filter((line) => !line.includes('//'))
  assert.equal(args.length, 4)
  for (const line of args) assert.match(line, new RegExp(`todo\\(|'${TODO.replace(/[()]/g, '\\$&')}'`))
  assert.equal(usesTodo, true)
  const plain = scaffold({
    file: 'src/P.vue',
    source: '<script setup lang="ts">defineProps<{ label: string }>()</script>',
    fs: noFs,
  })
  assert.equal(plain.usesTodo, false)
})

test('binding names are legal identifiers and unique across a batch', () => {
  assert.deepEqual(
    bindingNames([
      'src/views/404.vue',
      'src/proto-shell.vue',
      'src/views/account/recover/password/StartView.vue',
      'src/views/account/signup/StartView.vue',
      'src/components/panels/PanelThreads.vue',
    ]),
    ['Views404', 'ProtoShell', 'PasswordStartView', 'SignupStartView', 'PanelThreads']
  )
})
