// 输入框里的代码：```` ``` ```` 围起来的几行画成代码块，`` `一段` `` 画成行内代码，代码块里
// Tab 是缩进。
//
// 只是画：正文还是原来那几个字符，发出去以后由消息自己渲染成代码。其余的 Markdown
// （加粗、列表）照原样显示——输入框里贴代码最难用，别的格式不在这一步。
import type { Extension } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { EditorState, Transaction } from '@tiptap/pm/state'

import { Extension as TiptapExtension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

const INDENT = '    '
const FENCE = /^\s*```/
const INLINE_CODE = /`[^`]+`/g

type LineKind = 'text' | 'fence' | 'code'

/** 每一行是什么：普通文字、围栏那一行、还是围栏里的代码。没合上的围栏一直算到最后。 */
function lineKinds(doc: PMNode): LineKind[] {
  const kinds: LineKind[] = []
  let inside = false
  doc.forEach((para) => {
    if (FENCE.test(para.textContent)) {
      kinds.push('fence')
      inside = !inside
    } else kinds.push(inside ? 'code' : 'text')
  })
  return kinds
}

function codeDecorations(doc: PMNode): DecorationSet {
  const kinds = lineKinds(doc)
  const decos: Decoration[] = []
  let i = 0
  doc.forEach((para, pos) => {
    const kind = kinds[i++]
    if (kind !== 'text') {
      decos.push(Decoration.node(pos, pos + para.nodeSize, { class: `composer-code-line composer-code-line--${kind}` }))
      return
    }
    for (const m of para.textContent.matchAll(INLINE_CODE)) {
      const from = pos + 1 + m.index
      decos.push(Decoration.inline(from, from + m[0].length, { class: 'composer-code' }))
    }
  })
  return DecorationSet.create(doc, decos)
}

/** 选区碰到的每一行代码，各自开头在文档里的位置；有一行不是代码就不归这里管。 */
function selectedCodeLines(state: EditorState): number[] | null {
  const kinds = lineKinds(state.doc)
  const { from, to } = state.selection
  const starts: number[] = []
  let i = 0
  let outside = false
  state.doc.forEach((para, pos) => {
    const kind = kinds[i++]
    if (pos + para.nodeSize <= from || pos > to) return
    if (kind !== 'code') outside = true
    starts.push(pos + 1)
  })
  return outside || !starts.length ? null : starts
}

function outdent(tr: Transaction, starts: number[]): Transaction {
  for (const start of [...starts].reverse()) {
    const lead = /^ {1,4}/.exec(tr.doc.resolve(start).parent.textContent)
    if (lead) tr.delete(start, start + lead[0].length)
  }
  return tr
}

export function createComposerCode(): Extension {
  return TiptapExtension.create({
    name: 'composerCode',
    addProseMirrorPlugins() {
      return [
        new Plugin({
          key: new PluginKey('composerCode'),
          state: {
            init: (_cfg, state) => codeDecorations(state.doc),
            apply: (tr, old) => (tr.docChanged ? codeDecorations(tr.doc) : old),
          },
          props: {
            decorations(state) {
              return this.getState(state)
            },
            handleKeyDown(view, event) {
              if (event.key !== 'Tab' || event.ctrlKey || event.metaKey || event.altKey) return false
              const starts = selectedCodeLines(view.state)
              // 不在代码里：Tab 照旧把焦点移到下一个控件。
              if (!starts) return false
              const { tr, selection } = view.state
              if (event.shiftKey) view.dispatch(outdent(tr, starts))
              else if (selection.empty) view.dispatch(tr.insertText(INDENT))
              else {
                for (const start of [...starts].reverse()) tr.insertText(INDENT, start)
                view.dispatch(tr)
              }
              return true
            },
          },
        }),
      ]
    },
  })
}
