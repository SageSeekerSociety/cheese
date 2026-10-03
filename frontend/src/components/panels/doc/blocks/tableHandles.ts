// Row and column handles on a table while it is edited: ⋮ beside the row and
// ⋯ above the column under the pointer (or the caret, on a phone), and a ＋
// strip under and beside the table to add a row or a column at the end.
//
// The handles sit on <body>, outside the editable DOM, so ProseMirror never
// sees them; they follow the cell when the pointer or the caret moves and hide
// when the page scrolls. A chart's table keeps its first column (the
// categories) and at least one series; any table keeps its header row, which
// Markdown cannot do without.
import type { Editor } from '@tiptap/core'
import type { EditorView } from '@tiptap/pm/view'

import { Extension } from '@tiptap/core'
import { Plugin, PluginKey } from '@tiptap/pm/state'

import { controlButton, menuAt, type PopoverItem } from './popover'

import { t } from '@/i18n'

type Command = 'addRowBefore' | 'addRowAfter' | 'deleteRow' | 'addColumnBefore' | 'addColumnAfter' | 'deleteColumn'

class Handles {
  private row = controlButton('doc-th doc-th--row', t('work.room.doc.blocks.rowActions'), '⋮')
  private col = controlButton('doc-th doc-th--col', t('work.room.doc.blocks.columnActionsTable'), '⋯')
  private addRow = controlButton('doc-th-add doc-th-add--row', t('work.room.doc.blocks.addRowEnd'), '＋')
  private addCol = controlButton('doc-th-add doc-th-add--col', t('work.room.doc.blocks.addColumnEnd'), '＋')
  private cell: HTMLTableCellElement | null = null

  constructor(
    private editor: Editor,
    private view: EditorView
  ) {
    for (const el of [this.row, this.col, this.addRow, this.addCol]) {
      el.hidden = true
      document.body.append(el)
    }
    this.row.addEventListener('click', () => this.rowMenu())
    this.col.addEventListener('click', () => this.columnMenu())
    this.addRow.addEventListener('click', () => this.append('row'))
    this.addCol.addEventListener('click', () => this.append('column'))
    view.dom.addEventListener('mousemove', this.onMove)
    view.dom.addEventListener('mouseleave', this.onLeave)
    document.addEventListener('scroll', this.hide, true)
    window.addEventListener('resize', this.hide)
  }

  private onMove = (e: MouseEvent) => {
    const cell = (e.target as HTMLElement).closest?.('td, th') as HTMLTableCellElement | null
    if (cell && this.view.dom.contains(cell)) this.show(cell)
  }

  private onLeave = (e: MouseEvent) => {
    const to = e.relatedTarget as Node | null
    if (to && [this.row, this.col, this.addRow, this.addCol].some((el) => el.contains(to))) return
    if (!this.caretCell()) this.hide()
  }

  private caretCell(): HTMLTableCellElement | null {
    if (!this.editor.isFocused) return null
    const { node } = this.view.domAtPos(this.view.state.selection.from)
    const el = node.nodeType === 1 ? (node as HTMLElement) : node.parentElement
    return (el?.closest('td, th') as HTMLTableCellElement | null) ?? null
  }

  /** Called when the document or the selection changes. */
  update() {
    if (!this.editor.isEditable) return this.hide()
    const caret = this.caretCell()
    if (caret) this.show(caret)
    else if (this.cell && !this.cell.isConnected) this.hide()
  }

  hide = () => {
    if ([this.row, this.col].some((el) => el.getAttribute('aria-expanded') === 'true')) return
    for (const el of [this.row, this.col, this.addRow, this.addCol]) el.hidden = true
    this.cell = null
  }

