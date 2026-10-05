// How the document's blocks look and are edited in the editor: the controls a
// block carries (a callout's kind, a timeline item's menu, the ＋ on stat
// cards), the formula rendering, the footnote popover.
//
// The schema (lib/docSchema/blocks.ts) already renders every block as plain,
// readable markup, which is what every other editor built on it shows. This
// file only adds views, by extending those same nodes: the schema does not
// change, so a page with views and the collaboration service without them
// read and write the same document.
//
// Two rules every view here keeps, learned the hard way in the prototype:
//   - A control is a non-editable button that swallows mousedown, so pressing
//     it never moves the caret or takes the editor's focus.
//   - A view that changes its own attributes (a class, a CSS variable) says so
//     in ignoreMutation; otherwise ProseMirror reads the change as an edit and
//     rebuilds the block.
import type { AnyExtension, Editor, NodeViewRendererProps } from '@tiptap/core'
import type { Transaction } from '@tiptap/pm/state'
import type { NodeView } from '@tiptap/pm/view'
import type { CalloutKind } from '../../../../lib/docSchema/blocks'
import type { AgentHook } from './mermaidView'

import { TextSelection } from '@tiptap/pm/state'

import { CALLOUT_KINDS, emptyItem } from '../../../../lib/docSchema/blocks'

import { chartView } from './chartView'
import { codeBlockView } from './mermaidView'
import { closePopover, controlButton, menuAt, popoverAt } from './popover'
import {
  calloutLabel,
  calloutShape,
  columnShape,
  detailsShape,
  fitTimeline,
  footnoteNote,
  footnoteText,
  mathShape,
  renderMath,
  statItemShape,
  statsShape,
  timelineItemShape,
  timelineShape,
} from './shapes'
import { arrive, depart, posOf, slideFrom } from './viewKit'

import { t } from '@/i18n'

type ViewFactory = (props: NodeViewRendererProps) => NodeView

const own =
  (dom: HTMLElement, ...controls: HTMLElement[]) =>
  (m: { type: string; target: Node }) =>
    (m.type === 'attributes' && m.target === dom) || controls.some((c) => c.contains(m.target))

/** Run `act` on the node at `pos` once it has played its way out; edits made
 *  meanwhile (by anyone) move `pos` along, and a node they delete is left alone. */
function afterLeaving(editor: Editor, pos: number, act: (pos: number) => void): void {
  let at: number | null = pos
  const follow = ({ transaction }: { transaction: Transaction }) => {
    if (at === null) return
    const mapped = transaction.mapping.mapResult(at)
    at = mapped.deleted ? null : mapped.pos
  }
  editor.on('transaction', follow)
  void depart(editor.view.nodeDOM(pos) as Element | null).then(() => {
    editor.off('transaction', follow)
    if (at !== null) act(at)
  })
}

/** Remove the item at `pos`; the last item of a block takes the block with it. */
function removeItem(editor: Editor, pos: number): void {
  const $pos = editor.state.doc.resolve(pos)
  if ($pos.parent.childCount === 1) dropItem(editor, pos)
  else afterLeaving(editor, pos, (at) => dropItem(editor, at))
}

function dropItem(editor: Editor, pos: number): void {
  const { state } = editor
  const $pos = state.doc.resolve(pos)
  const node = state.doc.nodeAt(pos)
  if (!node) return
  const tr = state.tr
  if ($pos.parent.childCount === 1) {
    tr.replaceWith($pos.before(), $pos.after(), state.schema.nodes.paragraph.create())
    tr.setSelection(TextSelection.create(tr.doc, $pos.before() + 1))
  } else {
    tr.delete(pos, pos + node.nodeSize)
  }
  editor.view.dispatch(tr.scrollIntoView())
  editor.view.focus()
}

function addItemAfter(editor: Editor, pos: number): void {
  const node = editor.state.doc.nodeAt(pos)
  if (!node) return
  const at = pos + node.nodeSize
  const tr = editor.state.tr.insert(at, emptyItem(editor.schema, node.type.name as 'timelineItem' | 'statItem'))
  tr.setSelection(TextSelection.create(tr.doc, at + 2))
  editor.view.dispatch(tr.scrollIntoView())
  editor.view.focus()
  arrive(editor.view.nodeDOM(at) as Element | null)
}

