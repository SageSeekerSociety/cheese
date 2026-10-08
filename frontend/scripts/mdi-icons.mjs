// MDI 图标子集要用的那几件事：从源码里问出「都用到了哪些图标」，从 @mdi/font 的
// CSS 里问出「每个名字是哪个字形」，然后只留用得上的规则。
//
// 为什么要有子集：@mdi/font 的 woff2 是 403 KB —— 已经压过，gzip 也压不动（403 →
// 395 KB），而首屏靠 vite.config.ts 那个 preload 主动去下它。整个应用真正用得到的
// 名字只有 390 多个，裁完是 21 KB。同一份 CSS 里 7448 条图标规则只用得上 390 条，
// 那份也一起裁（gzip 54 KB → 2 KB）。
//
// 名字从两条路来，缺一不可：
//
//   1. 扫源码里的 `mdi-xxx` 字面量。本仓库的图标名全是字符串常量（模板里的
//      `:icon="'mdi-chevron-down'"`、命令表里的 `icon: 'mdi-plus'`、组件里的映射
//      表），没有一处是运行时拼出来的 —— mdi-icons.test.mjs 盯着这一条。
//   2. Vuetify 自己的别名表（`vuetify/iconsets/mdi` 的 aliases）。勾、下拉箭头这些
//      画在 Vuetify 组件内部，不在我们源码里；漏一个就是一片方块。它是 import 进来
//      的，所以 Vuetify 升版带进来的新别名自动跟上，不用手工同步一份清单。
//
// 测试文件不扫：它们不进产物，作者在那儿写 `mdi-open` 这种假名字不该让构建红。
//
// 反过来，源码里写了字体里根本没有的名字要当回事 —— 那种图标今天就是空白的，
// 裁不裁都一样空。findUnknownIcons 把人揪出来，构建期直接报错。

import { readdirSync, readFileSync } from 'node:fs'
import { extname, join } from 'node:path'

/** 可能写着图标名的文件后缀。 */
const SOURCE_EXTENSIONS = new Set(['.vue', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.css', '.scss', '.html'])

/** 不进产物的文件：测试、测试夹具、测试用的 setup。 */
const TEST_FILE = /(?:^|[/\\])(?:__tests__|__mocks__|test)[/\\]|\.(?:spec|test)\.[cm]?[jt]sx?$/

/** 一段文本里出现的图标名，并进 `into`。 */
export function collectNamesFromText(text, into = new Set()) {
  for (const match of text.matchAll(/mdi-[a-z0-9-]+/g)) into.add(match[0])
  return into
}

/** 一棵目录树里所有源码文件出现的图标名，并进 `into`。 */
export function collectNamesFromTree(dir, into = new Set()) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules') collectNamesFromTree(full, into)
    } else if (SOURCE_EXTENSIONS.has(extname(entry.name)) && !TEST_FILE.test(full)) {
      collectNamesFromText(readFileSync(full, 'utf8'), into)
    }
  }
  return into
}

/**
 * 每个名字在字体里的码位表：`.mdi-home::before { content: "\F02DC"; }`。
 * 子集按码位裁，所以这张表是「类名 → 字形」唯一的桥。
 */
export function parseCodepoints(css) {
  const codepoints = new Map()
  const rule = /\.mdi-([a-z0-9-]+)::before\s*\{\s*content:\s*"\\([0-9A-Fa-f]{4,6})"/g
  for (const match of css.matchAll(rule)) codepoints.set(`mdi-${match[1]}`, Number.parseInt(match[2], 16))
  return codepoints
}

/** 字体里没有的名字。有它就说明那个图标画不出来，不管裁不裁都是空白。 */
export function findUnknownIcons(names, codepoints) {
  return [...names].filter((name) => !codepoints.has(name)).sort()
}

/**
 * 只留用得上的那几条 `.mdi-xxx::before` 规则。
 *
 * 基类（`.mdi:before, .mdi-set`）和 `.mdi-18px` / `.mdi-rotate-45` 那几条工具类不是
 * 这个形状（`::before` 前面还跟着别的选择器），碰不到，原样留下。
 */
export function keepUsedRules(css, names) {
  return css.replace(/\.mdi-([a-z0-9-]+)::before\s*\{[^}]*\}/g, (rule, name) => (names.has(`mdi-${name}`) ? rule : ''))
}
