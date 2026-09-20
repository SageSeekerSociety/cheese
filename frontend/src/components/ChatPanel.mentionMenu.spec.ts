// @vitest-environment jsdom
/** `@` 候选菜单没有高度上限那条。
 *
 * 它最多 7 项（`mentionMatches` 里 `slice(0, 7)`），每项 `min-height: 36px`，展开
 * 就是 254px；而 `.composer` 是 `.chat`（flex column、`height: 100%`）里不肯收缩的
 * 那一项。面板一矮（尤其手机上），多出来的部分连同输入框一起从 `.chat` 底部溢出、
 * 被外壳裁掉，且没有任何滚动条——输入框也跟着看不见。
 *
 * 这是 CSS，jsdom 量不到布局，照仓库的老办法用源码断言钉住（见
 * `panels/PanelCard.spec.ts` 末尾那两节）。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'ChatPanel.vue'), 'utf8')

function rule(selector: string): string {
  const start = src.indexOf(`\n${selector} {`)
  if (start < 0) throw new Error(`ChatPanel.vue 里没有这条规则：${selector}`)
  const body = src.slice(src.indexOf('{', start) + 1, src.indexOf('}', start))
  // 注释里也会出现 `overflow-y: auto` 这种词，先摘掉再断言，免得注释把测试骗绿。
  return body.replace(/\/\*[\s\S]*?\*\//g, '')
}

describe('@ 候选菜单压扁时滚得动', () => {
  it('有一个高度上限，并且自己滚', () => {
    const menu = rule('.mention-menu')
    expect(menu).toMatch(/max-height:\s*\d/)
    expect(menu).toContain('overflow-y: auto')
  })

  it('横向仍然是裁的 —— 圆角靠它', () => {
    const menu = rule('.mention-menu')
    expect(menu).toContain('overflow-x: hidden')
    // 简写的 `overflow` 会把上面那条覆盖掉（两轴一起设），别让它回来。
    expect(menu).not.toMatch(/[^-]overflow:\s/)
  })
})
