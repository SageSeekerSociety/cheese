import type { Extension } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { EditorState } from '@tiptap/pm/state'
import type { RefNames } from './refChip'

import { Extension as TiptapExtension } from '@tiptap/core'
import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

import { refChip, refTokens } from './refChip'

// ---- 结构化 token 装饰 (spec §9.1): decorate our OWN tokens — <@handle> /
// <#topicId> / <&path> — as clickable chips in the doc, read-only and edit
// alike. Deterministic token parsing, never NL guessing.
// ---- 引用 token（<@handle> / <#话题> / <&文件>）画成 chip：和对话里同一个样子、
// 同一套语法（lib/refChip.ts），否则同一个 token 在对话里是 chip、在文档里是一串
// 尖括号。The raw token stays in the document (markdown is the source of truth);
// the chip is display-only. ----

function tokenDecorations(doc: PMNode, names: () => RefNames): DecorationSet {
  const decos: Decoration[] = []
  doc.descendants((node, pos) => {
    if (!node.isText) return
    for (const ref of refTokens(node.text ?? '')) {
      const from = pos + ref.index
      // hide the raw token (inline display:none) + widget(show the chip):
      // the doc keeps `<&path>` verbatim, the reader sees 「(文件图标) name」.
      // (prosemirror-view has no Decoration.replace — widget/inline/node only.)
      decos.push(
        // 只读时它是一颗能 Tab 到的按钮；能编辑时不是 —— 光标在字里用方向键走，每颗 chip
        // 一个 Tab 停点会把焦点从正在写的地方拉走。
        Decoration.widget(from, (view) => refChip(ref.kind, ref.id, names(), { focusable: !view.editable }), {
          side: 1,
        }),
        Decoration.inline(from, from + ref.length, { style: 'display: none' })
      )
    }
  })
  return DecorationSet.create(doc, decos)
}

/** Dispatch with this meta set to redraw the chips: the names they show
 *  arrived (the roster loads after the document) or changed. */
export const tokenChipsKey = new PluginKey('cheeseTokenChips')

// A chip shows as one thing, so the caret treats it as one: Backspace after a
// chip takes the whole reference, Delete before it likewise, and ← / → step
// over it, by character or by word alike. Stepping through the hidden
// characters one at a time would stall the caret on screen and leave half a
// token (`<@lix`) behind as text.
function tokenAround(state: EditorState, side: 'before' | 'after'): { from: number; to: number } | null {
  const { $from, empty } = state.selection
  if (!empty || !$from.parent.isTextblock) return null
  const start = $from.start()
  const text = $from.parent.textBetween(0, $from.parent.content.size, undefined, '\uFFFC')
  const at = $from.parentOffset
  for (const ref of refTokens(text)) {
    if (side === 'before' && ref.index + ref.length === at) return { from: start + ref.index, to: start + at }
    if (side === 'after' && ref.index === at) return { from: start + at, to: start + at + ref.length }
  }
  return null
}

/** `names` says whom and which topic each token names; it is read when a chip is drawn. */
export function createTokenChips(opts: { names: () => RefNames }): Extension {
  return TiptapExtension.create({
    name: 'cheeseTokenChips',
    addProseMirrorPlugins() {
      return [
        new Plugin({
          key: tokenChipsKey,
          state: {
            init: (_cfg, state) => tokenDecorations(state.doc, opts.names),
            apply: (tr, old) =>
              tr.docChanged || tr.getMeta(tokenChipsKey) ? tokenDecorations(tr.doc, opts.names) : old,
          },
          props: {
            decorations(state) {
              return this.getState(state)
            },
            handleKeyDown(view, event) {
              // Ctrl / Option with these keys moves or deletes by word, and a chip is one word.
              if (event.shiftKey || event.metaKey) return false
              const side =
                event.key === 'Backspace' || event.key === 'ArrowLeft'
                  ? 'before'
                  : event.key === 'Delete' || event.key === 'ArrowRight'
                    ? 'after'
                    : null
              const token = side && tokenAround(view.state, side)
              if (!token) return false
              const { tr } = view.state
              if (event.key === 'Backspace' || event.key === 'Delete') {
                if (!view.editable) return false
                view.dispatch(tr.delete(token.from, token.to))
              } else {
                const to = event.key === 'ArrowLeft' ? token.from : token.to
                view.dispatch(tr.setSelection(TextSelection.create(tr.doc, to)))
              }
              return true
            },
          },
        }),
      ]
    },
  })
}
