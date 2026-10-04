// How a table fits a phone while it is read. A table of words becomes one card
// per row, each value under its column's name; a table of numbers keeps its
// columns and scrolls sideways with the first column held in place. Which is
// which is read from the cells, so nobody has to choose.
//
// This only labels the table and its cells; the stylesheet does the rest, and
// only for a reader on a narrow screen. Editing keeps the plain grid.
import type { Node as PMNode } from '@tiptap/pm/model'

import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

// A number with an optional sign, currency and a short unit: 1,274 / $410 / 2.1% / 6.1 秒.
const NUMBER = /^[-+−]?[$¥€£]?\s?\d[\d.,]*\s?\S{0,3}$/

/** Whether the cells after the first column are mostly numbers. */
export function isNumberTable(table: PMNode): boolean {
  let numbers = 0
  let filled = 0
  table.forEach((row, _offset, rowIndex) => {
    if (rowIndex === 0) return
    row.forEach((cell, _o, col) => {
      const text = cell.textContent.trim()
      if (col === 0 || !text) return
      filled++
      if (NUMBER.test(text)) numbers++
    })
  })
  return filled > 0 && numbers / filled >= 0.6
}

function tableDecorations(doc: PMNode): DecorationSet {
  const out: Decoration[] = []
  doc.descendants((node, pos) => {
    if (node.type.name !== 'table') return true
    const numbers = isNumberTable(node)
    out.push(Decoration.node(pos, pos + node.nodeSize, { 'data-shape': numbers ? 'numbers' : 'cards' }))
    if (numbers) return false
    const header = node.firstChild
    const names: string[] = []
    header?.forEach((cell) => names.push(cell.textContent.trim()))
    node.forEach((row, rowOffset, rowIndex) => {
      if (rowIndex === 0) return
      const rowPos = pos + 1 + rowOffset
      row.forEach((cell, cellOffset, col) => {
        const at = rowPos + 1 + cellOffset
        out.push(Decoration.node(at, at + cell.nodeSize, { 'data-label': names[col] ?? '' }))
      })
    })
    return false
  })
  return DecorationSet.create(doc, out)
}

export const TableShape = Extension.create({
  name: 'docTableShape',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: new PluginKey('docTableShape'),
        state: {
          init: (_config, state) => tableDecorations(state.doc),
          apply: (tr, previous) => (tr.docChanged ? tableDecorations(tr.doc) : previous),
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
