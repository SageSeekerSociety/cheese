// Unit tests for the hardcoded-Chinese ratchet's pure logic, run by
// `node --test` from `pnpm run test:ratchet` in CI.
//
// These are written against the failure modes that would make the gate either
// useless or hated: it must not count Chinese in comments (the tree is full of
// Chinese prose, and freezing it would drown the signal), it must not miss a
// string because of an apostrophe inside a regex, and it must never report a
// clean tree when it simply did not look.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  CJK,
  compare,
  formatReport,
  scanSource,
  scanTree,
  shouldScan,
  tightenedBaseline,
} from './i18n-cjk-ratchet-core.mjs'

test("the character class matches the catalog gate's", () => {
  assert.ok(CJK.test('中文'))
  assert.ok(CJK.test('㐀'))
  assert.ok(CJK.test('豈'))
  assert.ok(!CJK.test('Abc 123'), 'latin and digits are not Chinese')
  assert.ok(!CJK.test('：。、「」（）'), 'punctuation alone is not text')
  // Full-width punctuation is deliberately outside the class: it always rides
  // along with a Han character, and widening the class would make '：' on its
  // own a violation.
  assert.ok(CJK.test('：确定。'))
})

test('shouldScan: the catalog and the tests are out of scope', () => {
  assert.ok(shouldScan('src/components/Foo.vue'))
  assert.ok(shouldScan('src/lib/util.ts'))
  assert.ok(!shouldScan('src/i18n/messages/zh-CN/global.json'), 'the Chinese catalog is Chinese')
  assert.ok(!shouldScan('src/i18n/languages.ts'), 'language names must not follow the locale')
  assert.ok(!shouldScan('src/components/Foo.spec.ts'))
  assert.ok(!shouldScan('src/components/__tests__/Foo.ts'))
  assert.ok(!shouldScan('src/assets/logo.svg'), 'not source text')
})

test('scanSource: template text and attribute values are counts', () => {
  const vue = `<template>
  <div>
    <span>实名信息</span>
    <v-text-field label="真实姓名" placeholder="请输入您的真实姓名" />
    <v-btn>{{ loading ? '加载更早的消息…' : '更早的消息' }}</v-btn>
  </div>
</template>
`
  assert.deepEqual(scanSource('src/a.vue', vue), [3, 4, 5])
})

test('scanSource: HTML comments and JS comments are not counts', () => {
  const vue = `<template>
  <!-- 这是注释，不是文案 -->
  <div>确定</div>
</template>
<script setup lang="ts">
// 这里写中文注释很常见
/* 块注释也一样 */
const a = '确定'
</script>
`
  assert.deepEqual(scanSource('src/a.vue', vue), [3, 8])
})

test('scanSource: template line numbers survive a script block above it', () => {
  const vue = `<script setup lang="ts">
const label = '姓名'
</script>

<template>
  <div>确定</div>
</template>
`
  assert.deepEqual(scanSource('src/a.vue', vue), [2, 6])
})

test('scanSource: an inner <template #slot> still belongs to the outer block', () => {
  const vue = `<template>
  <v-data-table>
    <template #item="{ item }">
      <span>暂无数据</span>
    </template>
  </v-data-table>
</template>
`
  assert.deepEqual(scanSource('src/a.vue', vue), [4])
})

test('scanSource: a bare .ts file counts strings, not comments or identifiers', () => {
  const ts = `// 中文注释
const a = '思考强度'
const b = "执行"
const c = \`剩余 \${n} 天\`
const d = t('global.ok')
const e = /^[\\u4e00-\\u9fff]+$/ // a regex is not copy
`
  assert.deepEqual(scanSource('src/a.ts', ts), [2, 3, 4])
})

test('scanSource: a quote inside a regex does not desync the scanner', () => {
  const ts = `const re = /["']/g
const label = '确定'
`
  assert.deepEqual(scanSource('src/a.ts', ts), [2])
})

test('scanSource: an escaped quote and a multi-line template literal', () => {
  const ts = `const a = 'It\\'s 中文'
const b = \`第一行
第二行\`
`
  assert.deepEqual(scanSource('src/a.ts', ts), [1, 2, 3])
})

test('scanSource: one line counts once however many strings it holds', () => {
  const ts = `const a = '甲' + '乙' + '丙'
`
  assert.deepEqual(scanSource('src/a.ts', ts), [1])
})

test('scanSource: the allow directive exempts its own line, or the next one', () => {
  const ts = `const a = '甲'
const b = '乙' // i18n-cjk-allow
// i18n-cjk-allow-next-line
const c = '丙'
const d = '丁'
`
  assert.deepEqual(scanSource('src/a.ts', ts), [1, 5])
})

test('scanSource: console and logger arguments are developer logs, not copy', () => {
  const ts = `console.error('加载讨论数据失败', e)
logger.debug('选择算力失败')
toast.error('选择算力失败')
const label = '姓名'
console.log({ msg: '完成' })
`
  assert.deepEqual(scanSource('src/a.ts', ts), [3, 4, 5])
})

test('stringSpans: a URL in a string does not start a comment', () => {
  const ts = `const url = 'https://example.com/a'
const label = '确定'
`
  assert.deepEqual(scanSource('src/a.ts', ts), [2])
})

test('scanTree: empty files are not carried in the baseline', () => {
  const tree = scanTree([
    { path: 'src/a.vue', text: '<template><div>确定</div></template>' },
    { path: 'src/b.vue', text: '<template><div>OK</div></template>' },
    { path: 'src/i18n/messages/zh-CN/global.json', text: '{ "a": "中文" }' },
  ])
  assert.deepEqual(tree, { 'src/a.vue': 1 })
})

test('compare: a new file with Chinese is a regression against base 0', () => {
  const result = compare({ 'src/a.vue': 3 }, { 'src/a.vue': 3, 'src/b.vue': 1 })
  assert.equal(result.ok, false)
  assert.deepEqual(result.regressions, [{ file: 'src/b.vue', base: 0, now: 1 }])
})

test('compare: paying a file down is an improvement, not a failure', () => {
  const result = compare({ 'src/a.vue': 3, 'src/b.vue': 5 }, { 'src/a.vue': 1, 'src/b.vue': 5 })
  assert.equal(result.ok, true)
  assert.deepEqual(result.improvements, [{ file: 'src/a.vue', base: 3, now: 1 }])
  assert.equal(result.currentTotal, 6)
  assert.equal(result.baselineTotal, 8)
})

test('tightenedBaseline: it can only ever go down', () => {
  const next = tightenedBaseline({ 'src/a.vue': 2, 'src/gone.vue': 4 }, { 'src/a.vue': 5, 'src/new.vue': 1 })
  assert.deepEqual(next, { 'src/a.vue': 2, 'src/new.vue': 1 })
})

test('formatReport: a clean run states the total, a regression shows the lines', () => {
  const clean = formatReport(compare({ 'src/a.vue': 2 }, { 'src/a.vue': 2 }))
  assert.match(clean, /total: 2 line\(s\) of hardcoded Chinese, baseline allows 2/)
  assert.doesNotMatch(clean, /ratchet blocks/)

  const samples = new Map([['src/b.vue', [{ line: 3, text: '<span>实名信息</span>' }]]])
  const bad = formatReport(compare({ 'src/a.vue': 2 }, { 'src/a.vue': 2, 'src/b.vue': 1 }), samples)
  assert.match(bad, /New hardcoded Chinese/)
  assert.match(bad, /src\/b\.vue: 0 -> 1/)
  assert.match(bad, /3: <span>实名信息<\/span>/)
  assert.match(bad, /i18n-cjk-allow-next-line/)
})