  private show(cell: HTMLTableCellElement) {
    if (!this.editor.isEditable) return
    const table = cell.closest('table')
    if (!table) return
    this.cell = cell
    const r = cell.getBoundingClientRect()
    const box = table.getBoundingClientRect()
    const x = window.scrollX
    const y = window.scrollY
    const place = (el: HTMLElement, left: number, top: number, size?: { w?: number; h?: number }) => {
      el.hidden = false
      el.style.left = `${left + x}px`
      el.style.top = `${top + y}px`
      el.style.width = size?.w ? `${size.w}px` : ''
      el.style.height = size?.h ? `${size.h}px` : ''
    }
    place(this.row, box.left - 22, r.top + r.height / 2 - 10)
    place(this.col, r.left + r.width / 2 - 10, box.top - 22)
    place(this.addRow, box.left, box.bottom + 2, { w: box.width })
    place(this.addCol, box.right + 2, box.top, { h: box.height })
  }

  private shape() {
    const cell = this.cell
    const table = cell?.closest('table')
    if (!cell || !table) return null
    const row = (cell.parentElement as HTMLTableRowElement).rowIndex
    return {
      cell,
      table,
      row,
      col: cell.cellIndex,
      rows: table.rows.length,
      cols: table.rows[0]?.cells.length ?? 0,
      chart: Boolean(table.closest('[data-block="chart"]')),
    }
  }

  /** Where the caret goes in a cell: the start of its first paragraph. */
  private inside(cell: HTMLElement): number {
    return this.view.posAtDOM(cell.querySelector('p') ?? cell, 0)
  }

  private run(cell: HTMLElement, command: Command) {
    this.editor.chain().focus().setTextSelection(this.inside(cell))[command]().run()
  }

  private rowMenu() {
    const s = this.shape()
    if (!s) return
    const items: PopoverItem[] = []
    if (s.row > 0)
      items.push({ label: t('work.room.doc.blocks.rowAbove'), run: () => this.run(s.cell, 'addRowBefore') })
    items.push({ label: t('work.room.doc.blocks.rowBelow'), run: () => this.run(s.cell, 'addRowAfter') })
    if (s.row > 0 && s.rows > 2) {
      items.push({ label: t('work.room.doc.blocks.rowDelete'), run: () => this.run(s.cell, 'deleteRow') })
    }
    menuAt(this.row, items)
  }

  private columnMenu() {
    const s = this.shape()
    if (!s) return
    const items: PopoverItem[] = []
    if (!(s.chart && s.col === 0)) {
      items.push({ label: t('work.room.doc.blocks.columnLeft'), run: () => this.run(s.cell, 'addColumnBefore') })
    }
    items.push({ label: t('work.room.doc.blocks.columnRight'), run: () => this.run(s.cell, 'addColumnAfter') })
    if (s.chart ? s.col > 0 && s.cols > 2 : s.cols > 1) {
      items.push({ label: t('work.room.doc.blocks.columnDelete'), run: () => this.run(s.cell, 'deleteColumn') })
    }
    menuAt(this.col, items)
  }

  /** Add a row or a column at the end and put the caret in its first cell. */
  private append(what: 'row' | 'column') {
    const s = this.shape()
    if (!s) return
    const last = what === 'row' ? s.table.rows[s.rows - 1]?.cells[0] : s.table.rows[0]?.cells[s.cols - 1]
    if (!last) return
    this.run(last, what === 'row' ? 'addRowAfter' : 'addColumnAfter')
    const first = what === 'row' ? s.table.rows[s.rows]?.cells[0] : s.table.rows[0]?.cells[s.cols]
    if (first) this.editor.chain().focus().setTextSelection(this.inside(first)).run()
  }

  destroy() {
    this.view.dom.removeEventListener('mousemove', this.onMove)
    this.view.dom.removeEventListener('mouseleave', this.onLeave)
    document.removeEventListener('scroll', this.hide, true)
    window.removeEventListener('resize', this.hide)
    for (const el of [this.row, this.col, this.addRow, this.addCol]) el.remove()
  }
}

export const TableHandles = Extension.create({
  name: 'docTableHandles',
  addProseMirrorPlugins() {
    const editor = this.editor
    return [
      new Plugin({
        key: new PluginKey('docTableHandles'),
        view: (view) => new Handles(editor, view),
      }),
    ]
  },
})
