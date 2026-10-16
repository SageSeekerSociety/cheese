// Tests for the global-fallback rule. Run by `pnpm run test:ratchet`
// (node --test scripts/*.test.mjs), locally and in frontend.yml's `check` job.
//
// Three kinds of case, and the third is the one that matters:
//
//   1. the pure functions — which declarations are internal to a module, what
//      the file walk selects and skips, the counting and the report;
//   2. what the rule fires on and what it must leave alone, driven with source
//      fixtures and a declarations map;
//   3. the plant — a composable that keeps a function to itself and a component
//      that calls it by bare name are written into the real tree, and the real
//      check is required to go red on that pair, and green once the `export`
//      and the `import` are there. A rule nobody has watched fire is not a
//      rule, and this one's whole subject is a failure that is silent: the
//      call does not throw, it reaches `window.open`.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'

import {
  SCRIPT,
  TEMPLATE,
  collectDefinitions,
  collectFiles,
  countByFile,
  findGlobalFallbacks,
  formatGlobalFallbackReport,
  internalDeclarations,
  unboundCalls,
} from './global-fallback-ratchet-core.mjs'
import { compare } from './tsc-ratchet-core.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const CHECK = resolve(HERE, 'check-global-fallback-ratchet.mjs')

/** A declarations map as `judgeTree` builds it: internal name -> modules declaring it. */
const declaring = (...modules) => collectDefinitions(modules)

const COMPONENT = 'src/components/Probe.vue'
const MODULE = 'src/composables/useProbe.ts'

const script = (code) => `<script setup lang="ts">\n${code}</script>\n<template><div /></template>\n`
const template = (body) => `<script setup lang="ts">\nconst n = 1\nvoid n\n</script>\n<template>${body}</template>\n`

// ---------------------------------------------------------------------------
// 1. What a module keeps to itself, and which files are read at all.
// ---------------------------------------------------------------------------

describe('internalDeclarations', () => {
  it('keeps the declarations no export hands out', () => {
    const code = ['function blur() {}', 'const open = () => {}', 'let status = 1', 'class Panel {}'].join('\n')
    assert.deepEqual(internalDeclarations(code), ['blur', 'open', 'status', 'Panel'])
  })

  // A type is not a binding anything can be called through, and a module that
  // keeps a type to itself is not what this rule is about: the call site is
  // judged the same either way, and naming a type in the report would send the
  // reader to a line with nothing to export.
  it('says nothing about a type, so the report never points at one', () => {
    assert.deepEqual(internalDeclarations('interface Props { a: string }\ntype Id = string\n'), [])
  })

  it('drops every name an export hands out', () => {
    const forms = [
      ['export function open() {}', []],
      ['export const open = () => {}', []],
      ['const open = () => {}\nexport { open }', []],
      ['const open = () => {}\nexport { open as toggle }', []],
      ['export default function open() {}', []],
      ['function open() {}\nexport default open', ['open']],
      ['export const a = 1\nexport { a, b }\nfunction b() {}', []],
    ]
    for (const [code, expected] of forms) {
      assert.deepEqual(internalDeclarations(code), expected, `wrong for:\n${code}`)
    }
  })

  it('lists every internal name, in declaration order', () => {
    assert.deepEqual(internalDeclarations('const b = 1\nfunction a() {}\nconst b2 = () => b\n'), ['b', 'a', 'b2'])
  })
})

describe('collectDefinitions', () => {
  it('maps a name to every module that keeps it, sorted, relative to the root', () => {
    const definitions = declaring(
      { path: 'src/composables/useB.ts', code: 'function open() {}' },
      { path: 'src/composables/useA.ts', code: 'function open() {}\nfunction close() {}' },
      { path: 'src/lib/x.ts', code: 'export function open() {}' }
    )
    assert.deepEqual(definitions.get('open'), ['src/composables/useA.ts', 'src/composables/useB.ts'])
    assert.deepEqual(definitions.get('close'), ['src/composables/useA.ts'])
  })
})

