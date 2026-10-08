import assert from 'node:assert/strict'
import test from 'node:test'

import { scanScript } from './scene-debt-scan.mjs'

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

test('unparseable code cannot produce a zero-debt answer', () => {
  assert.throws(() => scanScript('src/views/P.vue', 'const = ;'), /src\/views\/P.vue/)
})
