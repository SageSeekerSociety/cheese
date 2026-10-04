// How a table fits a phone while it is read. A table of words becomes one card
// per row, each value under its column's name; a table of numbers keeps its
// columns and scrolls sideways with the first column held in place. Which is
// which is read from the cells, so nobody has to choose.
//
// This only labels the table and its cells (./shapes.ts reads which shape);
// the stylesheet does the rest, and only for a reader on a narrow screen.
// Editing keeps the plain grid.
import type { Node as PMNode } from '@tiptap/pm/model'

import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'

import { tableFit } from './shapes'

function tableDecorations(doc: PMNode): DecorationSet {
  const out: Decoration[] = []
  doc.descendants((node, pos) => {
    if (node.type.name !== 'table') return true
    const { shape, labels } = tableFit(node)
    out.push(Decoration.node(pos, pos + node.nodeSize, { 'data-shape': shape }))
    if (shape === 'numbers') return false
    node.forEach((row, rowOffset, rowIndex) => {
      if (rowIndex === 0) return
      const rowPos = pos + 1 + rowOffset
      row.forEach((cell, cellOffset, col) => {
        const at = rowPos + 1 + cellOffset
        out.push(Decoration.node(at, at + cell.nodeSize, { 'data-label': labels[col] ?? '' }))
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
