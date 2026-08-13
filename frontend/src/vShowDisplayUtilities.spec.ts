// `v-show` 和 Vuetify 的 display 工具类不能同时用在一个元素上。
//
// v-show 的做法是往元素的 inline style 里写 `display: none`。而 Vuetify 的
// `.d-flex` 是 `display: flex !important` —— **带 !important 的类胜过 inline
// style**，这是 CSS 唯一一处「类比行内样式优先级高」的地方。于是 v-show 写进去
// 了、也确实在 DOM 里，元素却纹丝不动。
//
// 实测（2026-08-12，专注模式）：`.col-chat` 的 inline style 是
// `flex: 0 0 50%; display: none;`，computed display 却是 `flex`、宽 468px；
// 在页面上把 `d-flex` 摘掉，立刻变成 `display: none`、宽 0。按钮会切换、图标会
// 翻转、`.pane-resizer` 和成员栏都正确消失了 —— 只有真正要隐藏的那一栏没动。
// 整个功能从上线起就没生效过，而且不报任何错。
//
// 这条规律没法靠 review 记住（两个类离得远：v-show 在父组件，d-flex 在子组件的
// 根元素上），所以写成测试。
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))

// Vuetify 的 display 工具类，全部是 !important
const DISPLAY_UTILITIES =
  /\b(d-flex|d-inline-flex|d-block|d-inline-block|d-inline|d-grid|d-inline-grid|d-none|d-table|d-table-row|d-table-cell)\b/

function vueFiles(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist') continue
    const path = join(dir, name)
    if (statSync(path).isDirectory()) out.push(...vueFiles(path))
    else if (name.endsWith('.vue')) out.push(path)
  }
  return out
}

/** The opening tag that carries `v-show`, as written in the source. */
function tagsWithVShow(source: string): string[] {
  return source.match(/<[A-Za-z][^>]*\sv-show[^>]*>/g) ?? []
}

/** The component names a `v-show` is applied to (PascalCase tags). */
function componentsUnderVShow(source: string): string[] {
  return tagsWithVShow(source)
    .map((tag) => /^<([A-Z][A-Za-z0-9]*)/.exec(tag)?.[1])
    .filter((name): name is string => !!name)
}

/** The first element in an SFC's `<template>` — the root that receives v-show. */
function rootTag(source: string): string | null {
  const template = /<template>([\s\S]*?)\n<\/template>/.exec(source)?.[1]
  if (!template) return null
  return /<[a-zA-Z][^>]*>/.exec(template.replace(/<!--[\s\S]*?-->/g, ''))?.[0] ?? null
}

describe('v-show never lands on a Vuetify display utility', () => {
  const files = vueFiles(SRC)

  it('scans a real component tree (guards against a broken glob)', () => {
    // Without this, a path change turns every assertion below into a silent
    // pass over an empty list — the failure mode the check exists to prevent.
    expect(files.length).toBeGreaterThan(20)
  })

  it('not on the element itself', () => {
    const offenders: string[] = []
    for (const file of files) {
      for (const tag of tagsWithVShow(readFileSync(file, 'utf8'))) {
        if (DISPLAY_UTILITIES.test(tag)) offenders.push(`${file}: ${tag.slice(0, 90)}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('not on the root of a component something v-shows', () => {
    // The hard case, and the one that actually shipped: the two halves live in
    // different files, so neither reads as wrong on its own.
    const byName = new Map(files.map((f) => [f.split('/').pop()!.replace('.vue', ''), f]))
    const offenders: string[] = []
    for (const file of files) {
      for (const name of componentsUnderVShow(readFileSync(file, 'utf8'))) {
        const target = byName.get(name)
        if (!target) continue
        const root = rootTag(readFileSync(target, 'utf8'))
        if (root && DISPLAY_UTILITIES.test(root)) {
          offenders.push(`${file} v-shows <${name}>, whose root is ${root.slice(0, 70)}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })
})
