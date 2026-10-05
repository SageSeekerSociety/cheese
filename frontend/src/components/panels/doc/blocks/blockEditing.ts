// Typing inside the blocks: the keys that walk a timeline item or a stat card
// field by field, the hints in empty fields, the up/down colour on a change,
// the status tag shortcuts, and how a reader's table fits a phone.
//
// A timeline item and a stat card are rows of short fields. Enter moves to the
// next field and, from the last one, starts a new item; Enter in an empty item
// leaves the block, so the caret is never trapped in it (a shared document
// keeps no empty paragraph after its last block). Backspace at the start of a
// field goes back one; on an empty item it removes the item.
import type { Editor } from '@tiptap/core'
import type { Node as PMNode, ResolvedPos } from '@tiptap/pm/model'
import type { EditorView } from '@tiptap/pm/view'
import type { StatusKind } from '../../../../lib/docSchema/blocks'

import { Extension, InputRule } from '@tiptap/core'
import { NodeSelection, Plugin, PluginKey, TextSelection } from '@tiptap/pm/state'
import { CellSelection, deleteTable } from '@tiptap/pm/tables'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

import { emptyItem } from '../../../../lib/docSchema/blocks'
import { convertsInPlace, slashPluginKey } from '../../../../lib/docSlashMenu'
import { setStatus } from '../../../../lib/docStatus'

import { trendOf } from './shapes'

import { t } from '@/i18n'

interface FieldInfo {
  item: 'timelineItem' | 'statItem'
  index: number
  last: boolean
  placeholder: string
}

const FIELDS: Record<string, FieldInfo> = {
  timelineWhen: { item: 'timelineItem', index: 0, last: false, placeholder: 'when' },
  timelineTitle: { item: 'timelineItem', index: 1, last: false, placeholder: 'title' },
  timelineBody: { item: 'timelineItem', index: 2, last: true, placeholder: 'body' },
  statName: { item: 'statItem', index: 0, last: false, placeholder: 'name' },
  statValue: { item: 'statItem', index: 1, last: false, placeholder: 'value' },
  statDelta: { item: 'statItem', index: 2, last: true, placeholder: 'delta' },
}

/** Fields a reader does not need to see when they are empty. */
const OPTIONAL = new Set(['timelineBody'])

/** Leave the block the caret is in: a paragraph after it, the caret in that. */
function leaveBlock(editor: Editor, $from: ResolvedPos, depth: number, drop?: { from: number; to: number }): boolean {
  const { state } = editor
  const box = $from.node(depth)
  const boxPos = $from.before(depth)
  const tr = state.tr
  let end = boxPos + box.nodeSize
  if (drop) {
    tr.delete(drop.from, drop.to)
    end -= drop.to - drop.from
  }
  tr.insert(end, state.schema.nodes.paragraph.create())
  tr.setSelection(TextSelection.create(tr.doc, end + 1))
  editor.view.dispatch(tr.scrollIntoView())
  return true
}

function fieldEnter(editor: Editor): boolean {
  const { state } = editor
  const { $from, empty } = state.selection
  const field = FIELDS[$from.parent.type.name]
  if (!field || !empty) return false
  const item = $from.node(-1)
  const itemPos = $from.before(-1)
  // An empty item, from any of its fields: done with the block.
  if (!item.textContent.trim()) {
    const box = $from.node(-2)
    if (box.childCount === 1) {
      const boxPos = $from.before(-2)
      const tr = state.tr.replaceWith(boxPos, boxPos + box.nodeSize, state.schema.nodes.paragraph.create())
      editor.view.dispatch(tr.setSelection(TextSelection.create(tr.doc, boxPos + 1)))
      return true
    }
    return leaveBlock(editor, $from, $from.depth - 2, { from: itemPos, to: itemPos + item.nodeSize })
  }
  if (!field.last) {
    editor.view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, $from.after() + 1)))
    return true
  }
  const at = itemPos + item.nodeSize
  const tr = state.tr.insert(at, emptyItem(state.schema, field.item))
  editor.view.dispatch(tr.setSelection(TextSelection.create(tr.doc, at + 2)).scrollIntoView())
  return true
}

