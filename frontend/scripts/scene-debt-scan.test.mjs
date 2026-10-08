import assert from 'node:assert/strict'
import test from 'node:test'

import { scanFile, scanScript } from './scene-debt-scan.mjs'

test('route calls include renamed and namespace imports, not quoted examples', () => {
  assert.equal(
    scanScript('src/views/P.vue', "import {useRoute as address} from 'vue-router'; address()").useRoute,
    true
  )
  assert.equal(scanScript('src/views/P.vue', "import * as router from 'vue-router'; router.useRoute()").useRoute, true)
  assert.equal(scanScript('src/views/P.vue', "const hint = 'useRoute()'; /* useRoute() */").useRoute, false)
  assert.equal(scanScript('src/views/P.vue', 'const hint = `useRoute()`; const r = /useRoute\\(\\)/').useRoute, false)
  assert.equal(scanScript('src/views/P.vue', 'const hint = `${useRoute()}`').useRoute, true)
})

test('network imports cannot be hidden behind aliases, relative paths or different syntax', () => {
  const imports = [
    "import x from '@/network/api/tasks'",
    "import type {X} from '@/network/types'",
    "import {type X} from '@/network/types'",
    "import '../../network'",
    "export {x} from '../../network/api/tasks'",
    "export type {X} from '@/network/types'",
    "export * from '@/network/api/tasks'",
    "const x = import('../../network/api/tasks')",
    "const x = require('../../network/api/tasks')",
    "import x = require('@/network')",
    "type X = import('@/network/types').X",
    'const x = import(`/src/network/api/tasks`)',
    "import x from '@/api/../network/api/tasks'",
  ]
  for (const code of imports) assert.equal(scanScript('src/views/tasks/P.vue', code).network, true, code)
})

test('new API modules, similarly named folders and string/comment examples remain legal', () => {
  for (const code of [
    "import x from '@/api/tasks'",
    "import x from '@/networking'",
    "import x from './network'",
    "const example = `import x from '@/network'`",
    "// import x from '@/network'\nconst x = 1",
    "/* export * from '@/network' */",
  ])
    assert.equal(scanScript('src/views/P.vue', code).network, false, code)
})

test('value imports expose the local binding, and types are never rendered children', () => {
  assert.deepEqual(
    scanScript('src/views/P.vue', "import Renamed from './Child.vue'; import type T from './T.vue'").imports,
    { Renamed: './Child.vue' }
  )
})

test('parenthesized, indexed and template route calls count, but shadowing does not', () => {
  for (const code of [
    "import {useRoute} from 'vue-router'; (useRoute)()",
    "import * as router from 'vue-router'; router['useRoute']()",
  ])
    assert.equal(scanScript('P.ts', code).useRoute, true)
  assert.equal(
    scanScript(
      'P.ts',
      "import {useRoute as address} from 'vue-router'; const run = (address: () => number) => address()"
    ).useRoute,
    false
  )
  assert.equal(
    scanFile(
      'P.vue',
      "<script setup>import {useRoute} from 'vue-router'</script><template><div>{{ useRoute().params.id }}</div></template>"
    ).useRoute,
    true
  )
  assert.equal(
    scanFile('P.vue', '<template><div title="useRoute()"><!-- {{ useRoute() }} --></div></template>').useRoute,
    false
  )
})

test('Vue SFC boundaries exclude commented scripts and preserve TSX', () => {
  assert.equal(
    scanFile('src/views/P.vue', "<!-- <script>import '@/network'</script> --><template><div/></template>").network,
    false
  )
  assert.equal(scanFile('src/views/P.vue', '<script lang="tsx">export default () => <div/></script>').network, false)
})

test('async bindings and dynamic expressions retain their component targets', () => {
  const code =
    "import {defineAsyncComponent as async} from 'vue'; import Other from './Other.vue'; const Child = async(() => import('./Child.vue')); const selection = computed(() => ready ? Child : Other)"
  const result = scanScript('P.ts', code, ['<component :is="selection"/>'])
  assert.deepEqual(result.components.Child, ['./Child.vue'])
  assert.deepEqual(new Set(result.rendered), new Set(['Child', 'Other']))
  assert.deepEqual(
    scanScript('P.ts', code, [
      '<div title="<component :is=&quot;Child&quot;/>"><!-- <component :is="Child"/> --></div>',
    ]).rendered,
    []
  )
})

test('unparseable code cannot produce a zero-debt answer', () => {
  assert.throws(() => scanScript('src/views/P.vue', 'const = ;'), /src\/views\/P.vue/)
})
