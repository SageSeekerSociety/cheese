// 正文上那三种装饰：token chip（<@handle> / <#话题> / <&文件>）、支线徽章、评论
// 下划线。三样都是把文档里**已经写好的**结构化东西画成看得懂的样子，没有一样去猜
// 自然语言（军规 4）。
//
// 它们住在 lib/ 而不是文档面板里，理由和 docSlashMenu.ts 一样：这是**内容**而不是
// 布局 —— 一段解析、几个 widget、三个 tiptap 扩展，都不碰面板的状态，也不该为了读
// 一行徽章文案去翻两千行的编辑器组件。
//
// 三样都要两份输入：「哪一段上有装饰」（段落 index，来自一次 GET /docs 的位置对齐）
// 和「装饰上写什么」（支线的标题与状态、话题标题表）。这两份输入是面板的，所以这里
// 导出的是**工厂**：面板把两份输入作为回调递进来，扩展只读它们，不自己去取。
import type { Extension } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { EditorState, Transaction } from '@tiptap/pm/state'
import type { RefNames } from './refChip'

import { Extension as TiptapExtension } from '@tiptap/core'
import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import { ySyncPluginKey } from '@tiptap/y-tiptap'

import { commentAnchors } from './docSchema'
import { refChip, refTokens } from './refChip'

import { t } from '@/i18n'

export interface LiveRefFacts {
  title: string | null
  status: string
}

const STATUS_KEY: Record<string, string> = {
  open: 'work.room.doc.status.inProgress',
  in_progress: 'work.room.doc.status.inProgress',
  active: 'work.room.doc.status.inProgress',
  draft: 'work.room.doc.status.draft',
  archived: 'work.room.doc.status.done',
  completed: 'work.room.doc.status.done',
}
function statusLabel(s: string): string {
  const key = STATUS_KEY[s]
  return key ? t(key) : s
}

// ---- 支线徽章: a doc paragraph that was upgraded into a subtopic stays in
// place as a live-ref showing that subtopic's live status. The badge is a
// ProseMirror WIDGET decoration appended at the end of the upgraded paragraph:
// it lives in the document flow, so it can never float over (and swallow clicks
// meant for) neighbouring text — unlike the old absolutely-positioned overlay
// track, which created cursor dead zones. ----

export const liveRefKey = new PluginKey('cheeseLiveRefBadges')

// A change that arrived from the collaborative document rather than this
// editor's own typing: the document opening, somebody else's edit, an undo.
// Mapping the old decorations through it would carry them along a document they
// were never built for (opening maps an empty set onto the whole text), so
// those rebuild from the index, as a fresh load did.
function fromDocument(tr: Transaction): boolean {
  return !!(tr.getMeta(ySyncPluginKey) as { isChangeOrigin?: boolean } | undefined)?.isChangeOrigin
}

// Build the badge element a live-ref widget renders as.
function liveRefWidget(topicId: string, facts: LiveRefFacts): HTMLElement {
  const { title: subTitle, status } = facts
  const el = document.createElement('span')
  el.className = 'doc-liveref'
  el.dataset.topic = topicId
  el.contentEditable = 'false'
  el.setAttribute('role', 'button')
  el.title = t('work.room.doc.liveRefTitle', {
    title: subTitle ?? t('work.room.doc.thisTask'),
    status: statusLabel(status),
  })
  const dot = document.createElement('span')
  dot.className = `doc-liveref__dot is-${status}`
  // 图标而不是 🧩：emoji 在不同系统上是彩色位图，尺寸和基线都不跟随字号，混在
  // 正文里显得很脏。用 puzzle 而不是 mdi-source-branch，是因为后者在本组件里
  // 已经代表 Git 标签页和「磁盘版本分叉」提示，一个图标不该同时是三件事。
  // (ProseMirror widget 是裸 DOM，用不了 <v-icon>；@mdi/font 是全局 CSS，
  //  所以这里直接写 mdi 的字体类。)
  const icon = document.createElement('i')
  icon.className = 'mdi mdi-puzzle-outline doc-liveref__icon'
  icon.setAttribute('aria-hidden', 'true')
  const label = document.createElement('span')
  label.className = 'doc-liveref__label'
  label.textContent = subTitle ?? t('work.room.doc.subtopic')
  const st = document.createElement('span')
  st.className = 'doc-liveref__status'
  st.textContent = statusLabel(status)
  el.append(dot, icon, label, st)
  return el
}

// Top-level doc-node index → the subtopic id that paragraph was upgraded into.
// Positional zip: server node i ↔ ProseMirror doc.child(i), the same alignment
// contract the panel uses when it flashes a paragraph.
function liveRefDecorations(
  doc: PMNode,
  index: Map<number, string>,
  factsOf: (topicId: string) => LiveRefFacts
): DecorationSet {
  const decos: Decoration[] = []
  doc.forEach((node, offset, index_) => {
    const topicId = index.get(index_)
    if (!topicId) return
    // End of the block's content (just inside its closing token) — the badge
    // renders after the paragraph's last character, in flow.
    const pos = offset + Math.max(node.nodeSize - 1, 1)
    // key 决定两次重建之间「这还是同一个装饰吗」：prosemirror-view 的
    // `WidgetType.eq` 一看见 key 相等就短路返回 true，DOM 于是原样留着。所以
    // key 里必须带上徽章会变的那点东西——只写 topicId 的话，支线改了标题、跑完
    // 收了工，徽章上的字还停在第一次渲染的那一刻，而这段代码的全部意义就是让
    // 它跟着变。反过来，没变的时候 key 一样，DOM 不重建，读的人也不会看见闪。
    const { title: subTitle, status } = factsOf(topicId)
    decos.push(
      Decoration.widget(pos, () => liveRefWidget(topicId, factsOf(topicId)), {
        side: 1,
        key: `liveref-${topicId}-${status}-${subTitle ?? ''}`,
      })
    )
  })
  return DecorationSet.create(doc, decos)
}

export function createLiveRefBadges(opts: {
  /** 段落 index → 这一段升级出来的那个地点 id。 */
  index: () => Map<number, string>
  /** 徽章上写什么：那条支线现在的标题和状态。 */
  factsOf: (topicId: string) => LiveRefFacts
}): Extension {
  return TiptapExtension.create({
    name: 'cheeseLiveRefBadges',
    addProseMirrorPlugins() {
      return [
        new Plugin({
          key: liveRefKey,
          state: {
            init: (_cfg, state) => liveRefDecorations(state.doc, opts.index(), opts.factsOf),
            apply: (tr, old) => {
              // Explicit poke (fresh /docs data or topicList change) → rebuild.
              if (tr.getMeta(liveRefKey) || fromDocument(tr)) {
                return liveRefDecorations(tr.doc, opts.index(), opts.factsOf)
              }
              // Local edits: map the existing widgets along, so a badge stays
              // glued to its paragraph while the user types (indices may shift
              // until the next server refresh; mapping avoids mis-attachment).
              return tr.docChanged ? old.map(tr.mapping, tr.doc) : old
            },
          },
          props: {
            decorations(state) {
              return this.getState(state)
            },
          },
        }),
      ]
    },
  })
}

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
