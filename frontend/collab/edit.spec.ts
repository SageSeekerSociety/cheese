// The agent's passage edits replay only the blocks they touch, rebuilt from
// Markdown, which has no comment marks; the words a comment is about keep their
// thread through such an edit, and words the edit rewrote do not pass it on.
import type { Node as PMNode } from '@tiptap/pm/model'

import { EditorState } from '@tiptap/pm/state'
import { Transform } from '@tiptap/pm/transform'
import { describe, expect, it } from 'vitest'

import {
  COMMENT_ANCHOR,
  commentAnchors,
  nodeMarkdown,
  parseMarkdown,
  pendingSuggestions,
  withoutSuggestions,
} from '../src/lib/docSchema'
import { decideSuggestion } from '../src/lib/docSuggestionList'

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

describe('an agent’s change over a pending suggestion', () => {
  function proposedBy(actor: string): PMNode {
    const live = parseMarkdown('第一段讲目标。\n')
    const result = applyEdits(live, [{ old: '讲目标', new: '讲目的' }], 'suggest', actor)
    if (!result.ok) throw new Error(JSON.stringify(result))
    return result.doc
  }

  it('drops a suggestion an agent seat left, and goes in', () => {
    const live = proposedBy('previous-seat')
    expect(pendingSuggestions(live)).toEqual([expect.objectContaining({ author: 'previous-seat' })])
    const result = applyEdits(live, [{ old: '第一段讲目标。', new: '第一段讲任务。' }], 'direct', 'cheese-agent', {
      editorIsAgent: true,
      agentAuthors: new Set(['previous-seat', 'cheese-agent']),
    })
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(nodeMarkdown(result.doc)).toBe('第一段讲任务。')
    expect(pendingSuggestions(result.doc)).toEqual([])
  })

  it('is refused where a person’s suggestion is pending, and drops nothing', () => {
    const live = proposedBy('teacher')
    const result = applyEdits(live, [{ old: '第一段讲目标。', new: '第一段讲任务。' }], 'direct', 'cheese-agent', {
      editorIsAgent: true,
      agentAuthors: new Set(['cheese-agent']),
    })
    expect(result).toMatchObject({ ok: false, status: 409, body: { error: 'edit', index: 0, reason: 'suggested' } })
    // 「什么都没丢」要在**一份真的被改过的文档**上读，不能断在传进去的那份上：
    // applyEdits 从不写它，那两条断言换成原样返回的空实现也照样绿。这里落一笔
    // 碰不到建议的改动 —— 人的建议还在，而它该在的那句话原样没动。
    const after = applyEdits(live, [{ old: '第一段', new: '第一部分' }], 'direct', 'cheese-agent', {
      editorIsAgent: true,
      agentAuthors: new Set(['cheese-agent']),
    })
    if (!after.ok) throw new Error(JSON.stringify(after))
    expect(pendingSuggestions(after.doc)).toEqual([expect.objectContaining({ author: 'teacher' })])
    expect(nodeMarkdown(withoutSuggestions(after.doc))).toBe('第一部分讲目标。')
  })

  it('a person’s change is refused, even over an agent’s suggestion', () => {
    const live = proposedBy('cheese-agent')
    const result = applyEdits(live, [{ old: '第一段讲目标。', new: '第一段讲任务。' }], 'direct', 'xiaowang', {
      editorIsAgent: false,
      agentAuthors: new Set(['cheese-agent']),
    })
    expect(result).toMatchObject({ ok: false, status: 409, body: { error: 'edit', index: 0, reason: 'suggested' } })
  })
})

describe('the agent’s suggestion inside a block', () => {
  it.each([
    ['a status tag', '结果 {✓ 通过}。\n', '{✓ 通过}', '{✗ 不通过}'],
    ['a timeline item', ':::timeline\n- 周一 | 开始\n  说明。\n:::\n', '说明。', '新的说明。'],
    ['a stat card', ':::stats\n- 甲 | 1 | +2%\n:::\n', '| 1 |', '| 3 |'],
    ['a footnote', '正文[^1]。\n\n[^1]: 旧\n', '[^1]: 旧', '[^1]: 新'],
    ['a chart', ':::chart bar\n| 周   | 次数  |\n| --- | --- |\n| 一   | 10  |\n:::\n', '| 10  |', '| 12  |'],
  ])('leaves %s as it was until someone accepts it', (_name, markdown, old, replacement) => {
    const result = applyEdits(parseMarkdown(markdown), [{ old, new: replacement }], 'suggest', 'cheese')
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(nodeMarkdown(withoutSuggestions(result.doc))).toBe(markdown.trimEnd())
  })

  it.each([
    ['a callout', '> [!NOTE]\n> 补充。\n', '[!NOTE]', '[!WARNING]', '> [!WARNING]\n> 补充。'],
    [
      'a chart',
      ':::chart bar\n| 周   | 次数  |\n| --- | --- |\n| 一   | 10  |\n:::\n',
      ':::chart bar',
      ':::chart line',
      ':::chart line\n| 周   | 次数  |\n| --- | --- |\n| 一   | 10  |\n:::',
    ],
  ])('proposes %s of another kind, which takes effect when accepted', (_name, markdown, old, replacement, becomes) => {
    const result = applyEdits(parseMarkdown(markdown), [{ old, new: replacement }], 'suggest', 'cheese')
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(nodeMarkdown(withoutSuggestions(result.doc))).toBe(markdown.trimEnd())
    expect(decided(result.doc, true)).toBe(becomes)
  })
})

