// @vitest-environment jsdom
// 光标停在空段落上才有那一行淡字：有字、整篇是空的、不能改、不在编辑时都没有。
import { Editor } from '@tiptap/core'
import { afterEach, describe, expect, it } from 'vitest'

import { createEmptyLineHint } from './docDecorations'
import { docExtensions } from './docSchema'

const editors: Editor[] = []
afterEach(() => editors.splice(0).forEach((e) => e.destroy()))

function editor(html: string) {
  const element = document.createElement('div')
  document.body.appendChild(element)
  const ed = new Editor({ element, extensions: [...docExtensions(), createEmptyLineHint()], content: html })
  editors.push(ed)
  return ed
}
/** The person clicks into the document. */
const enter = (ed: Editor) => ed.view.dom.dispatchEvent(new FocusEvent('focus'))
const hinted = (ed: Editor) => ed.view.dom.querySelectorAll('.doc-empty-line').length

describe('空段落里的提示', () => {
  it('只在光标所在的那个空段落上', () => {
    const ed = editor('<p>第一段</p><p></p><p></p>')
    enter(ed)
    ed.commands.setTextSelection(ed.state.doc.child(0).nodeSize + 1)
    expect(hinted(ed)).toBe(1)
    ed.commands.setTextSelection(2)
    expect(hinted(ed)).toBe(0)
    ed.commands.setTextSelection(ed.state.doc.child(0).nodeSize + 1)
    ed.view.dom.dispatchEvent(new FocusEvent('blur'))
    expect(hinted(ed)).toBe(0)
  })

  it('整篇是空的、不能改时都没有', () => {
    const empty = editor('<p></p>')
    enter(empty)
    expect(hinted(empty)).toBe(0)

    const ed = editor('<p>第一段</p><p></p>')
    enter(ed)
    ed.commands.setTextSelection(ed.state.doc.child(0).nodeSize + 1)
    ed.setEditable(false)
    ed.view.dispatch(ed.state.tr)
    expect(hinted(ed)).toBe(0)
  })
})
