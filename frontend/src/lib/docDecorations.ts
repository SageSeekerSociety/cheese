// 正文上的装饰：评论下划线等（引用 token 画成 chip 在 lib/tokenChips.ts，输入框也用它）。它们都是
// 把文档里**已经写好的**结构化东西画成看得懂的样子，没有一样去猜自然语言（军规 4）。
//
// 它们住在 lib/ 而不是文档面板里，理由和 docSlashMenu.ts 一样：这是**内容**而不是
// 布局 —— 一段解析、几个 widget、几个 tiptap 扩展，都不碰面板的状态。装饰上写什么
// （话题标题表、哪些评论串还开着）是面板的，所以这里导出的是**工厂**：面板把输入作为
// 回调递进来，扩展只读它们，不自己去取。
import type { Extension } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'

import { Extension as TiptapExtension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

import { commentAnchors } from './docSchema'

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