/** The text once the one pending suggestion in `doc` is accepted or rejected. */
function decided(doc: PMNode, accept: boolean): string {
  const [pending] = pendingSuggestions(doc)
  let after = doc
  decideSuggestion(EditorState.create({ doc }), (tr) => (after = tr.doc), pending.id, accept)
  return nodeMarkdown(withoutSuggestions(after))
}

describe('an agent proposing whole blocks', () => {
  const live = parseMarkdown('第一段，讲范围。\n\n第二段，讲目标。\n')
  const TEXT = '第一段，讲范围。\n\n第二段，讲目标。'

  it.each([
    [
      'a new paragraph at the end',
      '讲目标。',
      '讲目标。\n\n先定边界再排期。',
      '先定边界再排期。',
      `${TEXT}\n\n先定边界再排期。`,
    ],
    [
      'two paragraphs between two',
      '讲范围。',
      '讲范围。\n\n第三段。\n\n第四段。',
      '第三段。\n\n第四段。',
      '第一段，讲范围。\n\n第三段。\n\n第四段。\n\n第二段，讲目标。',
    ],
    ['a heading', '讲目标。', '讲目标。\n\n## 下一步', '## 下一步', `${TEXT}\n\n## 下一步`],
    ['a list', '讲目标。', '讲目标。\n\n- 甲\n- 乙', '- 甲\n- 乙', `${TEXT}\n\n- 甲\n- 乙`],
  ])(
    'proposes %s, leaving the text as it was until someone accepts it',
    (_name, old, replacement, proposed, accepted) => {
      const result = applyEdits(live, [{ old, new: replacement }], 'suggest', 'cheese')
      if (!result.ok) throw new Error(JSON.stringify(result))
      expect(nodeMarkdown(withoutSuggestions(result.doc))).toBe(TEXT)
      expect(pendingSuggestions(result.doc)).toEqual([
        expect.objectContaining({ author: 'cheese', old: '', new: proposed }),
      ])
      expect(decided(result.doc, true)).toBe(accepted)
      expect(decided(result.doc, false)).toBe(TEXT)
    }
  )

  it('proposes removing a paragraph whole', () => {
    const result = applyEdits(live, [{ old: '\n\n第二段，讲目标。', new: '' }], 'suggest', 'cheese')
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(nodeMarkdown(withoutSuggestions(result.doc))).toBe(TEXT)
    expect(pendingSuggestions(result.doc)).toEqual([expect.objectContaining({ old: '第二段，讲目标。', new: '' })])
    expect(decided(result.doc, true)).toBe('第一段，讲范围。')
    expect(decided(result.doc, false)).toBe(TEXT)
  })

  it('proposes turning a paragraph into a list item, and says what it becomes', () => {
    const result = applyEdits(live, [{ old: '第二段，讲目标。', new: '- 第二段，讲目标。' }], 'suggest', 'cheese')
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(pendingSuggestions(result.doc)).toEqual([
      expect.objectContaining({ old: '第二段，讲目标。', new: '- 第二段，讲目标。' }),
    ])
    expect(decided(result.doc, true)).toBe('第一段，讲范围。\n\n- 第二段，讲目标。')
    expect(decided(result.doc, false)).toBe(TEXT)
  })

  it('proposes an item added to a list', () => {
    const result = applyEdits(parseMarkdown('- 甲\n- 乙\n'), [{ old: '- 乙', new: '- 乙\n- 丙' }], 'suggest', 'cheese')
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(pendingSuggestions(result.doc)).toEqual([expect.objectContaining({ old: '', new: '- 丙' })])
    expect(decided(result.doc, true)).toBe('- 甲\n- 乙\n- 丙')
    expect(decided(result.doc, false)).toBe('- 甲\n- 乙')
  })

  // What still cannot be suggested is what the error, the tool description,
  // the editor's notice and the manual name: the rows and columns of a table,
  // the columns of a row of columns, the items of a timeline or stat cards.
  it.each([
    [
      'a row added to a table',
      '| 周 | 次数 |\n| --- | --- |\n| 一 | 10 |',
      '| 周 | 次数 |\n| --- | --- |\n| 一 | 10 |\n| 二 | 12 |',
    ],
    [
      'a column added to a table',
      '| 周 | 次数 |\n| --- | --- |\n| 一 | 10 |',
      '| 周 | 次数 | 人 |\n| --- | --- | --- |\n| 一 | 10 | 3 |',
    ],
    [
      'a column added to a row of columns',
      '::::columns\n:::column\n甲\n:::\n:::column\n乙\n:::\n::::',
      '::::columns\n:::column\n甲\n:::\n:::column\n乙\n:::\n:::column\n丙\n:::\n::::',
    ],
    [
      'an item added to a timeline',
      ':::timeline\n- 周一 | 开始\n  说明。\n:::',
      ':::timeline\n- 周一 | 开始\n  说明。\n- 周二 | 继续\n  再说。\n:::',
    ],
    ['a stat card added', ':::stats\n- 甲 | 1 | +2%\n:::', ':::stats\n- 甲 | 1 | +2%\n- 乙 | 2 | +3%\n:::'],
  ])('cannot propose %s, only make the change', (_name, before, after) => {
    const text = nodeMarkdown(parseMarkdown(before))
    const edit = { old: text, new: nodeMarkdown(parseMarkdown(after)) }
    expect(applyEdits(parseMarkdown(text), [edit], 'suggest', 'cheese')).toMatchObject({
      ok: false,
      status: 422,
      body: { reason: 'structure' },
    })
    const direct = applyEdits(parseMarkdown(text), [edit], 'direct', 'cheese')
    if (!direct.ok) throw new Error(JSON.stringify(direct))
    expect(nodeMarkdown(direct.doc)).toBe(edit.new)
  })
})

