// 文档内查找：把正文里所有匹配的字标出来，给「第几个 / 共几个」，并能上下跳。
//
// 浏览器的 Ctrl+F 只认屏幕上的 DOM —— off-screen 的行今天还在 DOM 里（见
// lib/contentVisibility.ts），所以它凑巧能用，但那不是我们能保证的事。这里查的是
// 编辑器**节点树**里的文字，和渲染、和哪一块在屏幕外都无关。
//
// 两半：一半是纯函数（文档进、匹配数组出；再一小段下标推进），一半是把它画成装饰的
// tiptap 扩展。前者不认识编辑器，测试直接喂节点树；后者只读面板递进来的 query 与
// 当前下标。
import type { Node as PMNode } from '@tiptap/pm/model'

import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

/** 一处匹配：文档里的 [from, to) 区间。 */
export interface FindMatch {
  from: number
  to: number
}

/**
 * 把一段文字里第 `offset` 个字符的位置，映射回文档里的绝对位置。
 *
 * `block` 是 `blockPos` 处那个文本块的节点；`block.descendants` 给的子位置是相对块内
 * 容起点（= blockPos + 1）的，所以绝对位置 = blockPos + 1 + 子位置 + 段内偏移。
 */
function posAtOffset(block: PMNode, blockPos: number, offset: number): number {
  let consumed = 0
  let result = blockPos + 1 + offset
  let found = false
  block.descendants((child, childPos) => {
    if (found) return false
    if (!child.isText) return true
    const length = child.nodeSize
    if (offset <= consumed + length) {
      result = blockPos + 1 + childPos + (offset - consumed)
      found = true
      return false
    }
    consumed += length
    return true
  })
  return result
}

/**
 * 找出文档里所有（不区分大小写、互不重叠的）匹配，按出现顺序排列。
 *
 * 在**每个文本块内部**找，不跨块 —— 跨段的匹配没有意义（中间隔着换段）。
 * 块内跨标记（一段里有一部分加粗/标了颜色）算作连续文字，所以「**粗**体」里找「粗体」
 * 也能找到，这正是读者预期的那种「在一段里找」。
 */
export function findMatches(doc: PMNode, query: string): FindMatch[] {
  if (!query) return []
  const needle = query.toLowerCase()
  const matches: FindMatch[] = []
  doc.descendants((node, pos) => {
    if (!node.isTextblock) return true
    const haystack = node.textContent.toLowerCase()
    let index = haystack.indexOf(needle)
    let lastEnd = 0
    while (index !== -1) {
      // 互不重叠：从上一处匹配结束之后才开始下一处。
      if (index >= lastEnd) {
        const from = posAtOffset(node, pos, index)
        const to = posAtOffset(node, pos, index + needle.length)
        if (to > from) {
          matches.push({ from, to })
          lastEnd = index + needle.length
        }
      }
      index = haystack.indexOf(needle, index + 1)
    }
    // 已经把这个块里的文字找完了，不必再往下走它的子节点（否则会重复匹配）。
    return false
  })
  return matches
}

/** 匹配数变了以后，把当前下标夹回 [0, total) 之内。总数是 0 时是 0。 */
export function clampIndex(index: number, total: number): number {
  if (total <= 0) return 0
  return Math.min(Math.max(index, 0), total - 1)
}

/** 在上一个 / 下一个之间前后挪一格，到头绕回另一头。总数是 0 时不动。 */
export function stepIndex(index: number, total: number, delta: number): number {
  if (total <= 0) return 0
  const base = ((index % total) + total) % total
  return (((base + delta) % total) + total) % total
}

/** 用这个 key 打一发 setMeta(…, true) 让装饰按新的 query / 当前下标重画。 */
export const findHighlightKey = new PluginKey('cheeseDocFindHighlights')

export const FIND_HIT_CLASS = 'doc-find-hit'
export const FIND_ACTIVE_CLASS = 'doc-find-hit is-active'

function findHighlights(doc: PMNode, query: string, activeIndex: number): DecorationSet {
  if (!query) return DecorationSet.empty
  const matches = findMatches(doc, query)
  const active = clampIndex(activeIndex, matches.length)
  const decorations = matches.map((match, i) =>
    Decoration.inline(match.from, match.to, {
      class: i === active ? FIND_ACTIVE_CLASS : FIND_HIT_CLASS,
    })
  )
  return DecorationSet.create(doc, decorations)
}

/**
 * 把当前查询的匹配画成装饰（正文里高亮）。query / 当前下标换了就用
 * `findHighlightKey` 打一发 setMeta；文档变了它会自己按新文档重画。
 *
 * 返回的是 ProseMirror 插件而不是 tiptap 扩展：它由面板在文档打开后挂到编辑器上
 * （`editor.registerPlugin`），而不是编辑器的固定扩展表里 —— 查找是面板的一件事，
 * 不该在每一份文档的编辑器上常驻。
 */
export function findHighlightsPlugin(opts: { query: () => string; activeIndex: () => number }): Plugin {
  return new Plugin({
    key: findHighlightKey,
    state: {
      init: (_cfg, state) => findHighlights(state.doc, opts.query(), opts.activeIndex()),
      apply: (tr, old) =>
        tr.docChanged || tr.getMeta(findHighlightKey) ? findHighlights(tr.doc, opts.query(), opts.activeIndex()) : old,
    },
    props: {
      decorations(state) {
        return this.getState(state) as DecorationSet
      },
    },
  })
}
