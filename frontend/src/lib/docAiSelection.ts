import type { Editor } from '@tiptap/core'
import type { Node as PMNode } from '@tiptap/pm/model'
import type { DocAiSelection, DocAiSource } from './docAiTypes'

import { TextSelection } from '@tiptap/pm/state'
import { marked } from 'marked'

export interface DocSelectionSnapshot {
  editor: Editor
  doc: PMNode
  from: number
  to: number
}
interface Unit {
  text: string
  start: number
  end: number
}
interface Line {
  text: string
  offsets: number[]
}

/** Token raw slices carry provenance. No rendered quote is searched in source. */
function inline(line: Line): Unit[] | null {
  const units: Unit[] = []
  const append = (text: string, start: number, end: number) => {
    units.push({ text, start: line.offsets[start], end: line.offsets[end] })
  }
  const visit = (raw: string, at: number): boolean => {
    let cursor = at
    for (const token of marked.Lexer.lexInline(raw, { gfm: true })) {
      if (line.text.slice(cursor, cursor + token.raw.length) !== token.raw) return false
      if (token.type === 'text' && token.raw === token.text) {
        for (let i = 0; i < token.raw.length; ) {
          const entity = token.raw.slice(i).match(/^&(?:#\d+|#x[\da-f]+|[a-z]+);/i)
          if (entity) {
            const textarea = document.createElement('textarea')
            textarea.innerHTML = entity[0]
            append(textarea.value, cursor + i, cursor + i + entity[0].length)
            i += entity[0].length
          } else {
            const char = String.fromCodePoint(token.raw.codePointAt(i)!)
            append(char, cursor + i, cursor + i + char.length)
            i += char.length
          }
        }
      } else if (token.type === 'escape') {
        append(token.text, cursor, cursor + token.raw.length)
      } else if (token.type === 'strong' || token.type === 'em' || token.type === 'del') {
        const children = token.tokens.map((child) => child.raw).join('')
        const edge = (token.raw.length - children.length) / 2
        if (!Number.isInteger(edge) || token.raw.slice(edge, -edge) !== children || !visit(children, cursor + edge))
          return false
      } else if (token.type === 'link') {
        const children = token.tokens.map((child) => child.raw).join('')
        if (!token.raw.startsWith(`[${children}](`) || !visit(children, cursor + 1)) return false
      } else if (token.type === 'codespan') {
        const ticks = token.raw.match(/^`+/)?.[0].length ?? 0
        const body = token.raw.slice(ticks, -ticks)
        if (body !== token.text) return false
        for (let i = 0; i < body.length; ) {
          const char = String.fromCodePoint(body.codePointAt(i)!)
          append(char, cursor + ticks + i, cursor + ticks + i + char.length)
          i += char.length
        }
      } else return false
      cursor += token.raw.length
    }
    return cursor === at + raw.length
  }
  return visit(line.text, 0) ? units : null
}

/** Supported raw line grammar is deliberately closed. Unsupported HTML, fences,
 * tables and transformed continuation indentation require a new provenance rule. */
function sourceBlocks(source: string, prefix: number): Unit[][] | null {
  const blocks: Unit[][] = []
  let paragraph: Line | null = null
  const flush = () => {
    if (!paragraph) return true
    const units = inline(paragraph)
    paragraph = null
    if (!units) return false
    blocks.push(units)
    return true
  }
  let offset = prefix
  for (const match of source.slice(prefix).matchAll(/([^\r\n]*)(\r\n|\r|\n|$)/g)) {
    const [whole, raw, newline] = match
    if (!whole) continue
    if (!raw.trim()) {
      if (!flush()) return null
      offset += whole.length
      continue
    }
    if (/^\s*(?:```|~~~|\||<!--|<|[-*_]{3,}\s*$)/.test(raw) || /\|/.test(raw)) return null
    const leader = raw.match(/^(?: {0,3}> ?)*(?: {0,3}#{1,6} |\s*(?:[-+*]|\d+[.)]) (?:\[[ xX]\] )?)?/u)?.[0] ?? ''
    if (/^\s{4}/.test(raw) && !leader.trim()) return null
    const text = raw.slice(leader.length)
    const line = { text, offsets: Array.from({ length: text.length + 1 }, (_, i) => offset + leader.length + i) }
    if (leader.trim()) {
      if (!flush()) return null
      const units = inline(line)
      if (!units) return null
      blocks.push(units)
    } else if (paragraph) {
      paragraph.text += `\n${text}`
      paragraph.offsets.push(...line.offsets)
    } else paragraph = line
    offset += raw.length + newline.length
  }
  return flush() ? blocks : null
}

export function captureDocSelection(editor: Editor): DocSelectionSnapshot | null {
  const selection = editor.state.selection
  if (!(selection instanceof TextSelection) || selection.empty) return null
  return { editor, doc: editor.state.doc, from: selection.from, to: selection.to }
}

export async function validateDocSelection(
  snapshot: DocSelectionSnapshot,
  canonical: DocAiSource,
  raw: string,
  version: number,
  titlePrefix: string
): Promise<DocAiSelection | null> {
  const { editor, doc, from, to } = snapshot
  if (
    editor.isDestroyed ||
    editor.state.doc !== doc ||
    canonical.source !== raw ||
    canonical.base_version !== version ||
    canonical.offset_unit !== 'utf8-bytes' ||
    !raw.startsWith(titlePrefix)
  )
    return null
  const parsed = editor.markdown?.parse(raw.slice(titlePrefix.length))
  if (!parsed) return null
  const canonicalDoc = editor.schema.nodeFromJSON(parsed)
  // StarterKit appends one empty caret paragraph after a terminal list/table.
  const trailingCaret =
    doc.lastChild?.type.name === 'paragraph' &&
    doc.lastChild.content.size === 0 &&
    doc.childCount === canonicalDoc.childCount + 1 &&
    doc.content.cut(0, doc.content.size - doc.lastChild.nodeSize).eq(canonicalDoc.content)
  if (!canonicalDoc.eq(doc) && !trailingCaret) return null
  const blocks = sourceBlocks(raw, titlePrefix.length)
  if (!blocks) return null
  const textblocks: { node: PMNode; pos: number }[] = []
  doc.descendants((node, pos) => {
    if (node.isTextblock) {
      textblocks.push({ node, pos })
      return false
    }
  })
  if (trailingCaret) textblocks.pop()
  if (blocks.length !== textblocks.length) return null
  let start: number | undefined
  let end: number | undefined
  for (let i = 0; i < blocks.length; i++) {
    const { node, pos } = textblocks[i]
    const units = blocks[i]
    if (
      node.textContent !== units.map((unit) => unit.text).join('') ||
      node.content.content.some((child) => !child.isText)
    )
      return null
    let pm = pos + 1
    for (const unit of units) {
      if (pm === from) start = unit.start
      pm += unit.text.length
      if (pm === to) end = unit.end
    }
  }
  if (start === undefined || end === undefined || start >= end) return null
  const bytes = new TextEncoder()
  const byteStart = bytes.encode(raw.slice(0, start)).length
  const byteEnd = bytes.encode(raw.slice(0, end)).length
  const node = canonical.nodes.find((span) => span.start <= byteStart && byteEnd <= span.end)
  if (!node) return null
  const digest = await crypto.subtle.digest('SHA-256', bytes.encode(raw.slice(start, end)))
  if (editor.isDestroyed || editor.state.doc !== doc) return null
  return {
    node_id: node.node_id,
    start: byteStart,
    end: byteEnd,
    exact_hash: Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join(''),
  }
}