function fieldBackspace(editor: Editor): boolean {
  const { state } = editor
  const { $from, empty } = state.selection
  const field = FIELDS[$from.parent.type.name]
  if (!field || !empty || $from.parentOffset !== 0) return false
  if (field.index > 0) {
    editor.view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, $from.before() - 1)))
    return true
  }
  const item = $from.node(-1)
  const itemPos = $from.before(-1)
  const box = $from.node(-2)
  const boxPos = $from.before(-2)
  const index = $from.index(-2)
  if (item.textContent.trim()) {
    // Into the end of the item before; at the first item, nothing to do.
    if (index > 0) editor.view.dispatch(state.tr.setSelection(TextSelection.create(state.doc, itemPos - 2)))
    return true
  }
  if (box.childCount === 1) {
    const tr = state.tr.replaceWith(boxPos, boxPos + box.nodeSize, state.schema.nodes.paragraph.create())
    editor.view.dispatch(tr.setSelection(TextSelection.create(tr.doc, boxPos + 1)))
    return true
  }
  const tr = state.tr.delete(itemPos, itemPos + item.nodeSize)
  tr.setSelection(TextSelection.near(tr.doc.resolve(index > 0 ? itemPos - 2 : itemPos + 2), index > 0 ? -1 : 1))
  editor.view.dispatch(tr)
  return true
}

/** Enter on an empty last paragraph of a callout, a column or a fold leaves it. */
function containerEnter(editor: Editor): boolean {
  const { $from, empty } = editor.state.selection
  if (!empty || $from.parent.type.name !== 'paragraph' || $from.parent.content.size) return false
  const depth = $from.depth - 1
  if (depth < 1) return false
  const box = $from.node(depth)
  if (!['callout', 'detailsContent'].includes(box.type.name)) return false
  if ($from.index(depth) !== box.childCount - 1 || box.childCount < 2) return false
  const outer = box.type.name === 'detailsContent' ? depth - 1 : depth
  return leaveBlock(editor, $from, outer, { from: $from.before(), to: $from.after() })
}

/** Enter in a fold's title goes into its content. */
function summaryEnter(editor: Editor): boolean {
  const { $from, empty } = editor.state.selection
  if (!empty || $from.parent.type.name !== 'detailsSummary') return false
  editor.view.dispatch(editor.state.tr.setSelection(TextSelection.create(editor.state.doc, $from.after() + 2)))
  return true
}

/** Which item the caret is in: an empty optional field stays visible there. */
function editingItem(selection: { $from: ResolvedPos }): number {
  const { $from } = selection
  for (let d = $from.depth; d > 0; d--) {
    if (FIELDS[$from.node(d).firstChild?.type.name ?? '']) return $from.before(d)
  }
  return -1
}

function fieldDecorations(doc: PMNode, selection: { $from: ResolvedPos }): DecorationSet {
  const out: Decoration[] = []
  const editing = editingItem(selection)
  doc.descendants((node, pos) => {
    const field = FIELDS[node.type.name]
    if (field) {
      const empty = node.content.size === 0
      const itemPos = doc.resolve(pos).before()
      if (empty && OPTIONAL.has(node.type.name) && itemPos !== editing) {
        out.push(Decoration.node(pos, pos + node.nodeSize, { class: 'doc-field--blank' }))
      } else if (empty) {
        out.push(
          Decoration.node(pos, pos + node.nodeSize, {
            class: 'doc-field--empty',
            'data-placeholder': t(`work.room.doc.blocks.fields.${field.placeholder}`),
          })
        )
      }
      if (node.type.name === 'statDelta' && !empty) {
        const trend = trendOf(node.textContent)
        if (trend) out.push(Decoration.node(pos, pos + node.nodeSize, { 'data-trend': trend }))
      }
      return false
    }
    if (node.type.name === 'detailsSummary' && node.content.size === 0) {
      out.push(
        Decoration.node(pos, pos + node.nodeSize, {
          class: 'doc-field--empty',
          'data-placeholder': t('work.room.doc.blocks.fields.summary'),
        })
      )
    }
    return true
  })
  return DecorationSet.create(doc, out)
}

