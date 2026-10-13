export interface MessageSelection {
  row: HTMLElement
  text: string
}

const ACTION_ROW = '[data-mid][data-actions]'
const NATIVE_INPUT = 'input, textarea, [contenteditable="true"]'

function elementOf(node: Node | null): Element | null {
  return node instanceof Element ? node : node?.parentElement ?? null
}

/** A selection belongs to a message only when both ends are in that one row.
 * Cross-message selections and selections in another pane keep their native menu. */
export function messageSelection(root: HTMLElement | null, editingId: string | null): MessageSelection | null {
  const selection = window.getSelection()
  if (!root || !selection || selection.isCollapsed || !selection.rangeCount) return null
  const range = selection.getRangeAt(0)
  const start = elementOf(range.startContainer)
  const end = elementOf(range.endContainer)
  if (!start || !end || start.closest(NATIVE_INPUT) || end.closest(NATIVE_INPUT)) return null
  const row = start.closest<HTMLElement>(ACTION_ROW)
  if (!row || !root.contains(row) || row !== end.closest(ACTION_ROW) || row.dataset.mid === editingId) return null
  const text = selection.toString()
  return text.trim() ? { row, text } : null
}
