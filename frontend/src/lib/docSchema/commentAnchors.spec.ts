// @vitest-environment jsdom
// The words a comment is about: marked in the document, gone from its Markdown,
// not copied along with pasted text, and still on the same words after text
// lands before them.
import { Editor } from '@tiptap/core'
import { Slice } from '@tiptap/pm/model'
import { afterEach, describe, expect, it } from 'vitest'

import { COMMENT_ANCHOR, commentAnchors, docExtensions, serializeDoc } from '.'

let editor: Editor | null = null
afterEach(() => {
  editor?.destroy()
  editor = null
})

function open(markdown: string): Editor {
  editor = new Editor({ element: document.createElement('div'), extensions: docExtensions(), content: '' })
  editor.commands.setContent(markdown, { contentType: 'markdown' })
  return editor
}

function span(ed: Editor, words: string): { from: number; to: number } {
  let found: { from: number; to: number } | null = null
  ed.state.doc.descendants((node, pos) => {
    if (found || !node.isTextblock) return
    const at = node.textContent.indexOf(words)
    if (at >= 0) found = { from: pos + 1 + at, to: pos + 1 + at + words.length }
  })
  if (!found) throw new Error(`no "${words}"`)
  return found
}

function comment(ed: Editor, words: string, thread: string) {
  const { from, to } = span(ed, words)
  ed.view.dispatch(ed.state.tr.addMark(from, to, ed.schema.marks[COMMENT_ANCHOR].create({ thread })))
}

function marked(ed: Editor, thread: string): string[] {
  return (commentAnchors(ed.state.doc).get(thread) ?? []).map((r) => ed.state.doc.textBetween(r.from, r.to))
}

describe('comment anchors', () => {
  it('leave no trace in the Markdown', () => {
    const ed = open('先用 **PostgreSQL**，周汇总超过 200 ms 再评估。')
    comment(ed, '200 ms', 't1')
    comment(ed, '周汇总超过 200', 't2')

    expect(serializeDoc(ed)).toBe('先用 **PostgreSQL**，周汇总超过 200 ms 再评估。')
  })

  it('stay on the same words when text is typed before them', () => {
    const ed = open('数据量到五百万行就评估迁移。')
    comment(ed, '五百万行', 't1')
    ed.view.dispatch(ed.state.tr.insertText('先用 PostgreSQL。', 1))

    expect(marked(ed, 't1')).toEqual(['五百万行'])
  })

  it('do not extend to words typed right after them', () => {
    const ed = open('数据量到五百万行就评估迁移。')
    comment(ed, '五百万行', 't1')
    ed.view.dispatch(ed.state.tr.insertText('左右', span(ed, '五百万行').to))

    expect(marked(ed, 't1')).toEqual(['五百万行'])
  })

  it('are not copied with pasted text', () => {
    const ed = open('数据量到五百万行就评估迁移。\n\n另一段。')
    comment(ed, '五百万行', 't1')
    const { from, to } = span(ed, '五百万行')
    let slice = ed.state.doc.slice(from, to)
    ed.view.someProp('transformPasted', (f) => {
      slice = f(slice, ed.view, false) as Slice
    })
    ed.view.dispatch(ed.state.tr.replace(span(ed, '另一段').to, span(ed, '另一段').to, slice))

    expect(marked(ed, 't1')).toEqual(['五百万行'])
  })
})
