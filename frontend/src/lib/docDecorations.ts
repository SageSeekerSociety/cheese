// 正文上的装饰：token chip（<@handle> / <#话题> / <&文件>）、评论下划线等。它们都是
// 把文档里**已经写好的**结构化东西画成看得懂的样子，没有一样去猜自然语言（军规 4）。
//
// 它们住在 lib/ 而不是文档面板里，理由和 docSlashMenu.ts 一样：这是**内容**而不是
// 布局 —— 一段解析、几个 widget、几个 tiptap 扩展，都不碰面板的状态。装饰上写什么
// （话题标题表、哪些评论串还开着）是面板的，所以这里导出的是**工厂**：面板把输入作为
// 回调递进来，扩展只读它们，不自己去取。
import type { Extension } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { EditorState } from '@tiptap/pm/state'
import type { RefNames } from './refChip'

import { Extension as TiptapExtension } from '@tiptap/core'
import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

import { commentAnchors } from './docSchema'
import { refChip, refTokens } from './refChip'

// ---- Comment highlights: the words an open thread is about, found by the
// thread's mark in the document (docSchema/commentAnchors). A resolved thread
// keeps its mark and loses its highlight; the thread being read is drawn
// stronger. ----

export const commentHighlightKey = new PluginKey('cheeseCommentHighlights')

function commentHighlights(doc: PMNode, open: ReadonlySet<string>, active: string | null): DecorationSet {
  const decos: Decoration[] = []
  for (const [id, ranges] of commentAnchors(doc)) {
    if (!open.has(id)) continue
    const attrs = { class: id === active ? 'doc-comment-mark is-active' : 'doc-comment-mark', 'data-comment': id }
    for (const range of ranges) decos.push(Decoration.inline(range.from, range.to, attrs))
  }
  return DecorationSet.create(doc, decos)
}

/** Highlight the open threads' words; poke with `commentHighlightKey` when the threads change. */
export function createCommentHighlights(opts: {
  open: () => ReadonlySet<string>
  active: () => string | null
}): Extension {
  return TiptapExtension.create({
    name: 'cheeseCommentHighlights',
    addProseMirrorPlugins() {
      return [
        new Plugin({
          key: commentHighlightKey,
          state: {
            init: (_cfg, state) => commentHighlights(state.doc, opts.open(), opts.active()),
            apply: (tr, old) =>
              tr.docChanged || tr.getMeta(commentHighlightKey)
                ? commentHighlights(tr.doc, opts.open(), opts.active())
                : old,
          },
          props: {
            decorations(state) {
              return this.getState(state) as DecorationSet
            },
          },
        }),
      ]
    },
  })
}

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
        Decoration.widget(from, () => refChip(ref.kind, ref.id, names()), { side: 1 }),
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
// over it. Stepping through the hidden characters one at a time would stall the
// caret on screen and leave half a token (`<@lix`) behind as text.
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
              if (event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return false
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

// ---- 与话题标题重复的大标题：面板上方已经用话题标题当页面标题，文档第一行若是
// 一模一样的一级标题，再显示一遍就是重复。它仍是文档的一部分（芝士读得到、导出也在），
// 只是不画出来：判据是纯字符串相等，不猜。----
export function createTitleEcho(opts: { title: () => string | null | undefined }): Extension {
  return TiptapExtension.create({
    name: 'cheeseTitleEcho',
    addProseMirrorPlugins() {
      return [
        new Plugin({
          props: {
            decorations(state) {
              const first = state.doc.firstChild
              const title = opts.title()?.trim()
              if (!first || !title || first.type.name !== 'heading' || first.attrs.level !== 1) return null
              if (first.textContent.trim() !== title) return null
              return DecorationSet.create(state.doc, [Decoration.node(0, first.nodeSize, { class: 'doc-title-echo' })])
            },
          },
        }),
      ]
    },
  })
}

// ---- 光标停在一个空段落上：段落里写一行淡字，说能打字，也能打「/」插入别的内容。整篇
// 都是空的时候不用它（那时另有一句说这篇文档是空的），不能改、不在编辑时也不出现。----
const emptyLineFocusKey = new PluginKey<boolean>('cheeseEmptyLineFocus')

export function createEmptyLineHint(): Extension {
  return TiptapExtension.create({
    name: 'cheeseEmptyLineHint',
    addProseMirrorPlugins() {
      const editor = this.editor
      return [
        new Plugin<boolean>({
          key: emptyLineFocusKey,
          // 在不在编辑：焦点进出编辑区时记一笔，淡字跟着出现、消失。
          state: {
            init: () => false,
            apply: (tr, focused) => (tr.getMeta(emptyLineFocusKey) as boolean | undefined) ?? focused,
          },
          props: {
            handleDOMEvents: {
              focus: (view) => {
                view.dispatch(view.state.tr.setMeta(emptyLineFocusKey, true))
                return false
              },
              blur: (view) => {
                view.dispatch(view.state.tr.setMeta(emptyLineFocusKey, false))
                return false
              },
            },
            decorations(state) {
              if (!editor.isEditable || !emptyLineFocusKey.getState(state)) return null
              const { selection, doc } = state
              const parent = selection.$from.parent
              if (!selection.empty || parent.type.name !== 'paragraph' || parent.content.size > 0) return null
              if (doc.childCount === 1 && doc.firstChild === parent) return null
              const at = selection.$from.before()
              return DecorationSet.create(doc, [Decoration.node(at, at + parent.nodeSize, { class: 'doc-empty-line' })])
            },
          },
        }),
      ]
    },
  })
}