const STATUS_INPUT = /\{([✓✗!vVxX])\s+([^{}]{1,40})\}$/
const KIND_OF: Record<string, StatusKind> = { '✓': 'ok', v: 'ok', V: 'ok', '✗': 'no', x: 'no', X: 'no', '!': 'warn' }

export const BlockEditing = Extension.create({
  name: 'docBlockEditing',
  priority: 1000,
  addKeyboardShortcuts() {
    return {
      // While the slash menu is open, Enter picks from it.
      Enter: ({ editor }) =>
        !slashPluginKey.getState(editor.state)?.active &&
        (fieldEnter(editor) || summaryEnter(editor) || containerEnter(editor)),
      Backspace: ({ editor }) => fieldBackspace(editor),
      'Mod-Shift-1': ({ editor }) => setStatus(editor, 'ok'),
      'Mod-Shift-2': ({ editor }) => setStatus(editor, 'no'),
      'Mod-Shift-3': ({ editor }) => setStatus(editor, 'warn'),
    }
  },
  addInputRules() {
    return [
      // `{v 文字}` (or ✓ ✗ ! x) becomes a status tag as the brace closes.
      new InputRule({
        find: STATUS_INPUT,
        handler: ({ state, range, match }) => {
          const type = state.schema.marks.status
          if (!type) return
          state.tr.replaceWith(
            range.from,
            range.to,
            state.schema.text(match[2].trim(), [type.create({ kind: KIND_OF[match[1]] })])
          )
        },
      }),
    ]
  },
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: new PluginKey('docBlockFields'),
        props: { decorations: (state) => fieldDecorations(state.doc, state.selection) },
      }),
      // A table selected whole (Esc in a cell selects every cell, which is how
      // the table plugin shows a selected table): Backspace takes the table, not
      // just its words. Ahead of the table plugin, which would only clear them.
      new Plugin({
        key: new PluginKey('docWholeTable'),
        props: {
          handleKeyDown(view, event) {
            if (event.key !== 'Backspace' && event.key !== 'Delete') return false
            const { selection } = view.state
            if (!(selection instanceof CellSelection) || !selection.isRowSelection() || !selection.isColSelection())
              return false
            return deleteTable(view.state, view.dispatch)
          },
        },
      }),
      // A click under the last block puts the caret on a line there. After a
      // chart, a table or another block with its own structure there is no such
      // line (a shared document keeps no empty paragraph after its last block),
      // so the click makes one.
      new Plugin({
        key: new PluginKey('docBlockBelow'),
        props: {
          handleDOMEvents: {
            mousedown(view, event) {
              if (!view.editable || event.button !== 0 || event.target !== view.dom) return false
              const { doc } = view.state
              const last = doc.lastChild
              if (!last || convertsInPlace(last)) return false
              const dom = view.nodeDOM(doc.content.size - last.nodeSize)
              if (!(dom instanceof HTMLElement) || event.clientY <= dom.getBoundingClientRect().bottom) return false
              event.preventDefault()
              const tr = view.state.tr.insert(doc.content.size, view.state.schema.nodes.paragraph.create())
              tr.setSelection(TextSelection.create(tr.doc, tr.doc.content.size - 1))
              view.dispatch(tr.scrollIntoView())
              view.focus()
              return true
            },
          },
        },
      }),
      // A click beside a line that ends in a formula or a footnote lands, in
      // Chrome, outside the line; put the caret where ProseMirror's own hit
      // test says the click was.
      new Plugin({
        key: new PluginKey('docBlockClick'),
        props: {
          handleClick(view, pos, event) {
            const $pos = view.state.doc.resolve(pos)
            if (event.shiftKey || !$pos.parent.isTextblock || view.state.selection.from === pos) return false
            let atom = false
            $pos.parent.forEach((child) => {
              if (child.isAtom && child.isInline) atom = true
            })
            if (!atom) return false
            view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, pos)))
            return false
          },
        },
      }),
    ]
  },
})