describe('collectFiles', () => {
  it('selects what the patterns name and skips what the ignores name', () => {
    const found = collectFiles(['src/**/*.ts'], {
      root: ROOT,
      ignores: ['**/*.spec.ts', '**/*.test.ts'],
    })
    assert.ok(found.length > 100, `expected the tree, got ${found.length} files`)
    assert.ok(found.every((path) => path.endsWith('.ts')))
    assert.ok(found.every((path) => !path.endsWith('.spec.ts') && !path.endsWith('.test.ts')))
    assert.ok(found.includes('src/composables/useDesignViewport.ts'))
    // Nothing from node_modules, however deep the walk goes.
    assert.ok(!found.some((path) => path.includes('node_modules')))
  })

  it('reads a nested name too — the components this rule judges are not all at one depth', () => {
    const found = collectFiles(['src/**/*.vue'], { root: ROOT })
    assert.ok(found.includes('src/components/panels/ChangesDiff.vue'), 'a component three directories down')
    assert.ok(found.every((path) => path.endsWith('.vue')))
  })

  it('leaves a spec component out of the call sites', () => {
    const all = collectFiles(['src/**/*.vue'], { root: ROOT })
    const withoutSpecs = collectFiles(['src/**/*.vue'], { root: ROOT, ignores: ['**/*.spec.vue'] })
    assert.deepEqual(withoutSpecs, all.filter((path) => !path.endsWith('.spec.vue')))
  })
})

describe('countByFile and the report', () => {
  const result = (count) => compare({}, count)

  it('counts findings per component', () => {
    assert.deepEqual(countByFile([{ file: 'a.vue' }, { file: 'a.vue' }, { file: 'b.vue' }]), { 'a.vue': 2, 'b.vue': 1 })
  })

  it('names both files, the call site and the fix', () => {
    const violation = {
      file: COMPONENT,
      line: 2,
      column: 1,
      name: 'open',
      where: SCRIPT,
      definedIn: MODULE,
    }
    const text = formatGlobalFallbackReport(result(countByFile([violation])), [violation])
    assert.match(text, /Probe\.vue:2:1 {2}open\(\) {2}\[script setup\]/)
    assert.match(text, /useProbe\.ts declares it and exports it to nobody/)
    assert.match(text, /total: 1 call\(s\)/)
  })

  it('says so plainly when there is nothing to report', () => {
    const text = formatGlobalFallbackReport(result({}))
    assert.match(text, /No call falls back/)
    assert.doesNotMatch(text, /export that name/)
  })
})

// ---------------------------------------------------------------------------
// 2. What the rule fires on, and what it must leave alone.
// ---------------------------------------------------------------------------

