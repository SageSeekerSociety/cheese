import type { Editor } from '@tiptap/core'
import type { Mark, Node as PMNode } from '@tiptap/pm/model'

import { getMarkRange } from '@tiptap/core'

export interface DocLinkTarget {
  editor: Editor
  doc: PMNode
  from: number
  to: number
  mark: Mark | null
  href: string
}
export function captureDocLink(editor: Editor, position = editor.state.selection.from): DocLinkTarget | null {
  const type = editor.schema.marks.link
  const range = type && getMarkRange(editor.state.doc.resolve(position), type)
  if (!range) return null
  let mark: Mark | undefined
  editor.state.doc.nodesBetween(range.from, range.to, (node) => {
    mark ??= type.isInSet(node.marks)
  })
  return mark ? { editor, doc: editor.state.doc, ...range, mark, href: String(mark.attrs.href) } : null
}
export function captureNewDocLink(editor: Editor): DocLinkTarget | null {
  const { from, to, empty, $from, $to } = editor.state.selection
  if (empty || !$from.sameParent($to) || !$from.parent.isTextblock) return null
  return { editor, doc: editor.state.doc, from, to, mark: null, href: '' }
}
export function safeDocHref(input: string): string | null {
  const value = input.trim()
  if (!value || /[\u0000- \u007f]/.test(value)) return null
  if (/^(?:https?:\/\/|mailto:)/i.test(value)) {
    try {
      const url = new URL(value)
      if (url.protocol !== 'mailto:' && !url.hostname) return null
      return value
    } catch {
      return null
    }
  }
  // Relative and internal links remain relative. Protocol-relative, scripts and
  // unrecognised schemes must never become navigable from the callout.
  return /^(?:\/(?!\/)|\.\.?\/|#)/.test(value) ? value : null
}
export function applyDocLink(target: DocLinkTarget, input: string | null): 'ok' | 'stale' | 'invalid' | 'readonly' {
  const { editor, doc, from, to, mark } = target
  if (editor.isDestroyed || editor.state.doc !== doc) return 'stale'
  if (!editor.isEditable) return 'readonly'
  const type = editor.schema.marks.link
  if (!type) return 'invalid'
  if (mark) {
    const current = captureDocLink(editor, from)
    if (!current || current.from !== from || current.to !== to || !current.mark?.eq(mark)) return 'stale'
  }
  const href = input === null ? null : safeDocHref(input)
  if (input !== null && !href) return 'invalid'
  const transaction = editor.state.tr.removeMark(from, to, type)
  if (href) transaction.addMark(from, to, type.create({ ...(mark?.attrs ?? {}), href }))
  // One dispatched transaction means one atomic edit and one undo item. The
  // transaction keeps the current selection instead of replacing it with target.
  editor.view.dispatch(transaction)
  return 'ok'
}