describe('an edit in a list whose item opens empty above a nested list', () => {
  // What an editor makes when someone indents the items below an empty bullet:
  // the empty item holds an empty line and the nested list.
  const live = parseMarkdown('x').type.schema.nodeFromJSON({
    type: 'doc',
    content: [
      { type: 'heading', attrs: { level: 2 }, content: [{ type: 'text', text: '分工' }] },
      {
        type: 'bulletList',
        content: [
          {
            type: 'listItem',
            content: [
              { type: 'paragraph' },
              {
                type: 'bulletList',
                content: [
                  {
                    type: 'listItem',
                    content: [{ type: 'paragraph', content: [{ type: 'text', text: '芝士：调度审查。' }] }],
                  },
                ],
              },
            ],
          },
          {
            type: 'listItem',
            content: [{ type: 'paragraph', content: [{ type: 'text', text: '芝士S：先让测试全绿。' }] }],
          },
        ],
      },
      { type: 'paragraph', content: [{ type: 'text', text: '后记。' }] },
    ],
  })

  it('is placed, as a suggestion or directly', () => {
    for (const mode of ['suggest', 'direct'] as const) {
      const result = applyEdits(live, [{ old: '先让测试全绿', new: '先补契约' }], mode, 'cheese-agent')
      if (!result.ok) throw new Error(`${mode}: ${JSON.stringify(result)}`)
      const reads = nodeMarkdown(mode === 'suggest' ? withoutSuggestions(result.doc) : result.doc)
      expect(reads).toContain(mode === 'suggest' ? '先让测试全绿' : '先补契约')
      if (mode === 'suggest') {
        expect(pendingSuggestions(result.doc)).toEqual([expect.objectContaining({ author: 'cheese-agent' })])
      }
      // The empty item and its nested list are still there.
      expect(result.doc.child(1).child(0).childCount).toBe(2)
    }
  })
})

describe('an agent proposing words at the end of the last paragraph', () => {
  const live = parseMarkdown('第一段，讲范围。\n\n第二段，讲目标。\n')

  it('proposes them inside that paragraph', () => {
    const result = applyEdits(live, [{ old: '讲目标。', new: '讲目标。先定边界再排期。' }], 'suggest', 'cheese')
    if (!result.ok) throw new Error(JSON.stringify(result))
    expect(nodeMarkdown(withoutSuggestions(result.doc))).toBe('第一段，讲范围。\n\n第二段，讲目标。')
    const [proposed] = pendingSuggestions(result.doc)
    let accepted = ''
    decideSuggestion(
      EditorState.create({ doc: result.doc }),
      (tr) => (accepted = nodeMarkdown(tr.doc)),
      proposed.id,
      true
    )
    expect(accepted).toBe('第一段，讲范围。\n\n第二段，讲目标。先定边界再排期。')
  })
})
