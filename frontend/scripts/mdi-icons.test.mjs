// 图标子集的护栏。跑法：`pnpm run test:ratchet`（CI 的 check 里）。
//
// 三条里第一条是主闸门：源码里写了一个 @mdi/font 里不存在的图标名，界面上那个地方
// 就是一片空白 —— 今天有（有过）三个，谁都没发现。构建期也拦（vite.config.ts 的
// mdiFont），但构建在 CI 里跑得比这个晚，而且没人天天看构建日志，所以这里也拦一道。
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { aliases } from 'vuetify/iconsets/mdi'

import {
  collectNamesFromText,
  collectNamesFromTree,
  findUnknownIcons,
  keepUsedRules,
  parseCodepoints,
} from './mdi-icons.mjs'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(import.meta.url)
const PACKAGE_CSS = readFileSync(require.resolve('@mdi/font/css/materialdesignicons.css'), 'utf8')

/** 上游 CSS 里每个名字对应的码位。 */
const CODEPOINTS = parseCodepoints(PACKAGE_CSS)

/** 一起发送进字体的名字：源码里的 + Vuetify 别名表里的。 */
function shippedNames() {
  const names = collectNamesFromTree(join(ROOT, 'src'))
  for (const file of ['index.html', 'demo.html']) collectNamesFromText(readFileSync(join(ROOT, file), 'utf8'), names)
  for (const name of Object.values(aliases)) names.add(name)
  return names
}

test('每个用到的图标名都在 @mdi/font 里存在', () => {
  const unknown = findUnknownIcons(shippedNames(), CODEPOINTS)
  assert.deepEqual(
    unknown,
    [],
    `这些图标名在 @mdi/font 里不存在，界面上会是空白：${unknown.join(', ')}\n` +
      '换一个字体里真实存在的名字；确实是新图标就升 @mdi/font。'
  )
})

test('Vuetify 的别名表接得上字体的码位表', () => {
  // 别名表和码位表各自跟自己的上游走，中间断掉不会有人报错，只会在某个组件上出现
  // 一片空白。这条是那个接缝。
  assert.deepEqual(findUnknownIcons(new Set(Object.values(aliases)), CODEPOINTS), [])
  assert.ok(Object.keys(aliases).length > 40, '别名表读出来是空的？那这道闸门就没在拦东西')
})

test('裁掉的规则只剩用到的那些', () => {
  const names = shippedNames()
  const kept = keepUsedRules(PACKAGE_CSS, names)
  const before = PACKAGE_CSS.match(/\.mdi-[a-z0-9-]+::before/g) ?? []
  const after = kept.match(/\.mdi-[a-z0-9-]+::before/g) ?? []
  // 留下的条数就是名字数（每个名字一条规则）。用名字数对齐而不是写死一个数字：
  // 加一个图标就不该改这里。
  assert.equal(after.length, names.size)

  // 没裁的那几样必须活下来：基类、尺寸类、旋转类。它们不匹配上面那个形状，靠的就是
  // 这一点 —— 顺手确认一下，别哪天换了正则把它们一起吃掉。
  for (const rule of ['.mdi:before', '.mdi-set', '.mdi-18px.mdi:before', '.mdi-rotate-45']) {
    assert.ok(kept.includes(rule), `裁 CSS 时误删了 ${rule}`)
  }

  assert.ok(after.length < before.length / 10, `只裁掉了 ${before.length - after.length} 条，不对`)
})

test('图标名在源码里都是字面量，没有运行时拼出来的', () => {
  // 拼出来的名字扫不到，扫不到就不进子集 —— 界面上是一片空白，而且 CI 这里也查不到
  // 是哪一处。这条把「拼」这个写法本身禁掉，让上面那条闸门的覆盖面成立。
  const offenders = []
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name)
      if (entry.isDirectory()) {
        if (entry.name !== 'node_modules') walk(full)
      } else if (/\.(?:vue|[cm]?[jt]sx?)$/.test(entry.name) && !/\.(?:spec|test)\./.test(entry.name)) {
        const text = readFileSync(full, 'utf8')
        for (const [index, line] of text.split('\n').entries()) {
          if (/mdi-\$\{/.test(line) || /['"`]mdi-['"`]/.test(line)) offenders.push(`${full}:${index + 1}`)
        }
      }
    }
  }
  walk(join(ROOT, 'src'))
  assert.deepEqual(offenders, [], `图标名是拼出来的，子集扫不到：${offenders.join(', ')}`)
})

test('码位表读得出来，且是个像样的规模', () => {
  assert.ok(CODEPOINTS.size > 5000, `只从上游 CSS 里读出 ${CODEPOINTS.size} 个图标`)
  assert.equal(CODEPOINTS.get('mdi-home'), 0xf02dc)
})
