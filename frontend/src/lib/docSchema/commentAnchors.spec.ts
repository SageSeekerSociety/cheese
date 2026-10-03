// @vitest-environment jsdom
// The words a comment is about: marked in the document, gone from its Markdown,
// not copied along with pasted text, and still on the same words after text
// lands before them.
import type { Node as PMNode } from '@tiptap/pm/model'

import { Editor } from '@tiptap/core'
import Collaboration from '@tiptap/extension-collaboration'
import { Slice } from '@tiptap/pm/model'
import { afterEach, describe, expect, it } from 'vitest'
import * as Y from 'yjs'

import { COMMENT_ANCHOR, commentAnchors, docExtensions, liveNode, serializeDoc, writeMarkdown } from '.'

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

describe('comment anchors through rewrites', () => {
  async function shared(markdown: string) {
    const doc = new Y.Doc()
    writeMarkdown(doc, markdown)
    const ed = new Editor({
      element: document.createElement('div'),
      extensions: [...docExtensions(), Collaboration.configure({ document: doc })],
    })
    editor = ed
    await new Promise((done) => setTimeout(done, 20))
    return { doc, ed }
  }
  function words(node: PMNode, thread: string): string[] {
    return (commentAnchors(node).get(thread) ?? []).map((r) => node.textBetween(r.from, r.to))
  }

  it('keep their words when the whole document is written from Markdown', async () => {
    const { doc, ed } = await shared('数据量到五百万行就评估迁移。\n\n第二段。\n')
    comment(ed, '五百万行', 't1')
    writeMarkdown(doc, '先用 PostgreSQL。数据量到五百万行就评估迁移。\n\n第二段，改过了。\n')
    expect(words(liveNode(doc), 't1')).toEqual(['五百万行'])
  })

  it('keep their words when a whole-document rewrite moves them within a changed paragraph', async () => {
    const { doc, ed } = await shared('数据量到五百万行就评估迁移。\n')
    comment(ed, '五百万行', 't1')
    writeMarkdown(doc, '先评估，数据量到五百万行再迁移。\n')
    expect(words(liveNode(doc), 't1')).toEqual(['五百万行'])
  })

  it('do not move onto what replaced their words: those are not the words commented on', async () => {
    const { doc, ed } = await shared('数据量到五百万行就评估迁移。\n')
    comment(ed, '五百万行', 't1')
    writeMarkdown(doc, '数据量到一千万条就评估迁移。\n')
    expect(words(liveNode(doc), 't1')).toEqual([])
  })

  it('do not move onto words typed over them', async () => {
    const ed = open('数据量到五百万行就评估迁移。')
    comment(ed, '五百万行', 't1')
    const { from, to } = span(ed, '五百万行')
    ed.view.dispatch(ed.state.tr.insertText('一千万条', from, to))
    expect(marked(ed, 't1')).toEqual([])
  })

  it('are gone with words deleted outright', async () => {
    const ed = open('数据量到五百万行就评估迁移。')
    comment(ed, '五百万行', 't1')
    const { from, to } = span(ed, '五百万行')
    ed.view.dispatch(ed.state.tr.delete(from, to))
    expect(marked(ed, 't1')).toEqual([])
  })
})