describe('findGlobalFallbacks', () => {
  const definitions = declaring({ path: MODULE, code: 'function open() {}\nfunction close() {}\nfunction blur() {}\nfunction scrollToBottom() {}\nfunction rowTo() {}\nfunction nameOf() {}' })
  const found = (code, file = COMPONENT) => findGlobalFallbacks(code, file, definitions)

  it('fires on a bare call in <script setup>', () => {
    const hits = found(script('open()\n'))
    assert.equal(hits.length, 1)
    assert.deepEqual(
      { name: hits[0].name, line: hits[0].line, where: hits[0].where, definedIn: hits[0].definedIn, file: hits[0].file },
      { name: 'open', line: 2, where: SCRIPT, definedIn: MODULE, file: COMPONENT }
    )
  })

  // The #1618 case, in the form it actually took: the call is inside a
  // conditional, not at the top of setup.
  it('fires on a call inside a branch, and names every one of them', () => {
    const hits = found(script('const on = ref(false)\nfunction toggle() {\n  if (!on.value) blur()\n  close()\n}\n'))
    assert.deepEqual(hits.map((hit) => [hit.name, hit.line, hit.where]), [
      ['blur', 4, SCRIPT],
      ['close', 5, SCRIPT],
    ])
  })

  it('fires on a bare call in the template', () => {
    const hits = found(template('<button @click="scrollToBottom()">top</button>'))
    assert.equal(hits.length, 1)
    assert.equal(hits[0].name, 'scrollToBottom')
    assert.equal(hits[0].where, TEMPLATE)
    assert.equal(hits[0].line, 5)
  })

  // Part of the tree's own vocabulary, and the shape a reader will reach for:
  // the name is the module's, not the global's, and the call is missing an
  // import rather than a `window.`.
  it('fires on a template call inside a directive value and an interpolation alike', () => {
    const hits = found(template('<div v-if="open()">{{ close() }}</div>'))
    assert.deepEqual(hits.map((hit) => [hit.name, hit.where]), [
      ['open', TEMPLATE],
      ['close', TEMPLATE],
    ])
  })

  it('leaves a spell-out global alone', () => {
    assert.deepEqual(found(script('window.open()\nel.focus()\nthis.close()\n')), [])
    assert.deepEqual(found(template('<button @click="window.print()">print</button>')), [])
  })

  it('leaves a call the component imported alone', () => {
    assert.deepEqual(found(script("import { open } from '@/composables/useProbe'\nopen()\n")), [])
    assert.deepEqual(found(script("import { open as show } from '@/composables/useProbe'\nshow()\n")), [])
  })

  it('leaves a call the component declared itself alone', () => {
    assert.deepEqual(found(script('const open = () => {}\nopen()\n')), [])
    assert.deepEqual(found(script('function open() {}\nopen()\n')), [])
  })

  it('leaves a name no module in this tree keeps alone', () => {
    // `scrollBy` is a browser global and no module here declares it: the author
    // meant the global, or is calling something that never existed. Neither is
    // a missing `export`, and this rule does not re-state `no-undef`.
    assert.deepEqual(found(script('scrollBy(0, 10)\n')), [])
  })

  // The false positive the loose half exists to avoid, in the shape it has in
  // the tree today — `isMine` in ChatTimeline.vue and `rowTo` in
  // TasksView.vue: a template calls a prop by name, and the prop is declared in
  // the component's own `defineProps` type literal, which is not a binding.
  it('leaves a prop called by name in the template alone', () => {
    const code = [
      '<script setup lang="ts">',
      'const props = defineProps<{ nameOf: (x: string) => string; rowTo: string }>()',
      'void props',
      '</script>',
      '<template>',
      '  <span>{{ nameOf(rowTo) }}</span>',
      '</template>',
      '',
    ].join('\n')
    assert.deepEqual(found(code), [])
  })

  it('leaves a method and a local declared in the script alone, called from the template', () => {
    const code = [
      '<script setup lang="ts">',
      'const items = ref<string[]>([])',
      'function rowTo(item: string) { return item }',
      '</script>',
      '<template>',
      '  <li v-for="open in items" :key="open" @click="rowTo(open)">{{ open }}</li>',
      '</template>',
      '',
    ].join('\n')
    assert.deepEqual(found(code), [])
  })

  it('leaves a v-for alias alone even when a module keeps that name', () => {
    const code = template('<li v-for="open in items" :key="open" @click="open()">{{ open }}</li>')
    assert.deepEqual(found(code), [])
  })

  it('leaves a slot alias alone', () => {
    const code = template('<template #default="{ open }"><span @click="open()">{{ open }}</span></template>')
    assert.deepEqual(found(code), [])
  })

  it('does not fire twice on the same call', () => {
    const hits = found(template('<div @click="open()">{{ open() }}</div>'))
    assert.deepEqual(hits.map((hit) => hit.line), [5, 5])
    assert.notEqual(hits[0].column, hits[1].column)
  })

  // Only a bare identifier callee is judged: a method on an object the
  // component does hold is the component's business, and `no-undef` already
  // covers the object.
  it('judges the callee, not every identifier in the expression', () => {
    assert.deepEqual(found(script('const api = { open: () => {} }\napi.open()\n')), [])
    assert.deepEqual(found(template('<button @click="handlers[0]()">x</button>')), [])
  })
})