function moveItem(editor: Editor, pos: number, step: -1 | 1): void {
  const { state } = editor
  const $pos = state.doc.resolve(pos)
  const node = state.doc.nodeAt(pos)
  const index = $pos.index()
  const other = $pos.parent.maybeChild(index + step)
  if (!node || !other) return
  // Where the two were, to slide each from there to its new place.
  const otherPos = step < 0 ? pos - other.nodeSize : pos + node.nodeSize
  const was = (at: number) => (editor.view.nodeDOM(at) as Element | null)?.getBoundingClientRect()
  const movedFrom = was(pos)
  const otherFrom = was(otherPos)
  const tr = state.tr.delete(pos, pos + node.nodeSize)
  const at = step < 0 ? pos - other.nodeSize : pos + other.nodeSize
  tr.insert(at, node)
  tr.setSelection(TextSelection.create(tr.doc, at + 2))
  editor.view.dispatch(tr.scrollIntoView())
  editor.view.focus()
  slideFrom(editor.view.nodeDOM(at) as Element | null, movedFrom)
  slideFrom(editor.view.nodeDOM(step < 0 ? at + node.nodeSize : pos) as Element | null, otherFrom)
}

// ---- Callout: the kind is a label at the top; picking it opens the five kinds.

const calloutView: ViewFactory = ({ node, editor, getPos }) => {
  const kind = controlButton('doc-callout__kind', t('work.room.doc.blocks.calloutKind'))
  const { dom, content } = calloutShape(node.attrs.kind as string, kind)
  kind.addEventListener('click', () => {
    const pos = posOf(getPos)
    if (pos === null || !editor.isEditable) return
    const current = editor.state.doc.nodeAt(pos)?.attrs.kind as CalloutKind
    menuAt(
      kind,
      CALLOUT_KINDS.map((k) => ({
        label: calloutLabel(k),
        hint: t(`work.room.doc.blocks.kindHints.${k}`),
        pressed: k === current,
        run: () => editor.view.dispatch(editor.state.tr.setNodeAttribute(pos, 'kind', k)),
      }))
    )
  })
  return {
    dom,
    contentDOM: content,
    update(next) {
      if (next.type !== node.type) return false
      dom.dataset.kind = next.attrs.kind as string
      kind.textContent = calloutLabel(next.attrs.kind as string)
      return true
    },
    ignoreMutation: own(dom, kind),
  }
}

// ---- Timeline: the time column is as wide as the longest time.

const timelineView: ViewFactory = ({ node }) => {
  const dom = timelineShape()
  const fit = () => fitTimeline(dom)
  requestAnimationFrame(fit)
  // Web fonts change how wide the times are.
  void document.fonts?.ready.then(fit)
  return {
    dom,
    contentDOM: dom,
    update(next) {
      if (next.type !== node.type) return false
      requestAnimationFrame(fit)
      return true
    },
    ignoreMutation: own(dom),
  }
}

const timelineItemView: ViewFactory = ({ editor, getPos }) => {
  const dot = controlButton('doc-tl__dot', t('work.room.doc.blocks.itemActions'))
  const { dom, content } = timelineItemShape(dot)
  dot.addEventListener('click', () => {
    const pos = posOf(getPos)
    if (pos === null || !editor.isEditable) return
    menuAt(dot, [
      { label: t('work.room.doc.blocks.moveUp'), run: () => moveItem(editor, pos, -1) },
      { label: t('work.room.doc.blocks.moveDown'), run: () => moveItem(editor, pos, 1) },
      { label: t('work.room.doc.blocks.addAfter'), run: () => addItemAfter(editor, pos) },
      { label: t('work.room.doc.blocks.removeItem'), run: () => removeItem(editor, pos) },
    ])
  })
  return { dom, contentDOM: content, ignoreMutation: own(dom, dot) }
}

// ---- Stat cards: × on each card, ＋ after the last one while the caret is in them.

