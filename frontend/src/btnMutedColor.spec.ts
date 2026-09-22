// `<v-btn class="c-muted">` 在这个应用里是琥珀色的。
//
// `MyApp.vue` 用 v-defaults-provider 给全站的 VBtn 默认了 `color: 'primary'`，
// Vuetify 于是给每颗没写 color 的按钮挂上 `text-primary`——那是
// `color: … !important`，比 `.c-muted` 那一行普通的 `color` 优先。写的人以为
// 是一颗安静的灰按钮，屏幕上是一颗主操作色的按钮，而琥珀色在设计系统里只留给
// 那一个主操作。
//
// 实测（2026-09-23，改动面板）：返回箭头和 ⋯ 计算出来是 rgb(245, 127, 23)。
// 这样写的按钮当时全站有 17 颗，没有一颗是有意的。
//
// 按钮要灰就写 `color="medium-emphasis"`。两个类离得远（默认值在 MyApp，类在
// 各处），review 记不住，所以写成测试。
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))

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

/** 带灰色文字类、却没写 color 的 `<v-btn>` 开标签。 */
function mutedButtonsWithoutColor(source: string): string[] {
  const out: string[] = []
  for (const m of source.matchAll(/<v-btn\b([^>]*?)>/gs)) {
    const attrs = m[1]
    if (/\bclass="[^"]*\bc-(muted|faint)\b/.test(attrs) && !/(^|\s):?color=/.test(attrs)) out.push(m[0])
  }
  return out
}

describe('灰色的按钮真的是灰的', () => {
  it('没有 <v-btn> 只靠 c-muted / c-faint 上色', () => {
    const offenders = vueFiles(SRC).flatMap((file) =>
      mutedButtonsWithoutColor(readFileSync(file, 'utf8')).map(
        (tag) => `${relative(SRC, file)}: ${tag.replace(/\s+/g, ' ')}`
      )
    )
    expect(offenders, '改成 color="medium-emphasis"').toEqual([])
  })

  it('这条检查认得出它要拦的写法', () => {
    expect(mutedButtonsWithoutColor('<v-btn icon="mdi-x" class="c-muted" />')).toHaveLength(1)
    expect(mutedButtonsWithoutColor('<v-btn icon="mdi-x" color="medium-emphasis" />')).toHaveLength(0)
    expect(mutedButtonsWithoutColor('<v-btn :color="c" class="c-muted" />')).toHaveLength(0)
  })
})
