// The document's text as a reader sees it, and where each character of it sits
// in the editor — so a change described in Markdown (what the AI teammate and
// the service speak) can be found again in the document on screen.
//
// Markdown is how a change is sent and stored; the editor holds a tree. A
// change is found by its plain text, which both sides agree on, rather than by
// Markdown offsets, which only the serializer could reproduce.

import type { Node as PMNode } from '@tiptap/pm/model'

import { parseMarkdown } from './docSchema'

import { t } from '@/i18n'

/** One replacement in the document's Markdown: `old` becomes `new`. */
export interface DocEdit {
  old: string
  new: string
}

/** What to tell the person when an edit or a rewrite did not go through. The
 *  service explains a refused edit (the text moved, it occurs twice…) in a
 *  sentence of its own, which is shown as it is. */
export function editFailure(error: unknown): string {
  const status = (error as { status?: unknown } | null)?.status
  const message = error instanceof Error ? error.message : ''
  if (typeof status === 'number' && status >= 400 && status < 500) {
    return message || (status === 409 ? t('work.room.docEdit.stale') : t('work.room.docEdit.failedRetry'))
  }
  return message ? t('work.room.docEdit.failed', { reason: message }) : t('work.room.docEdit.failedRetry')
}

export interface FlatText {
  /** Text blocks joined by `\n`; an inline object counts as one character. */
  text: string
  /** `pos[i]` is the document position of `text[i]`. */
  pos: number[]
}

/** The document's plain text, with the position of every character in it. */
export function flatText(doc: PMNode): FlatText {
  let text = ''
  const pos: number[] = []
  doc.descendants((node, at) => {
    if (!node.isTextblock) return true
    if (text) {
      text += '\n'
      pos.push(at)
    }
    node.forEach((child, offset) => {
      const start = at + 1 + offset
      if (child.isText) {
        const value = child.text ?? ''
        for (let i = 0; i < value.length; i++) pos.push(start + i)
        text += value
      } else {
        text += '￼'
        pos.push(start)
      }
    })
    return false
  })
  return { text, pos }
}

/** How a piece of Markdown reads: its plain text, blocks joined by `\n`. */
export function plainOf(markdown: string): string {
  if (!markdown.trim()) return ''
  return flatText(parseMarkdown(markdown)).text
}

/** The positions of `text[from, to)` in the document. */
export function rangeOf(flat: FlatText, from: number, to: number): { from: number; to: number } {
  if (to <= from) {
    const at = from < flat.pos.length ? flat.pos[from] : flat.pos.length ? flat.pos[flat.pos.length - 1] + 1 : 0
    return { from: at, to: at }
  }
  return { from: flat.pos[from], to: flat.pos[to - 1] + 1 }
}

/** Every place `needle` occurs in the text, as character offsets. */
export function occurrences(haystack: string, needle: string): number[] {
  const found: number[] = []
  if (!needle) return found
  for (let at = haystack.indexOf(needle); at >= 0; at = haystack.indexOf(needle, at + 1)) found.push(at)
  return found
}