// ---- Between two blocks, and a block as a whole.
//
// Two blocks with their own structure (a chart, a table, a timeline) can sit
// next to each other with no line between them, and a shared document keeps
// no empty paragraph there. ProseMirror's gap cursor is the way in: a click in
// the gap puts it there, and typing starts a line. Hovering the gap draws a
// line across it, so the gap reads as a place to click.
//
// Esc selects the whole block the caret is in (Notion's gesture); Backspace
// then removes it, Enter opens a line after it. Lower priority than the menus
// that close on Esc (slash, @, #), so they get it first.

/** Where the pointer's gap is: the position before the block under it, and the
 *  gap's middle in page coordinates. Null when the pointer is not in such a gap. */
function gapAt(view: EditorView, y: number): { pos: number; mid: number } | null {
  const { doc } = view.state
  let prev: { node: PMNode; bottom: number } | null = null
  let found: { pos: number; mid: number } | null = null
  doc.forEach((node, offset) => {
    if (found) return
    const dom = view.nodeDOM(offset)
    if (!(dom instanceof HTMLElement)) return
    const rect = dom.getBoundingClientRect()
    if (y < rect.top) {
      const structured = !convertsInPlace(node) && (!prev || !convertsInPlace(prev.node))
      const top = prev ? prev.bottom : rect.top - 16
      if (structured && y >= top) found = { pos: offset, mid: (top + rect.top) / 2 }
      prev = { node, bottom: rect.bottom }
      return
    }
    prev = { node, bottom: rect.bottom }
  })
  return found
}

const BlockGap = new Plugin({
  key: new PluginKey('docBlockGap'),
  view(view) {
    const line = document.createElement('div')
    line.className = 'doc-gap-hint'
    line.setAttribute('aria-hidden', 'true')
    view.dom.parentElement?.append(line)
    let shown = false
    const hide = () => {
      if (!shown) return
      shown = false
      line.classList.remove('is-shown')
    }
    const move = (e: MouseEvent) => {
      const gap = view.editable && e.target === view.dom ? gapAt(view, e.clientY) : null
      if (!gap) return hide()
      const host = line.offsetParent ?? view.dom.parentElement
      const base = host?.getBoundingClientRect()
      const prose = view.dom.getBoundingClientRect()
      if (!base) return hide()
      line.style.top = `${gap.mid - base.top}px`
      line.style.left = `${prose.left - base.left}px`
      line.style.width = `${prose.width}px`
      shown = true
      line.classList.add('is-shown')
    }
    view.dom.addEventListener('mousemove', move)
    view.dom.addEventListener('mouseleave', hide)
    view.dom.addEventListener('mousedown', hide)
    return {
      destroy() {
        view.dom.removeEventListener('mousemove', move)
        view.dom.removeEventListener('mouseleave', hide)
        view.dom.removeEventListener('mousedown', hide)
        line.remove()
      },
    }
  },
})

export const BlockSelect = Extension.create({
  name: 'docBlockSelect',
  priority: 50,
  addProseMirrorPlugins() {
    return [
      BlockGap,
      new Plugin({
        key: new PluginKey('docBlockSelect'),
        props: {
          handleKeyDown(view, event) {
            if (event.key !== 'Escape' || event.defaultPrevented || event.isComposing || !view.editable) return false
            const { doc } = view.state
            const { $from } = view.state.selection
            // Already the whole block (or a gap between blocks): nothing larger to select.
            if ($from.depth < 1) return false
            view.dispatch(view.state.tr.setSelection(NodeSelection.create(doc, $from.before(1))))
            return true
          },
        },
      }),
    ]
  },
})