/** Mark `dom` with `doc-editing` while the caret is inside the node at getPos. */
function trackEditing(editor: Editor, getPos: NodeViewRendererProps['getPos'], dom: HTMLElement): () => void {
  const mark = () => {
    const pos = posOf(getPos)
    const { $from } = editor.state.selection
    let inside = false
    for (let d = $from.depth; d > 0; d--) if ($from.before(d) === pos) inside = true
    dom.classList.toggle('doc-editing', inside && editor.isFocused && editor.isEditable)
  }
  const blur = () => setTimeout(mark)
  editor.on('selectionUpdate', mark)
  editor.on('focus', mark)
  editor.on('blur', blur)
  return () => {
    editor.off('selectionUpdate', mark)
    editor.off('focus', mark)
    editor.off('blur', blur)
  }
}

const statsView: ViewFactory = ({ editor, getPos }) => {
  const { dom, cards } = statsShape()
  const add = controlButton('doc-stats__add', t('work.room.doc.blocks.addStat'), '＋')
  dom.append(add)
  add.addEventListener('click', () => {
    const pos = posOf(getPos)
    const node = pos === null ? null : editor.state.doc.nodeAt(pos)
    if (pos === null || !node || !node.lastChild) return
    addItemAfter(editor, pos + node.nodeSize - 1 - node.lastChild.nodeSize)
  })
  const stop = trackEditing(editor, getPos, dom)
  return { dom, contentDOM: cards, ignoreMutation: own(dom, add), destroy: stop }
}

const statItemView: ViewFactory = ({ editor, getPos }) => {
  const { dom, content } = statItemShape()
  const remove = controlButton('doc-stat__remove', t('work.room.doc.blocks.removeStat'), '×')
  dom.append(remove)
  remove.addEventListener('click', () => {
    const pos = posOf(getPos)
    if (pos !== null) removeItem(editor, pos)
  })
  return { dom, contentDOM: content, ignoreMutation: own(dom, remove) }
}

// ---- Columns: a menu on each column adds one beside it or removes it.

const columnView: ViewFactory = ({ editor, getPos }) => {
  const { dom, content } = columnShape()
  const handle = controlButton('doc-column__menu', t('work.room.doc.blocks.columnActions'), '⋯')
  dom.prepend(handle)
  handle.addEventListener('click', () => {
    const pos = posOf(getPos)
    if (pos === null || !editor.isEditable) return
    const $pos = editor.state.doc.resolve(pos)
    const count = $pos.parent.childCount
    const items = []
    if (count < 3) {
      items.push({
        label: t('work.room.doc.blocks.addColumn'),
        run: () => {
          const node = editor.state.doc.nodeAt(pos)
          if (!node) return
          const at = pos + node.nodeSize
          const column = editor.schema.nodes.column.create(null, editor.schema.nodes.paragraph.create())
          const tr = editor.state.tr.insert(at, column)
          tr.setSelection(TextSelection.create(tr.doc, at + 2))
          editor.view.dispatch(tr)
          editor.view.focus()
          arrive(editor.view.nodeDOM(at) as Element | null)
        },
      })
    }
    items.push({
      label: t('work.room.doc.blocks.removeColumn'),
      run: () => {
        // Two columns are the fewest: removing one leaves the other's content in place.
        if (count <= 2) {
          const other = $pos.parent.child($pos.index() === 0 ? 1 : 0)
          editor.view.dispatch(editor.state.tr.replaceWith($pos.before(), $pos.after(), other.content))
          editor.view.focus()
          return
        }
        afterLeaving(editor, pos, (at) => {
          const node = editor.state.doc.nodeAt(at)
          if (!node) return
          editor.view.dispatch(editor.state.tr.delete(at, at + node.nodeSize))
          editor.view.focus()
        })
      },
    })
    menuAt(handle, items)
  })
  return { dom, contentDOM: content, ignoreMutation: own(dom, handle) }
}

// ---- Fold: open while editing, closed while reading; the arrow toggles it here only.

const detailsView: ViewFactory = ({ editor }) => {
  const toggle = controlButton('doc-details__toggle', t('work.room.doc.blocks.toggleDetails'))
  const { dom, content } = detailsShape(toggle, editor.isEditable)
  return { dom, contentDOM: content, ignoreMutation: own(dom, toggle) }
}

// ---- Formulas: rendered with KaTeX; clicking one while editing opens its source.

