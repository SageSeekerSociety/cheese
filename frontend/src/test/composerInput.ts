// 单测里替人在输入框（components/room/ComposerEditor.vue）里打字、按键。
//
// 输入框是一块 ProseMirror 编辑区，不是 <textarea>：happy-dom 不会把一次按键变成文字，
// 所以「打字」走编辑器自己的那条路——一次插入文字的事务，和真打字时浏览器报上来之后
// 编辑器做的是同一件事。按键照常派一个 keydown，编辑器和输入区接不接由它们自己决定。
import type { Editor } from '@tiptap/core'

import { TextSelection } from '@tiptap/pm/state'

import { docText, posAt } from '@/lib/composerDoc'

/** 输入框本身，或者 `root` 里的那个输入框（有几个就取第一个）。 */
export function composerEditor(root: ParentNode): Editor {
  const self = root instanceof Element && root.matches('.ProseMirror') ? root : null
  const dom = (self ?? root.querySelector('.ProseMirror')) as (HTMLElement & { editor?: Editor }) | null
  if (!dom?.editor) throw new Error('no composer in this tree')
  return dom.editor
}

/** 输入框里现在的正文（和要发出去的那段一样）。 */
export function composerText(root: ParentNode): string {
  return docText(composerEditor(root).state.doc)
}

/** 光标放到正文的第 `offset` 个字符前面（不给就是末尾）。 */
export function placeCaret(root: ParentNode, offset?: number): void {
  const ed = composerEditor(root)
  const { state } = ed.view
  const at = offset ?? docText(state.doc).length
  ed.view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, posAt(state.doc, at))))
}

/** 在光标处打一段字；`\n` 是换一行。 */
export async function typeInComposer(root: ParentNode, text: string): Promise<void> {
  const ed = composerEditor(root)
  text.split('\n').forEach((line, i) => {
    if (i > 0) ed.commands.splitBlock()
    if (line) ed.view.dispatch(ed.state.tr.insertText(line))
  })
  await Promise.resolve()
}

/** 整段换成 `text`，光标落在末尾：像人全选之后重新打了一遍。 */
export async function fillComposer(root: ParentNode, text: string): Promise<void> {
  const ed = composerEditor(root)
  ed.commands.selectAll()
  ed.commands.deleteSelection()
  await typeInComposer(root, text)
}

/** 在输入框上按一个键。返回这一下有没有被接走（preventDefault）。 */
export function pressInComposer(root: ParentNode, key: string, init: KeyboardEventInit = {}): boolean {
  const ed = composerEditor(root)
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init })
  ed.view.dom.dispatchEvent(event)
  return event.defaultPrevented
}
