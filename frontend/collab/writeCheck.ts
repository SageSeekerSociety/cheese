// Whether a Markdown write would lose visible text on its way into the live
// document.
//
// The live document is blocks; Markdown is the language 芝士 (and anything else
// writing through PUT /doc) reads and writes it in. Converting Markdown into
// blocks may respell it — spacing, escapes, list markers, table padding — and
// that is fine. What is not fine is text that a reader of the Markdown would
// see and the document would not have, dropped without anyone noticing. So a
// write is checked at the moment it is made, and refused with what to change.
//
// The test is about text, not syntax: a construct the editor keeps as literal
// text (an HTML tag, `$x^2$`) passes, because its characters are still there.
// Each top-level block is converted on its own and its letters and digits are
// counted on both sides; the document side counts the text and the attributes
// that carry text (a link's address, an image's path and alt, a code block's
// language, a formula, a footnote's label), so `[文字](地址)` loses nothing.
// What only names a block (`> [!NOTE]`, `:::timeline`, `<details>`) is
// structure, like a list marker.
//
// Counting characters cannot see a block written in the wrong shape: a
// timeline item without its `|` keeps every letter and still reads wrong. So
// the blocks are also checked for shape, and the message gives the right form.

import type { Node as PMNode } from '@tiptap/pm/model'
import type { Tokens } from 'marked'

import { docMarked, parseMarkdown } from '../src/lib/docSchema'

import { blockProblem, footnoteProblem } from './blockCheck'

export interface WriteProblem {
  /** 1-based line in the written Markdown. */
  line: number
  message: string
}

const VISIBLE = /[\p{L}\p{N}]/gu
// What only spells structure, line by line: quote markers, list and task
// markers. The digits of an ordered list are its numbering, not its text.
const LINE_SYNTAX = /^(?:\s*>)*\s*(?:\[![A-Za-z]+\]|(?:[-*+]|\d{1,9}[.)])\s+(?:\[[ xX]\]\s+)?)?/
// A container's opening and closing lines, and a fold's tags.
const BLOCK_SYNTAX = /^\s*:{3,}[a-z]*\s*$|<\/?(?:details|summary)(?:\s+open)?>/g
// An entity is one character on screen, whatever letters spell it.
const ENTITY = /&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]*);/gi
// Attributes whose value a reader can see or follow.
const TEXT_ATTRS = ['href', 'src', 'alt', 'title', 'language', 'source', 'latex', 'label']

function count(text: string, into = new Map<string, number>()): Map<string, number> {
  for (const char of text.match(VISIBLE) ?? []) into.set(char, (into.get(char) ?? 0) + 1)
  return into
}

function sourceChars(raw: string): Map<string, number> {
  const counts = new Map<string, number>()
  for (const line of raw.split('\n')) {
    count(line.replace(LINE_SYNTAX, '').replace(BLOCK_SYNTAX, '').replace(ENTITY, ' '), counts)
  }
  return counts
}

function documentChars(node: PMNode): Map<string, number> {
  const counts = new Map<string, number>()
  node.descendants((child) => {
    if (child.isText) count(child.text ?? '', counts)
    for (const key of TEXT_ATTRS) {
      const value = child.attrs?.[key]
      if (typeof value === 'string') count(value, counts)
    }
    for (const mark of child.marks) {
      for (const key of TEXT_ATTRS) {
        const value = mark.attrs?.[key]
        if (typeof value === 'string') count(value, counts)
      }
    }
    return true
  })
  return counts
}

/** Characters the source has more of than the document. */
function lost(source: Map<string, number>, kept: Map<string, number>): Map<string, number> {
  const out = new Map<string, number>()
  for (const [char, n] of source) {
    const missing = n - (kept.get(char) ?? 0)
    if (missing > 0) out.set(char, missing)
  }
  return out
}

function cells(row: string): number {
  return row
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split(/(?<!\\)\|/).length
}

function preview(text: string): string {
  const flat = text.trim().replace(/\s+/g, ' ')
  return flat.length > 40 ? `${flat.slice(0, 40)}…` : flat
}

function definitionProblem(token: Tokens.Def, line: number): WriteProblem {
  return {
    line,
    message: `第 ${line} 行是链接引用定义（${preview(token.raw)}），实况文档不支持，这一行会被整行丢掉。请把链接直接写成 [文字](地址)。`,
  }
}

function tableProblem(token: Tokens.Table, line: number): WriteProblem | null {
  const width = token.header.length
  const rows = token.raw.split('\n')
  for (const [i, row] of rows.entries()) {
    if (i < 2 || !row.trim()) continue
    const n = cells(row)
    if (n > width) {
      return {
        line: line + i,
        message: `第 ${line + i} 行表格这一行有 ${n} 格，表头只有 ${width} 格，多出的格子会被丢掉。请让每一行的格数和表头一致。`,
      }
    }
  }
  return null
}

/** The first place a Markdown write would lose visible text, or null. */
export function checkMarkdownWrite(markdown: string): WriteProblem | null {
  let offset = 0
  for (const token of docMarked.lexer(markdown)) {
    const line = markdown.slice(0, offset).split('\n').length
    offset += token.raw.length
    if (token.type === 'space') continue
    if (token.type === 'def') return definitionProblem(token as Tokens.Def, line + leadingBlankLines(token.raw))
    const shape = blockProblem(token, line + leadingBlankLines(token.raw))
    if (shape) return shape
    const missing = lost(sourceChars(token.raw), documentChars(parseMarkdown(token.raw)))
    if (missing.size === 0) continue
    if (token.type === 'table') {
      const problem = tableProblem(token as Tokens.Table, line + leadingBlankLines(token.raw))
      if (problem) return problem
    }
    const lines = token.raw.split('\n')
    const at = Math.max(
      0,
      lines.findIndex((text) => [...(text.match(VISIBLE) ?? [])].some((char) => missing.has(char)))
    )
    const gone = [...missing.keys()].join('')
    return {
      line: line + at,
      message: `第 ${line + at} 行（${preview(lines[at])}）有文字写不进实况文档：「${gone}」会被丢掉。这种写法不受支持，请改用实况文档支持的写法。`,
    }
  }
  return footnoteProblem(markdown)
}

function leadingBlankLines(raw: string): number {
  return raw.length - raw.replace(/^\n+/, '').length
}