function editMath(editor: Editor, getPos: NodeViewRendererProps['getPos'], anchor: HTMLElement, displayMode: boolean) {
  const pos = posOf(getPos)
  const node = pos === null ? null : editor.state.doc.nodeAt(pos)
  if (pos === null || !node || !editor.isEditable) return
  const el = document.createElement('div')
  el.classList.add('doc-pop--math')
  const input = document.createElement(displayMode ? 'textarea' : 'input')
  input.setAttribute('aria-label', t('work.room.doc.blocks.formulaSource'))
  input.value = node.attrs.latex as string
  const preview = document.createElement('div')
  preview.className = 'doc-pop__preview'
  el.append(input, preview)
  const draw = () => renderMath(preview, input.value, displayMode)
  draw()
  input.addEventListener('input', draw)
  // However the box closes (Enter, Esc, a click elsewhere), what is typed is kept.
  const save = () => {
    closePopover()
    editor.view.focus()
  }
  input.addEventListener('keydown', (e) => {
    const ev = e as KeyboardEvent
    if (ev.key === 'Enter' && (!displayMode || ev.metaKey || ev.ctrlKey)) {
      ev.preventDefault()
      save()
    }
  })
  popoverAt(anchor.getBoundingClientRect(), el, () => {
    const current = editor.state.doc.nodeAt(pos)
    if (current?.type === node.type && current.attrs.latex !== input.value) {
      editor.view.dispatch(editor.state.tr.setNodeAttribute(pos, 'latex', input.value))
    }
  })
  input.focus()
}

function mathView(displayMode: boolean): ViewFactory {
  return ({ node, editor, getPos }) => {
    let latex = node.attrs.latex as string
    const dom = mathShape(latex, displayMode)
    dom.addEventListener('click', () => editMath(editor, getPos, dom, displayMode))
    // A formula just inserted from the menu has no source yet: ask for it.
    if (!latex && editor.isEditable) requestAnimationFrame(() => editMath(editor, getPos, dom, displayMode))
    return {
      dom,
      update(next) {
        if (next.type !== node.type) return false
        if (next.attrs.latex !== latex) {
          latex = next.attrs.latex as string
          renderMath(dom, latex, displayMode)
        }
        return true
      },
      ignoreMutation: () => true,
      stopEvent: () => false,
    }
  }
}

// ---- Footnotes: the reference shows its note where it is read.

const footnoteRefView: ViewFactory = ({ node, editor }) => {
  const dom = document.createElement('sup')
  dom.dataset.footnoteRef = node.attrs.label as string
  dom.textContent = node.attrs.label as string
  dom.tabIndex = -1
  dom.addEventListener('click', () => {
    const label = dom.dataset.footnoteRef ?? ''
    const note = footnoteText(editor.state.doc, label)
    const el = footnoteNote(note)
    if (note && editor.isEditable) {
      const go = document.createElement('button')
      go.type = 'button'
      go.className = 'doc-menu__item'
      go.textContent = t('work.room.doc.blocks.editFootnote')
      go.addEventListener('click', () => {
        closePopover()
        const tr = editor.state.tr.setSelection(TextSelection.create(editor.state.doc, note.pos + 1))
        editor.view.dispatch(tr.scrollIntoView())
        editor.view.focus()
      })
      el.append(go)
    }
    popoverAt(dom.getBoundingClientRect(), el)
  })
  return {
    dom,
    update(next) {
      if (next.type !== node.type) return false
      dom.dataset.footnoteRef = next.attrs.label as string
      dom.textContent = next.attrs.label as string
      return true
    },
    ignoreMutation: () => true,
  }
}

const VIEWS: Record<string, ViewFactory> = {
  callout: calloutView,
  timeline: timelineView,
  timelineItem: timelineItemView,
  stats: statsView,
  statItem: statItemView,
  column: columnView,
  details: detailsView,
  mathInline: mathView(false),
  mathBlock: mathView(true),
  footnoteRef: footnoteRefView,
  chart: chartView,
}

/** The document's extensions with the editor's views on its blocks. */
export function withBlockViews(extensions: AnyExtension[], agent: AgentHook): AnyExtension[] {
  const views: Record<string, ViewFactory> = { ...VIEWS, codeBlock: codeBlockView(agent) }
  return extensions.map((extension) => {
    const view = views[extension.name]
    if (!view || extension.type !== 'node') return extension
    return (extension as unknown as { extend: (config: object) => AnyExtension }).extend({
      addNodeView: () => view,
    })
  })
}
