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
//      check is required to go red on that pair, and green once the name is
//      handed out and taken. Both shapes are planted, because the refactor this
//      rule exists for took the second: a function at module level that was
//      never exported, and — the #1618 shape — a function inside the
//      composable's own body that was never put in its `return`. A rule nobody
//      has watched fire is not a rule, and this one's whole subject is a failure
//      that is silent: the call does not throw, it reaches `window.open`.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { after, describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

import { ESLint } from 'eslint'

import {
  collectDefinitions,
  collectFiles,
  countByFile,
  findGlobalFallbacks,
  formatGlobalFallbackReport,
  internalDeclarations,
  SCRIPT,
  TEMPLATE,
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

/**
 * The composable a refactor leaves behind in two shapes, both of which keep the
 * two functions to themselves.
 *
 * `module level`: declared at the top and never exported — the shape the first
 * version of this rule could see. `inside the composable`: declared in the
 * body and never put in the `return` — the shape #1618 actually took, and the
 * one a rule reading only top-level statements judged clean.
 *
 * `scrollToBottom` is not a browser global; calling it unbound throws.
 * `open` is one, so calling it unbound does nothing at all — the failure this
 * rule exists to end.
 */
const composable = {
  'module level': {
    kept: [
      'function scrollToBottom() { window.scrollTo(0, 0) }',
      'function open() {}',
      'export function useScrollProbe() {}',
      '',
    ].join('\n'),
    fixed: [
      'export function scrollToBottom() { window.scrollTo(0, 0) }',
      'export function open() {}',
      'export function useScrollProbe() {}',
      '',
    ].join('\n'),
    // Taken by name, which is the whole repair for this shape.
    taken: "import { open } from '../composables/__globalFallbackProbe'",
  },
  'inside the composable': {
    kept: [
      'export function useScrollProbe() {',
      '  function scrollToBottom() { window.scrollTo(0, 0) }',
      '  function open() {}',
      '  return {}',
      '}',
      '',
    ].join('\n'),
    // Handed back through the composable's `return`, and taken out of the call
    // that returns it. A `return` is not an export — the module still lists the
    // name as internal — so the guard's own half of the repair is the `taken`
    // line, and `vue-tsc` is what refuses a return that omits the name.
    fixed: [
      'export function useScrollProbe() {',
      '  function scrollToBottom() { window.scrollTo(0, 0) }',
      '  function open() {}',
      '  return { open }',
      '}',
      '',
    ].join('\n'),
    taken: [
      "import { useScrollProbe } from '../composables/__globalFallbackProbe'",
      'const { open } = useScrollProbe()',
    ].join('\n'),
  },
}

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

  it('lists every internal name, in declaration order, each once', () => {
    assert.deepEqual(internalDeclarations('const b = 1\nfunction a() {}\nconst b2 = () => b\n'), ['b', 'a', 'b2'])
  })

  // The #1618 shape, and the reason the walk reads the whole module rather than
  // its top-level statements: `blur` sat in the body of `useDesignViewport`,
  // was never put in its `return`, and a rule that read `ast.body` alone judged
  // that very file clean. `close` is here beside it because a `return` is not an
  // export: a component calling `close()` bare is the same bug as `blur()`.
  it('keeps a declaration made inside a function too', () => {
    const code = [
      'export function useDesignViewport() {',
      '  function blur() {}',
      '  const close = () => {}',
      '  return { close }',
      '}',
      '',
    ].join('\n')
    assert.deepEqual(internalDeclarations(code), ['blur', 'close'])
  })

  // A namespace's members are reached as `NS.member`, never bare, so they are
  // not names this rule may index: every one of them is exported, and listing
  // them would have the report blame a module for keeping a name it hands out.
  it('leaves a namespace body out of the index', () => {
    assert.deepEqual(internalDeclarations('export namespace TeamsApi {\n  export function create() {}\n}\n'), [])
    assert.deepEqual(internalDeclarations('namespace Hidden {\n  function open() {}\n}\n'), [])
  })

  // What hands a name out is an `export` of that name and nothing else. The
  // composable above returns `close`, and this file exports `outer`, whose body
  // declares `focus`: neither makes the nested name reachable from another
  // module, so a component calling either one bare is still the call this rule
  // is for.
  it('hands out only the names an export names, never the ones under them', () => {
    const code = ['function outer() {', '  function focus() {}', '  return focus', '}', 'export { outer }'].join('\n')
    assert.deepEqual(internalDeclarations(code), ['focus'])
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
    assert.deepEqual(
      withoutSpecs,
      all.filter((path) => !path.endsWith('.spec.vue'))
    )
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
    assert.match(text, /useProbe\.ts declares it and never hands it out/)
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
  const definitions = declaring({
    path: MODULE,
    code: 'function open() {}\nfunction close() {}\nfunction blur() {}\nfunction scrollToBottom() {}\nfunction rowTo() {}\nfunction nameOf() {}',
  })
  const found = (code, file = COMPONENT) => findGlobalFallbacks(code, file, definitions)

  it('fires on a bare call in <script setup>', () => {
    const hits = found(script('open()\n'))
    assert.equal(hits.length, 1)
    assert.deepEqual(
      {
        name: hits[0].name,
        line: hits[0].line,
        where: hits[0].where,
        definedIn: hits[0].definedIn,
        file: hits[0].file,
      },
      { name: 'open', line: 2, where: SCRIPT, definedIn: MODULE, file: COMPONENT }
    )
  })

  // The #1618 case, in the form it actually took: the call is inside a
  // conditional, not at the top of setup.
  it('fires on a call inside a branch, and names every one of them', () => {
    const hits = found(script('const on = ref(false)\nfunction toggle() {\n  if (!on.value) blur()\n  close()\n}\n'))
    assert.deepEqual(
      hits.map((hit) => [hit.name, hit.line, hit.where]),
      [
        ['blur', 4, SCRIPT],
        ['close', 5, SCRIPT],
      ]
    )
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
    assert.deepEqual(
      hits.map((hit) => [hit.name, hit.where]),
      [
        ['open', TEMPLATE],
        ['close', TEMPLATE],
      ]
    )
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

  // The runtime form of the one macro whose argument names are bindings:
  // `defineProps(['open'])` makes `open` a name the template can call, the same
  // way the type form above does through its property keys.
  it('leaves a prop declared with the runtime form of defineProps alone', () => {
    const code = [
      '<script setup lang="ts">',
      "const props = defineProps(['open', 'close'])",
      'void props',
      '</script>',
      '<template>',
      '  <button @click="open()">x</button>',
      '</template>',
      '',
    ].join('\n')
    assert.deepEqual(found(code), [])
  })

  // The same declaration written with the name quoted, or computed, or in the
  // object the macro takes. A string is a binding when it sits where a prop
  // name sits; the three spellings below all declare a prop called `open`, and
  // refusing them would block a legal component in a rule that has no baseline.
  it('leaves a prop alone however its name is written', () => {
    for (const declaration of [
      "const props = defineProps({ 'open': Function })",
      "const props = defineProps({ ['open']: Function })",
      'const props = defineProps({ open: Function })',
      'const props = defineProps<{ open: () => void }>()',
      "const props = defineProps(['open'] as const)",
      "const props = defineProps(['open'] satisfies string[])",
    ]) {
      const code = [
        '<script setup lang="ts">',
        declaration,
        'void props',
        '</script>',
        '<template>',
        '  <button @click="open()">x</button>',
        '</template>',
        '',
      ].join('\n')
      assert.deepEqual(found(code), [], `reported a prop this declaration binds:\n${declaration}`)
    }
  })

  // The Options API declares the same names in a different place, and a
  // component written that way gets the same answer however its options object
  // reaches the default export.
  it('reads the Options API forms of the same declarations', () => {
    const component = (script) =>
      [
        '<script lang="ts">',
        script,
        '</script>',
        '<template>',
        '  <button @click="open()">x</button>',
        '</template>',
        '',
      ].join('\n')
    for (const options of [
      "props: ['open']",
      "props: { 'open': Function }",
      'props: { open: Function }',
      'methods: { open() {} }',
      // A quoted key is the same key as an unquoted one, in any container the
      // template reaches through the component's own options.
      "methods: { 'open'() {} }",
      "computed: { 'open': () => true }",
      "inject: { 'open': 'openKey' }",
      "data() { return { 'open': 1 } }",
      "setup() { return { 'open': () => {} } }",
      "mixins: [{ props: ['open'] }]",
      "extends: { props: ['open'] }",
    ]) {
      assert.deepEqual(
        found(component(`export default { ${options} }`)),
        [],
        `reported an option this declares:\n${options}`
      )
    }
    for (const script of [
      "export default defineComponent({ props: ['open'] })",
      "export default defineComponent({ methods: { 'open'() {} } })",
      "export default { props: ['open'] } satisfies ComponentOptions",
      "export default { props: ['open'] } as const",
      "const options = { props: ['open'] }\nexport default options",
      "const options = defineComponent({ props: ['open'] })\nexport default options",
    ]) {
      assert.deepEqual(
        found(component(`${script}\nvoid defineComponent`)),
        [],
        `reported options that reach the default export:\n${script}`
      )
    }
  })

  // The same declarations reach the template through one more hop of ordinary
  // code: a container or a mixin held in a variable, an options object passed to
  // `defineComponent`, a builder wrapped around it, or the object a `setup`
  // function returns by name. Every one of those still binds the name, so every
  // one of them must answer "clean" — an extra binding only loses a finding,
  // while failing to see one turns a legal component red.
  it('follows a declaration through the variable or wrapper it is written as', () => {
    const component = (script) =>
      [
        '<script lang="ts">',
        script,
        'void defineComponent',
        '</script>',
        '<template>',
        '  <button @click="open()">x</button>',
        '</template>',
        '',
      ].join('\n')
    for (const script of [
      "const propsDef = ['open']\nexport default { props: propsDef }",
      // A declaration an `export` wraps is still a module-level binding, and
      // `var` written in a top-level block is hoisted out of it.
      "export const propsDef = ['open']\nexport default { props: propsDef }",
      "export const options = { props: ['open'] }\nexport default options",
      "if (true) { var opts = { props: ['open'] } }\nexport default opts",
      "const Base = defineComponent({ props: ['open'] })\nexport default { extends: Base }",
      "const Base = defineComponent({ props: ['open'] })\nexport default { mixins: [Base] }",
      "const options = { props: ['open'] }\nexport default defineComponent(options)",
      "export default withInstall(defineComponent({ props: ['open'] }))",
      "export default { data: () => ({ 'open': 1 }) }",
      "export default { 'methods': { 'open'() {} } }",
      "export default { setup() { const vm = { 'open': () => {} }; return vm } }",
      // Two hops of alias, and a name the function does not declare at all,
      // which is then the module's.
      "export default { setup() { const a = { 'open': () => {} }; const b = a; return b } }",
      "const vm = { 'open': () => {} }\nexport default { setup() { return vm } }",
      // Scope, not the first declaration of the name in the file: a
      // block-scoped `const` has ended by the time the return reads the name,
      // and a `var` declared twice takes the initializer from either.
      "const vm = { 'open': () => {} }\nexport default { setup() { if (x) { const vm = other() } return vm } }",
      "const vm = { 'open': () => {} }\nexport default { setup() { for (const vm of list) { void vm } return vm } }",
      "var options\nvar options = { props: ['open'] }\nexport default options",
      // The same options object reaching the default export by name, and by
      // an assignment after the declaration.
      "const options = { props: ['open'] }\nexport { options as default }",
      "let options\noptions = { props: ['open'] }\nexport default options",
    ]) {
      assert.deepEqual(found(component(script)), [], `reported options that reach the template:\n${script}`)
    }
    const inSetup = [
      '<script setup lang="ts">',
      "defineOptions({ methods: { 'open'() {} } })",
      '</script>',
      '<template>',
      '  <button @click="open()">x</button>',
      '</template>',
      '',
    ].join('\n')
    assert.deepEqual(found(inSetup), [], 'reported a name defineOptions declares')
  })

  // Resolving those variables must not reach into a scope the default export
  // cannot name: a `const options` inside some unrelated function is not the
  // module-level binding the export refers to, and counting it would hide a
  // call in exactly the case this rule exists for.
  it('does not take a local of the same name for the exported one', () => {
    const code = [
      '<script lang="ts">',
      "import { options } from './elsewhere'",
      'export default options',
      'function unrelated() {',
      "  const options = { methods: { 'open'() {} } }",
      '  return options',
      '}',
      'void unrelated',
      '</script>',
      '<template>',
      '  <button @click="open()">x</button>',
      '</template>',
      '',
    ].join('\n')
    assert.deepEqual(
      found(code).map((hit) => [hit.name, hit.where]),
      [['open', TEMPLATE]]
    )
  })

  // A local shadows the module's name from the moment it is declared, even
  // before it has a value. A `setup` that returns such a local hands the
  // template whatever that local ends up holding, not what a module-level
  // variable of the same name happens to hold.
  it('does not read a module variable a setup local shadows', () => {
    const component = (method) =>
      [
        '<script lang="ts">',
        "const vm = { 'open': () => {} }",
        `export default { ${method} }`,
        'void useSomething',
        '</script>',
        '<template>',
        '  <button @click="open()">x</button>',
        '</template>',
        '',
      ].join('\n')
    for (const method of [
      // A declared local, before it has a value.
      'setup() { let vm; vm = useSomething(); return vm }',
      // A parameter, a destructured name, a function and a class all bind the
      // name in this scope just as a `const` does.
      'setup(vm) { return vm }',
      'setup() { const { vm } = useSomething(); return vm }',
      'setup() { function vm() {} return vm }',
      'setup() { class vm {} return vm }',
    ]) {
      assert.deepEqual(
        found(component(method)).map((hit) => [hit.name, hit.where]),
        [['open', TEMPLATE]],
        `read a module variable this setup shadows:\n${method}`
      )
    }
  })

  // A namespace hands its members out as `NS.member`; the name is not a
  // module-level binding, and `export default options` cannot name it.
  it('does not read a namespace member as a module binding', () => {
    const code = [
      '<script lang="ts">',
      "namespace N { export const options = { props: ['open'] } }",
      'export default options',
      'void N',
      '</script>',
      '<template>',
      '  <button @click="open()">x</button>',
      '</template>',
      '',
    ].join('\n')
    assert.deepEqual(
      found(code).map((hit) => [hit.name, hit.where]),
      [['open', TEMPLATE]]
    )
  })

  // Following a name into what it is initialised with, and a mixin into the
  // object it names, both walk a graph that a file can close on itself. The
  // walk stops at the point it has already visited: it cannot say what an
  // unresolvable cycle binds, so it says nothing binds — and it returns.
  it('stops at a cycle rather than following it forever', () => {
    const component = (script) =>
      [
        '<script lang="ts">',
        script,
        'void defineComponent',
        '</script>',
        '<template>',
        '  <button @click="open()">x</button>',
        '</template>',
        '',
      ].join('\n')
    for (const script of [
      'const a = b\nconst b = a\nexport default { props: a }',
      'const A = { mixins: [B] }\nconst B = { mixins: [A] }\nexport default { mixins: [A] }',
      'const A = defineComponent({ mixins: [A] })\nexport default A',
    ]) {
      assert.deepEqual(
        found(component(script)).map((hit) => [hit.name, hit.where]),
        [['open', TEMPLATE]],
        `did not settle on the cycle:\n${script}`
      )
    }
  })

  // A component registered under `components:` declares its own props, which
  // are that component's render context — not this component's. Reading them as
  // bindings would answer "clean" to a call this template cannot resolve.
  it("does not read another component's props as this one's", () => {
    const code = [
      '<script lang="ts">',
      "const Local = defineComponent({ props: ['open'] })",
      'export default { components: { Local } }',
      'void defineComponent',
      '</script>',
      '<template>',
      '  <button @click="open()">x</button>',
      '</template>',
      '',
    ].join('\n')
    assert.deepEqual(
      found(code).map((hit) => [hit.name, hit.where]),
      [['open', TEMPLATE]]
    )
  })

  // An emit's name is not a binding: the template emits through `emit(...)`,
  // never by calling the name. Neither is a string that merely sits somewhere
  // in the script — a quoted key in a lookup table names the table's entry, a
  // quoted key in a destructuring pattern names the property being read, and a
  // `props` key outside a component's options names an ordinary object's
  // field. Counting any of them answers "clean" to exactly the call this rule
  // was written to find.
  it('does not let an emit name or any other string bind the call', () => {
    const code = [
      '<script setup lang="ts">',
      "const emit = defineEmits(['open'])",
      "const emitObject = defineEmits({ 'open': null })",
      "const label = 'open'",
      "const route = { name: 'open' }",
      "const list = ['open']",
      "const labels = { 'open': '打开' }",
      "const cfg = { props: ['open'] }",
      "const { 'open': renamed } = route",
      'void emit',
      'void emitObject',
      'void label',
      'void route',
      'void list',
      'void labels',
      'void cfg',
      'void renamed',
      '</script>',
      '<template>',
      '  <button @click="open()">x</button>',
      '</template>',
      '',
    ].join('\n')
    const hits = found(code)
    assert.deepEqual(
      hits.map((hit) => [hit.name, hit.where]),
      [['open', TEMPLATE]]
    )
  })

  // A model, unlike an emit, *is* a prop: `defineModel('open')` compiles to a
  // prop named `open`, so the template's `open()` reaches it and is bound.
  it('counts the name a model is declared under', () => {
    for (const declaration of [
      "const modelValue = defineModel('open')",
      "const modelValue = defineModel('open' as const)",
    ]) {
      const code = [
        '<script setup lang="ts">',
        declaration,
        'void modelValue',
        '</script>',
        '<template>',
        '  <button @click="open()">x</button>',
        '</template>',
        '',
      ].join('\n')
      assert.deepEqual(found(code), [], `reported a prop this declaration binds:\n${declaration}`)
    }
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
    assert.deepEqual(
      hits.map((hit) => hit.line),
      [5, 5]
    )
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
    assert.deepEqual(
      calls.map((call) => [call.name, call.line, call.where]),
      [
        ['open', 2, SCRIPT],
        ['close', 5, TEMPLATE],
      ]
    )
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

  const component = (call, taking = 'const n = 1\nvoid n') =>
    [
      '<script setup lang="ts">',
      taking,
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

  // Both shapes, each in the tree, each required to go red. The second is the
  // one #1618 took and the first version of this rule could not see; it is
  // planted here so that reading only the module's top-level statements can
  // never come back unnoticed.
  for (const [shape, source] of Object.entries(composable)) {
    it(`refuses a component calling a composable function that was never exported (${shape})`, () => {
      try {
        write(source.kept, component('open()'))

        const blocked = run()
        assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
        // The call site, the name, where in the component it is, and the file
        // that keeps the name to itself — the four things a reader needs to fix it.
        assert.match(blocked.stdout, /__globalFallbackProbe\.vue:6:19 {2}open\(\) {2}\[template\]/)
        assert.match(blocked.stdout, /__globalFallbackProbe\.ts declares it and never hands it out/)
        assert.match(blocked.stdout, /total: 1 call\(s\)/)
      } finally {
        rmSync(MODULE_FILE, { force: true })
        rmSync(COMPONENT_FILE, { force: true })
      }
    })

    it(`accepts the repaired pair (${shape})`, () => {
      try {
        // What the module half of the repair is, stated where it can be read:
        // at module level the export takes the name off the internal list,
        // while a `return` inside the composable does not — the module still
        // keeps it, and the component's own `taken` line is the half this rule
        // judges. `vue-tsc` is what refuses a return that omits the name.
        assert.deepEqual(internalDeclarations(source.fixed), shape === 'module level' ? [] : ['scrollToBottom', 'open'])
        write(source.fixed, component('open()', source.taken))
        const clean = run()
        assert.equal(clean.status, 0, `${clean.stdout}${clean.stderr}`)
        assert.match(clean.stdout, /No call falls back/)
      } finally {
        rmSync(MODULE_FILE, { force: true })
        rmSync(COMPONENT_FILE, { force: true })
      }
    })
  }

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
      write(
        composable['inside the composable'].kept,
        component('open()').replace('const n = 1\nvoid n', 'scrollToBottom()')
      )

      const blocked = run()
      assert.equal(blocked.status, 1, `${blocked.stdout}${blocked.stderr}`)
      assert.match(blocked.stdout, /__globalFallbackProbe\.vue:2:1 {2}scrollToBottom\(\) {2}\[script setup\]/)
      assert.match(blocked.stdout, /total: 2 call\(s\)/)
    } finally {
      rmSync(MODULE_FILE, { force: true })
      rmSync(COMPONENT_FILE, { force: true })
    }
  })

  // The other correct spelling: the call was always meant to be the global, so
  // it says so. A rule that refused this would be `no-undef` under another name.
  it('accepts the call once the component says `window.`', () => {
    try {
      write(composable['module level'].kept, component('window.open()'))
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
      write(composable['module level'].kept, component('open()'))
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
      assert.match(failed.stderr, /__globalFallbackProbe\.ts declares it and never hands it out/)
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
        write(composable['module level'].kept, broken)
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
