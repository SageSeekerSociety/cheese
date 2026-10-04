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
import type { StatusKind } from '../../../../lib/docSchema/blocks'

import { Extension, InputRule } from '@tiptap/core'
import { Plugin, PluginKey, TextSelection } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

import { emptyItem } from '../../../../lib/docSchema/blocks'
import { slashPluginKey } from '../../../../lib/docSlashMenu'
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