describe('unboundCalls', () => {
  it('reports the two halves separately, each with its own location', () => {
    const code = [
      '<script setup lang="ts">',
      'open()',
      '</script>',
      '<template>',
      '  <button @click="close()">x</button>',
      '</template>',
      '',
    ].join('\n')
    const calls = unboundCalls(code, COMPONENT)
    assert.deepEqual(calls.map((call) => [call.name, call.line, call.where]), [
      ['open', 2, SCRIPT],
      ['close', 5, TEMPLATE],
    ])
  })
})

// ---------------------------------------------------------------------------
// 3. The plant. Real files, in the real tree, judged by the real check.
// ---------------------------------------------------------------------------

describe('the pair a refactor leaves behind', () => {
  const MODULE_PATH = 'src/composables/__globalFallbackProbe.ts'
  const COMPONENT_PATH = 'src/components/__globalFallbackProbe.vue'
  const MODULE_FILE = resolve(ROOT, MODULE_PATH)
  const COMPONENT_FILE = resolve(ROOT, COMPONENT_PATH)

  // The composable a refactor leaves behind: the two functions live here, and
  // the module hands neither of them out. `scrollToBottom` is not a browser
  // global — calling it unbound throws. `open` is, so calling it unbound does
  // nothing at all, which is the failure this rule exists to end.
  const composable = [
    'export function useScrollProbe() { return [scrollToBottom, open] }',
    'function scrollToBottom() { window.scrollTo(0, 0) }',
    'function open() {}',
    '',
  ].join('\n')

  const component = (call) => [
    '<script setup lang="ts">',
    'const n = 1',
    'void n',
    '</script>',
    '<template>',
    `  <button @click="${call}">x</button>`,
    '</template>',
    '',
  ].join('\n')

  after(() => {
    rmSync(MODULE_FILE, { force: true })
    rmSync(COMPONENT_FILE, { force: true })
  })

  const run = (...args) =>
    spawnSync(process.execPath, [CHECK, '--sources', MODULE_PATH, '--files', COMPONENT_PATH, ...args], {
      cwd: ROOT,
      encoding: 'utf8',
    })

  const write = (moduleSource, componentSource) => {
    writeFileSync(MODULE_FILE, moduleSource)
    writeFileSync(COMPONENT_FILE, componentSource)
  }

  it('refuses a component calling a composable function that was never exported', () => {
    try {
      write(composable, component('open()'))

      const blocked = run()
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      // The call site, the name, where in the component it is, and the file
      // that forgot the export — the four things a reader needs to fix it.
      assert.match(blocked.stdout, /__globalFallbackProbe\.vue:6:19 {2}open\(\) {2}\[template\]/)
      assert.match(blocked.stdout, /__globalFallbackProbe\.ts declares it and exports it to nobody/)
      assert.match(blocked.stdout, /Either export that name from the module and import it here/)
      assert.match(blocked.stdout, /total: 1 call\(s\)/)
    } finally {
      rmSync(MODULE_FILE, { force: true })
      rmSync(COMPONENT_FILE, { force: true })
    }
  })

  // `window.open` is why the pre-existing checks say nothing: eslint's config
  // injects the browser globals and turns `no-undef` off for TS. This is the
  // premise against which the whole rule is justified, so it is asserted here
  // rather than only argued in the core's header — and if the repo ever closes
  // that blind spot, this is where the rule's reason for existing comes back
  // for a visit.
  it('is the only check that sees it: eslint passes the same component', async () => {
    // Linted as a path inside the tree, through the config the repo already
    // runs, so the browser globals are injected exactly as they are for a real
    // component.
    const eslint = new ESLint({ cwd: ROOT })
    const [result] = await eslint.lintText(component('open()'), { filePath: COMPONENT_FILE })
    assert.deepEqual(
      result.messages.map((message) => `${message.ruleId ?? 'fatal'}: ${message.message}`),
      [],
      'the base eslint config now reports a bare call to a browser global — either the ' +
        'blind spot this rule exists for has closed, or it has moved'
    )
  })

  it('refuses a call in <script setup> too, and counts both halves', () => {
    try {
      write(composable, component('open()').replace('const n = 1\nvoid n', 'scrollToBottom()'))

      const blocked = run()
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      assert.match(blocked.stdout, /__globalFallbackProbe\.vue:2:1 {2}scrollToBottom\(\) {2}\[script setup\]/)
      assert.match(blocked.stdout, /total: 2 call\(s\)/)
    } finally {
      rmSync(MODULE_FILE, { force: true })
      rmSync(COMPONENT_FILE, { force: true })
    }
  })

  it('accepts the same pair once the export and the import are there', () => {
    try {
      write(
        composable.replace('function open()', 'export function open()'),
        component('open()').replace('const n = 1', "import { open } from '../composables/__globalFallbackProbe'\nvoid open")
      )
      const clean = run()
      assert.equal(clean.status, 0, `${clean.stdout}${clean.stderr}`)
      assert.match(clean.stdout, /No call falls back/)
    } finally {
      rmSync(MODULE_FILE, { force: true })
      rmSync(COMPONENT_FILE, { force: true })
    }
  })

  // The other correct spelling: the call was always meant to be the global, so
  // it says so. A rule that refused this would be `no-undef` under another name.
  it('accepts the call once the component says `window.`', () => {
    try {
      write(composable, component('window.open()'))
      const clean = run()
      assert.equal(clean.status, 0, `${clean.stdout}${clean.stderr}`)
    } finally {
      rmSync(MODULE_FILE, { force: true })
      rmSync(COMPONENT_FILE, { force: true })
    }
  })

  // A file the parser refuses is not a file with no finding. There is no
  // baseline here, so the record's `frozen` is 0 on every run and the verdict
  // is the finding itself; the exit code and the record must agree, which is
  // the one thing a collector reads both of.
  it('reports one JSON record whose status agrees with the exit code', () => {
    try {
      write(composable, component('open()'))
      const failed = run('--json')
      assert.equal(failed.status, 1, `${failed.stdout}${failed.stderr}`)
      const record = JSON.parse(failed.stdout)
      assert.equal(record.id, 'fe-global-fallback')
      assert.equal(record.status, 'fail')
      assert.equal(record.actual, 1)
      assert.equal(record.frozen, 0)
      assert.deepEqual(record.stale, [])
      assert.deepEqual(record.details, [
        { file: COMPONENT_PATH, line: 6, column: 19, name: 'open', where: TEMPLATE, definedIn: MODULE_PATH },
      ])
      // The human report goes to stderr under --json, so a failing run still
      // names the call and the fix to whoever reads the log.
      assert.match(failed.stderr, /__globalFallbackProbe\.ts declares it and exports it to nobody/)
    } finally {
      rmSync(MODULE_FILE, { force: true })
      rmSync(COMPONENT_FILE, { force: true })
    }
  })

  // A component the parser refuses is not a component with no finding. Exit 2
  // says so, and never 0.
  //
  // Two shapes, because they fail in two different places and a rule can lose
  // one of them without noticing: a template expression the parser cannot read
  // (reported on the template body — the file is read as far as it can be, and
  // the half it could not read is exactly where a lost binding would be) and a
  // `<script>` that does not parse at all (the parser throws). A template that
  // is merely malformed markup is a third thing and is *not* here: the tree has
  // hundreds of those, they read from end to end, and failing on them would
  // leave this check unable to judge anything at all.
  it('could not judge rather than pass when a component will not parse', () => {
    const unreadable = [
      '<script setup lang="ts">\nconst n = 1\nvoid n\n</script>\n<template>\n  <div>{{ ) }}</div>\n</template>\n',
      '<script setup lang="ts">\nconst = =\n</script>\n<template><div /></template>\n',
    ]
    try {
      for (const broken of unreadable) {
        write(composable, broken)
        const refused = run()
        assert.equal(refused.status, 2, `${refused.stdout}${refused.stderr}`)
        assert.match(refused.stderr, /could not be parsed/)
      }
    } finally {
      rmSync(MODULE_FILE, { force: true })
      rmSync(COMPONENT_FILE, { force: true })
    }
  })
})
