// Unit tests for the hardcoded-Chinese gate's pure logic, run by
// `node --test` from `pnpm run test:ratchet` in CI.
//
// These are written against the failure modes that would make the gate either
// useless or hated: it must not count Chinese prose in `//` and `/* */`
// comments (the tree is full of it), it must not miss a string because of an
// apostrophe inside a regex, and its one exemption — an `i18n-data:` annotation
// on the line — must not be usable without saying why. Chinese in a template's
// HTML comments is a finding of its own: counted (see the ratchet half below),
// because vitest and `vite dev` keep the comment in the DOM the no-Chinese
// specs read.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  annotationOn,
  checkSource,
  CJK,
  formatReport,
  scanSource,
  scanTree,
  shouldScan,
} from './i18n-cjk-gate-core.mjs'

test("the character class matches the catalog gate's", () => {
  assert.ok(CJK.test('中文'))
  assert.ok(CJK.test('㐀'))
  assert.ok(CJK.test('豈'))
  assert.ok(!CJK.test('Abc 123'), 'latin and digits are not Chinese')
  assert.ok(!CJK.test('👍'), 'an emoji is not Chinese')
  assert.ok(!CJK.test('한국어'), 'Hangul is not Chinese')
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
  assert.ok(!shouldScan('src/proto-copy-preview.vue'), 'preview-only entry, not in the build')
  assert.ok(!shouldScan('src/views/demo/catalogFixtures.ts'), 'component catalog and manual demos')
  assert.ok(!shouldScan('src/demo-main.ts'))
  assert.ok(shouldScan('src/views/demoted/Foo.vue'), 'only the demo directory itself')
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

test('scanSource: every <script> block is read, not just the first', () => {
  // 一个 `.vue` 可以带两个脚本块（`<script>` + `<script setup>`）。只读第一个
  // 的话，ChatPanel.vue 整个 `<script setup>` 里的中文对这道闸门就是隐形的——
  // 而那正是它曾经的样子。
  const vue = `<script lang="ts">
const a = '外层'
</script>

<script setup lang="ts">
const b = '里层'
</script>

<template>
  <div>标题</div>
</template>
`
  assert.deepEqual(scanSource('src/a.vue', vue), [2, 6, 10])
})

test('scanSource: a <template> written in a script comment is not a block', () => {
  const vue = `<script setup lang="ts">
// 用法：<template #item> 里放
//   一行中文说明
const a = 1
</script>

<template>
  <div>确定</div>
</template>
`
  assert.deepEqual(scanSource('src/a.vue', vue), [8])
})

test('scanSource: a MIME wildcard in a template does not open a comment', () => {
  const vue = `<template>
  <input accept="image/*" />
  <span>上传头像</span>
</template>

<style>
/* 样式 */
</style>
`
  assert.deepEqual(scanSource('src/a.vue', vue), [3])
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

test('scanSource: code inside ${…} is code — its comments are not counted, its strings are', () => {
  const ts = `const q = \`/feedback\${build({
  // 四个筛选，空值不传
  a: 1,
})}\`
const b = \`\${ok ? '成功' : 'x'}\`
const c = '确定'
`
  assert.deepEqual(scanSource('src/a.ts', ts), [5, 6])
})

test('scanSource: a template logged to the console stays a developer log after its ${…}', () => {
  const ts = `console.error(\`加载 \${id} 失败\`)
console.warn(\`\${a ? '甲' : ''} 乙\`)
`
  assert.deepEqual(scanSource('src/a.ts', ts), [])
})

test('scanSource: one line counts once however many strings it holds', () => {
  const ts = `const a = '甲' + '乙' + '丙'
`
  assert.deepEqual(scanSource('src/a.ts', ts), [1])
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

test('checkSource: unannotated Chinese in a script or a template fails as copy', () => {
  const vue = `<template>
  <div>确定</div>
</template>
<script setup lang="ts">
const a = '取消'
</script>
`
  assert.deepEqual(
    checkSource('src/a.vue', vue).problems.map(({ line, problem }) => ({ line, problem })),
    [
      { line: 2, problem: 'copy' },
      { line: 5, problem: 'copy' },
    ]
  )
})

test('checkSource: an annotated line with a reason passes, in .ts and in both .vue blocks', () => {
  const ts = `const BOUNDS = [
  ['a', '阿'], // i18n-data: collation anchor, compared and never shown
  ['b', '八'], /* i18n-data: collation anchor */
]
`
  assert.deepEqual(checkSource('src/a.ts', ts), { problems: [], annotated: [2, 3] })

  const vue = `<template>
  <span>【PDF】</span><code>第 N 页</code> <!-- i18n-data: the stored marker format, shown as an example -->
</template>
<script setup lang="ts">
const ORIGIN = '【第 N 页】' // i18n-data: written into task text and parsed back by model.ts
</script>
`
  assert.deepEqual(checkSource('src/a.vue', vue), { problems: [], annotated: [2, 5] })
})

test('checkSource: an annotation without a reason fails', () => {
  const ts = `const a = '甲' // i18n-data:
const b = '乙' // i18n-data:   —
const c = '丙' /* i18n-data: */
`
  assert.deepEqual(
    checkSource('src/a.ts', ts).problems.map(({ line, problem }) => ({ line, problem })),
    [
      { line: 1, problem: 'no-reason' },
      { line: 2, problem: 'no-reason' },
      { line: 3, problem: 'no-reason' },
    ]
  )
})

test('checkSource: a template line takes only an HTML comment — `//` there is text the user sees', () => {
  const vue = `<template>
  <div>确定 // i18n-data: not a comment here</div>
</template>
`
  assert.deepEqual(
    checkSource('src/a.vue', vue).problems.map(({ problem }) => problem),
    ['copy']
  )
})

test('checkSource: an annotation on a line with no counted Chinese is stale', () => {
  const ts = `const a = t('global.ok') // i18n-data: the string was translated, the note stayed
// 注释里的中文 // i18n-data: a comment is not counted, so nothing is excused
const b = '确定' // i18n-data: still data
`
  assert.deepEqual(
    checkSource('src/a.ts', ts).problems.map(({ line, problem }) => ({ line, problem })),
    [
      { line: 1, problem: 'stale' },
      { line: 2, problem: 'stale' },
    ]
  )
})

test('checkSource: a Chinese comment that never reaches the DOM is not one', () => {
  // `<script>`, `<style>`, and the file around the blocks are not the rendered
  // markup, so a Chinese comment there is prose nobody sees — and the tree is
  // full of it.
  const vue = `<script setup lang="ts">
// 中文注释
const a = 1
</script>

<!-- 块外的中文注释 -->
<template>
  <div>OK</div>
</template>

<style>
/* 样式里的中文 */
</style>
`
  assert.deepEqual(checkSource('src/a.vue', vue), { problems: [], annotated: [] })
  assert.deepEqual(checkSource('src/a.ts', '// 中文注释\nconst a = 1\n'), { problems: [], annotated: [] })
})

test('checkSource: an i18n-data comment is exempt, Chinese reason and all', () => {
  const vue = `<template>
  <code>【PDF · 第 N 页】</code> <!-- i18n-data: 写进题目简介的固定标记，原样展示 -->
</template>
`
  assert.deepEqual(checkSource('src/a.vue', vue), { problems: [], annotated: [2] })
})

test('checkSource: an annotation beside a Chinese comment has nothing to excuse', () => {
  // The comment is prose, never the Chinese an annotation covers, so the
  // annotation is stale. Reporting the comment instead would count the line and
  // let the annotation stand, ready to exempt whatever copy lands there next.
  const vue = `<template>
  <div>{{ t('global.ok') }}</div> <!-- i18n-data: collation anchor --> <!-- 顺带一句说明 -->
</template>
`
  assert.deepEqual(
    checkSource('src/a.vue', vue).problems.map(({ line, problem }) => ({ line, problem })),
    [{ line: 2, problem: 'stale' }]
  )
})

test('checkSource: an i18n-data comment whose reason wraps is still exempt', () => {
  // `annotationOn` reads one line, where the marker is recognizable before the
  // `-->` arrives; the whole comment has to be read the same way, or a reason
  // written over two lines turns its own comment into a finding.
  const vue = `<template>
  <code>【PDF · 第 N 页】</code> <!-- i18n-data: 写进题目简介的固定标记，
  原样展示 -->
</template>
`
  assert.deepEqual(checkSource('src/a.vue', vue), { problems: [], annotated: [2] })
})

test('checkSource: a template line reads markup annotations, never code ones', () => {
  // `//` is text in a template, so a comment whose prose mentions the code
  // marker is prose — not an annotation, and so not a stale one either.
  const vue = `<template>
  <!-- 脚本里用 // i18n-data: 理由 标注 -->
</template>
`
  assert.deepEqual(checkSource('src/a.vue', vue), { problems: [], annotated: [] })
})

test('checkSource: copy beside a comment that quotes the marker is still copy', () => {
  // Reading the quoted marker as an annotation would exempt the copy on this
  // line and report nothing at all.
  const vue = `<template>
  <div>确定</div> <!-- see <!-- i18n-data: reason -->
</template>
`
  assert.deepEqual(
    checkSource('src/a.vue', vue).problems.map(({ line, problem }) => ({ line, problem })),
    [{ line: 2, problem: 'copy' }]
  )
})

test('checkSource: a reason that only begins on the next line is no reason', () => {
  // The marker and the reason have to share the opener's line: a reason that
  // starts after it leaves the annotation saying nothing, which is what
  // `no-reason` is for. Unlike a reason that starts on the marker's line and
  // continues below it, which the whole comment is read to cover.
  const vue = `<template>
  <code>【PDF · 第 N 页】</code> <!-- i18n-data:
  写进题目简介的固定标记 -->
</template>
`
  assert.deepEqual(
    checkSource('src/a.vue', vue).problems.map(({ line, problem }) => ({ line, problem })),
    [{ line: 2, problem: 'no-reason' }]
  )
})

test('checkSource: unannotated copy beside a comment reports the copy', () => {
  const vue = `<template>
  <div>确定</div> <!-- 确认按钮 -->
</template>
`
  assert.deepEqual(
    checkSource('src/a.vue', vue).problems.map(({ line, problem }) => ({ line, problem })),
    [{ line: 2, problem: 'copy' }]
  )
  // Once the copy is translated the comment is left, and a comment is prose.
  const fixed = `<template>
  <div>{{ t('global.ok') }}</div> <!-- 确认按钮 -->
</template>
`
  assert.deepEqual(checkSource('src/a.vue', fixed), { problems: [], annotated: [] })
})

test('annotationOn: the marker must open the comment, so prose mentioning it is not one', () => {
  assert.equal(annotationOn("const a = '甲' // see the i18n-data: rule", ['code']), null)
  assert.deepEqual(annotationOn("const a = '甲' //i18n-data: why", ['code']), { reason: 'why' })
  assert.equal(annotationOn('<b>甲</b> <!-- i18n-data: why -->', ['code']), null)
  assert.deepEqual(annotationOn('<b>甲</b> <!-- i18n-data: why -->', ['markup']), { reason: 'why' })
})

test('scanTree: out-of-scope files are not read, clean files are not listed', () => {
  const result = scanTree([
    { path: 'src/a.vue', text: '<template><div>确定</div></template>' },
    { path: 'src/b.vue', text: '<template><div>OK</div></template>' },
    { path: 'src/c.ts', text: "const a = '阿' // i18n-data: anchor\n" },
    { path: 'src/i18n/messages/zh-CN/global.json', text: '{ "a": "中文" }' },
  ])
  assert.equal(result.ok, false)
  assert.deepEqual(Object.keys(result.problems), ['src/a.vue'])
  assert.equal(result.annotated, 1)
})

test('formatReport: a clean run says so, a failure shows each line and what to do', () => {
  const clean = formatReport(scanTree([{ path: 'src/c.ts', text: "const a = '阿' // i18n-data: anchor\n" }]))
  assert.match(clean, /No Chinese copy in src\/\. 1 line\(s\) carry an i18n-data annotation\./)

  const bad = formatReport(
    scanTree([
      { path: 'src/b.vue', text: '<template>\n  <span>实名信息</span>\n</template>\n' },
      { path: 'src/c.ts', text: "const a = '阿' // i18n-data:\n" },
    ])
  )
  assert.match(bad, /src\/b\.vue\n {2}2: \[Chinese outside the catalog\] <span>实名信息<\/span>/)
  assert.match(bad, /src\/c\.ts\n {2}1: \[i18n-data annotation without a reason\]/)
  assert.match(bad, /t\('namespace\.component\.role'\)/, 'says how to move copy into the catalog')
  assert.match(bad, /annotation on the SAME line that says why/, 'says how to mark data')
  assert.match(bad, /write the reason after the colon/)
  assert.match(bad, /2 problem\(s\)\./)
})
