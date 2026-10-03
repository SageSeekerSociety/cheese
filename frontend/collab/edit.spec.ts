// The agent's passage edits replay only the blocks they touch, rebuilt from
// Markdown, which has no comment marks; the words a comment is about keep their
// thread through such an edit, and words the edit rewrote do not pass it on.
import type { Node as PMNode } from '@tiptap/pm/model'

import { Transform } from '@tiptap/pm/transform'
import { describe, expect, it } from 'vitest'

import { COMMENT_ANCHOR, commentAnchors, nodeMarkdown, parseMarkdown, withoutSuggestions } from '../src/lib/docSchema'

import { applyEdits } from './edit'

function commented(markdown: string, words: string, thread: string): PMNode {
  const doc = parseMarkdown(markdown)
  let at = -1
  doc.descendants((node, pos) => {
    if (at < 0 && node.isTextblock && node.textContent.includes(words)) at = pos + 1 + node.textContent.indexOf(words)
  })
  const mark = doc.type.schema.marks[COMMENT_ANCHOR].create({ thread })
  return new Transform(doc).addMark(at, at + words.length, mark).doc
}
function marked(doc: PMNode, thread: string): string[] {
  return (commentAnchors(doc).get(thread) ?? []).map((r) => doc.textBetween(r.from, r.to))
}

describe('a comment through the agent’s edit', () => {
  it('keeps its words when the edit changes the rest of their paragraph', () => {
    const live = commented('数据量到五百万行就评估迁移。\n', '五百万行', 't1')
    const result = applyEdits(live, [{ old: '就评估迁移', new: '就开始评估迁到 ClickHouse' }], 'direct', 'cheese')
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(marked(result.doc, 't1')).toEqual(['五百万行'])
  })

  it('does not pass it on to the words that replaced its own', () => {
    const live = commented('数据量到五百万行就评估迁移。\n', '五百万行', 't1')
    const result = applyEdits(live, [{ old: '五百万行', new: '一千万条' }], 'direct', 'cheese')
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(marked(result.doc, 't1')).toEqual([])
  })
})

describe('the agent’s suggestion inside a block', () => {
  it.each([
    ['a status tag', '结果 {✓ 通过}。\n', '{✓ 通过}', '{✗ 不通过}'],
    ['a timeline item', ':::timeline\n- 周一 | 开始\n  说明。\n:::\n', '说明。', '新的说明。'],
    ['a stat card', ':::stats\n- 甲 | 1 | +2%\n:::\n', '| 1 |', '| 3 |'],
    ['a footnote', '正文[^1]。\n\n[^1]: 旧\n', '[^1]: 旧', '[^1]: 新'],
  ])('leaves %s as it was until someone accepts it', (_name, markdown, old, replacement) => {
    const result = applyEdits(parseMarkdown(markdown), [{ old, new: replacement }], 'suggest', 'cheese')
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(nodeMarkdown(withoutSuggestions(result.doc))).toBe(markdown.trimEnd())
  })

  it('cannot propose a different kind of callout, only make the change', () => {
    const live = parseMarkdown('> [!NOTE]\n> 补充。\n')
    const suggested = applyEdits(live, [{ old: '[!NOTE]', new: '[!WARNING]' }], 'suggest', 'cheese')
    expect(suggested.ok).toBe(false)
    const direct = applyEdits(live, [{ old: '[!NOTE]', new: '[!WARNING]' }], 'direct', 'cheese')
    if (!direct.ok) throw new Error(JSON.stringify(direct))
    expect(nodeMarkdown(direct.doc)).toBe('> [!WARNING]\n> 补充。')
  })
})
