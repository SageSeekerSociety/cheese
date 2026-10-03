// The blocks a document has beyond plain Markdown: callouts, status tags,
// timelines, stat cards, columns, folded sections, formulas and footnotes.
//
// Each one is written in a syntax a reader can follow when the Markdown is
// opened anywhere else (GitHub alerts, `:::` containers, `<details>`, `$…$`,
// `[^1]`), and each one is read and written here so that what the editor holds
// and what 芝士 reads are the same text. The serializer's output is the
// canonical spelling: the writing guide teaches exactly that spelling, and an
// edit has to quote it byte for byte.
//
// Part of the document schema module (see ./index.ts): nothing here touches
// the DOM or imports the app. The editor adds its own views on top
// (components/panels/doc/blocks/); without them every block still renders as
// plain, readable markup.

import type { JSONContent, MarkdownToken } from '@tiptap/core'

import { Mark, mergeAttributes, Node } from '@tiptap/core'

// ---------------------------------------------------------------------------
// Containers: `:::name` … `:::`
// ---------------------------------------------------------------------------

const OPENER = /^(:{3,})([a-z]+)[ \t]*(.*)$/
const CLOSER = /^(:{3,})[ \t]*$/

/** The container starting at the head of `src`: its colon count, name, the rest
 *  of the opening line, its body, and the raw text it spans. A nested container
 *  closes with its own colon count, so `::::columns` can hold `:::column`. */
export function readContainer(src: string): { name: string; info: string; body: string; raw: string } | null {
  const lines = src.split('\n')
  const head = OPENER.exec(lines[0])
  if (!head) return null
  const open = head[1].length
  const stack: number[] = []
  for (let i = 1; i < lines.length; i++) {
    const nested = OPENER.exec(lines[i])
    if (nested) {
      stack.push(nested[1].length)
      continue
    }
    const close = CLOSER.exec(lines[i])
    if (!close) continue
    const n = close[1].length
    if (stack.length && stack[stack.length - 1] === n) {
      stack.pop()
      continue
    }
    if (!stack.length && n === open) {
      const raw = lines.slice(0, i + 1).join('\n') + (i + 1 < lines.length ? '\n' : '')
      return { name: head[2], info: head[3].trim(), body: lines.slice(1, i).join('\n'), raw }
    }
  }
  return null
}

/** Where a `:::name` container could start, for marked's tokenizer `start`. */
function containerStart(name: string) {
  const re = new RegExp(`^:{3,}${name}\\b`, 'm')
  return (src: string) => src.search(re)
}

/** `- a | b | c` → ['a', 'b', 'c'] (the dash optional). Splits on the first
 *  separators only, so the last field keeps any `|` it has. */
function fieldsOf(line: string, count: number): string[] {
  const text = line.replace(/^\s*[-*+][ \t]+/, '')
  const parts = text.split(/[ \t]*\|[ \t]*/)
  const head = parts.slice(0, count - 1)
  const tail = parts.slice(count - 1).join(' | ')
  return [...head, tail].map((part) => part.trim())
}

const ITEM_LINE = /^[-*+][ \t]+/

// ---------------------------------------------------------------------------
// Fields: the short lines a timeline item or a stat card is made of
// ---------------------------------------------------------------------------

function field(name: string, role: string) {
  return Node.create({
    name,
    content: 'inline*',
    defining: true,
    parseHTML: () => [{ tag: `div[data-field="${role}"]` }],
    renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-field': role }), 0],
  })
}

function inline(helpers: { parseInline: (t: MarkdownToken[]) => JSONContent[] }, tokens: MarkdownToken[] | undefined) {
  return tokens?.length ? helpers.parseInline(tokens) : []
}

function fieldNode(type: string, content: JSONContent[]): JSONContent {
  return content.length ? { type, content } : { type }
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------
//
//   :::timeline
//   - 10:02 | 提交合并请求 {✓ 单元测试}
//     类型检查报了 2 处错误。
//   :::

interface TimelineEntry {
  when: MarkdownToken[]
  title: MarkdownToken[]
  body: MarkdownToken[]
}

export const Timeline = Node.create({
  name: 'timeline',
  group: 'block',
  content: 'timelineItem+',
  defining: true,
  isolating: true,
  parseHTML: () => [{ tag: 'div[data-block="timeline"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-block': 'timeline' }), 0],
  markdownTokenName: 'timeline',
  markdownTokenizer: {
    name: 'timeline',
    level: 'block',
    start: containerStart('timeline'),
    tokenize(src, _tokens, lexer) {
      const found = readContainer(src)
      if (!found || found.name !== 'timeline') return undefined
      const entries: TimelineEntry[] = []
      let body: string[] | null = null
      const flush = () => {
        if (body && entries.length) entries[entries.length - 1].body = lexer.inlineTokens(body.join(' '))
      }
      for (const line of found.body.split('\n')) {
        if (!line.trim()) continue
        if (ITEM_LINE.test(line)) {
          flush()
          const [when, title] = line.includes('|') ? fieldsOf(line, 2) : ['', line.replace(ITEM_LINE, '').trim()]
          entries.push({ when: lexer.inlineTokens(when), title: lexer.inlineTokens(title), body: [] })
          body = []
        } else if (body) {
          body.push(line.trim())
        }
      }
      flush()
      if (!entries.length) return undefined
      return { type: 'timeline', raw: found.raw, entries }
    },
  },
  parseMarkdown: (token, helpers) =>
    helpers.createNode(
      'timeline',
      {},
      (token.entries as TimelineEntry[]).map((entry) =>
        helpers.createNode('timelineItem', {}, [
          fieldNode('timelineWhen', inline(helpers, entry.when)),
          fieldNode('timelineTitle', inline(helpers, entry.title)),
          fieldNode('timelineBody', inline(helpers, entry.body)),
        ])
      )
    ),
  renderMarkdown: (node, helpers) => {
    const items = (node.content ?? []).map((item: JSONContent) => {
      const [when, title, body] = (item.content ?? []).map((f) => helpers.renderChildren(f.content ?? []).trim())
      const head = `- ${when} | ${title}`
      return body ? `${head}\n  ${body}` : head
    })
    return `:::timeline\n${items.join('\n')}\n:::`
  },
})

export const TimelineItem = Node.create({
  name: 'timelineItem',
  content: 'timelineWhen timelineTitle timelineBody',
  defining: true,
  isolating: true,
  parseHTML: () => [{ tag: 'div[data-item="timeline"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-item': 'timeline' }), 0],
})

// ---------------------------------------------------------------------------
// Stat cards
// ---------------------------------------------------------------------------
//
//   :::stats
//   - 活跃项目 | 128 | +12%
//   :::

export const Stats = Node.create({
  name: 'stats',
  group: 'block',
  content: 'statItem+',
  defining: true,
  isolating: true,
  parseHTML: () => [{ tag: 'div[data-block="stats"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-block': 'stats' }), 0],
  markdownTokenName: 'stats',
  markdownTokenizer: {
    name: 'stats',
    level: 'block',
    start: containerStart('stats'),
    tokenize(src, _tokens, lexer) {
      const found = readContainer(src)
      if (!found || found.name !== 'stats') return undefined
      const cards = found.body
        .split('\n')
        .filter((line) => line.trim())
        .map((line) => fieldsOf(line, 3).map((part) => lexer.inlineTokens(part)))
      if (!cards.length) return undefined
      return { type: 'stats', raw: found.raw, cards }
    },
  },
  parseMarkdown: (token, helpers) =>
    helpers.createNode(
      'stats',
      {},
      (token.cards as MarkdownToken[][][]).map(([name, value, delta]) =>
        helpers.createNode('statItem', {}, [
          fieldNode('statName', inline(helpers, name)),
          fieldNode('statValue', inline(helpers, value)),
          fieldNode('statDelta', inline(helpers, delta)),
        ])
      )
    ),
  renderMarkdown: (node, helpers) => {
    const cards = (node.content ?? []).map((card: JSONContent) => {
      const [name, value, delta] = (card.content ?? []).map((f) => helpers.renderChildren(f.content ?? []).trim())
      return delta ? `- ${name} | ${value} | ${delta}` : `- ${name} | ${value}`
    })
    return `:::stats\n${cards.join('\n')}\n:::`
  },
})

export const StatItem = Node.create({
  name: 'statItem',
  content: 'statName statValue statDelta',
  defining: true,
  isolating: true,
  parseHTML: () => [{ tag: 'div[data-item="stat"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-item': 'stat' }), 0],
})

// ---------------------------------------------------------------------------
// Columns
// ---------------------------------------------------------------------------
//
//   ::::columns
//   :::column
//   **方案 A**
//   :::
//   ::::

export const Columns = Node.create({
  name: 'columns',
  group: 'block',
  content: 'column{2,3}',
  defining: true,
  isolating: true,
  parseHTML: () => [{ tag: 'div[data-block="columns"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-block': 'columns' }), 0],
  markdownTokenName: 'columns',
  markdownTokenizer: {
    name: 'columns',
    level: 'block',
    start: containerStart('columns'),
    tokenize(src, _tokens, lexer) {
      const found = readContainer(src)
      if (!found || found.name !== 'columns') return undefined
      const columns: MarkdownToken[][] = []
      let rest = found.body.replace(/^\n+/, '')
      while (rest) {
        const column = readContainer(rest)
        if (!column || column.name !== 'column') return undefined
        columns.push(lexer.blockTokens(column.body))
        rest = rest.slice(column.raw.length).replace(/^\n+/, '')
      }
      if (columns.length < 2 || columns.length > 3) return undefined
      return { type: 'columns', raw: found.raw, columns }
    },
  },
  parseMarkdown: (token, helpers) =>
    helpers.createNode(
      'columns',
      {},
      (token.columns as MarkdownToken[][]).map((tokens) => {
        const content = helpers.parseChildren(tokens)
        return helpers.createNode('column', {}, content.length ? content : [{ type: 'paragraph' }])
      })
    ),
  renderMarkdown: (node, helpers) => {
    const columns = (node.content ?? []).map(
      (column: JSONContent) => `:::column\n${helpers.renderChildren(column.content ?? [], '\n\n').trim()}\n:::`
    )
    return `::::columns\n${columns.join('\n')}\n::::`
  },
})

export const Column = Node.create({
  name: 'column',
  content: 'block+',
  defining: true,
  isolating: true,
  parseHTML: () => [{ tag: 'div[data-item="column"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-item': 'column' }), 0],
})

// ---------------------------------------------------------------------------
// Callout: a GitHub alert
// ---------------------------------------------------------------------------
//
//   > [!IMPORTANT]
//   > 推荐方案 A。

export const CALLOUT_KINDS = ['NOTE', 'TIP', 'IMPORTANT', 'WARNING', 'CAUTION'] as const
export type CalloutKind = (typeof CALLOUT_KINDS)[number]

const ALERT_HEAD = /^ {0,3}> ?\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*(?:\n|$)/i

export const Callout = Node.create({
  name: 'callout',
  group: 'block',
  content: 'block+',
  defining: true,
  addAttributes() {
    return {
      kind: {
        default: 'NOTE',
        parseHTML: (element) => element.getAttribute('data-kind') ?? 'NOTE',
        renderHTML: (attributes) => ({ 'data-kind': attributes.kind }),
      },
    }
  },
  parseHTML: () => [{ tag: 'div[data-block="callout"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-block': 'callout' }), 0],
  markdownTokenName: 'callout',
  markdownTokenizer: {
    name: 'callout',
    level: 'block',
    start: (src) => src.search(/^ {0,3}> ?\[!/im),
    tokenize(src, _tokens, lexer) {
      const head = ALERT_HEAD.exec(src)
      if (!head) return undefined
      const lines = src.slice(head[0].length).split('\n')
      const body: string[] = []
      let length = head[0].length
      for (const line of lines) {
        if (!/^ {0,3}>/.test(line)) break
        body.push(line.replace(/^ {0,3}> ?/, ''))
        length += line.length + 1
      }
      const raw = src.slice(0, Math.min(length, src.length))
      return { type: 'callout', raw, kind: head[1].toUpperCase(), tokens: lexer.blockTokens(body.join('\n')) }
    },
  },
  parseMarkdown: (token, helpers) => {
    const content = helpers.parseChildren(token.tokens ?? [])
    return helpers.createNode('callout', { kind: token.kind }, content.length ? content : [{ type: 'paragraph' }])
  },
  renderMarkdown: (node, helpers) => {
    const body = helpers.renderChildren(node.content ?? [], '\n\n').trim()
    const lines = body ? body.split('\n').map((line) => (line ? `> ${line}` : '>')) : []
    return [`> [!${node.attrs?.kind ?? 'NOTE'}]`, ...lines].join('\n')
  },
})

// ---------------------------------------------------------------------------
// Folded section: <details>
// ---------------------------------------------------------------------------
//
//   <details><summary>原始数据</summary>
//
//   正文……
//
//   </details>

const DETAILS_HEAD = /^ {0,3}<details(?:\s+open)?>[ \t]*\n?[ \t]*<summary>([\s\S]*?)<\/summary>[ \t]*(?:\n|$)/i

export const Details = Node.create({
  name: 'details',
  group: 'block',
  content: 'detailsSummary detailsContent',
  defining: true,
  isolating: true,
  parseHTML: () => [{ tag: 'div[data-block="details"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-block': 'details' }), 0],
  markdownTokenName: 'details',
  markdownTokenizer: {
    name: 'details',
    level: 'block',
    start: (src) => src.search(/^ {0,3}<details/im),
    tokenize(src, _tokens, lexer) {
      const head = DETAILS_HEAD.exec(src)
      if (!head) return undefined
      // The matching close: count the folds opened inside this one.
      const tags = /<(\/?)details\b[^>]*>/gi
      tags.lastIndex = head[0].length
      let depth = 1
      let end = -1
      for (let m = tags.exec(src); m; m = tags.exec(src)) {
        depth += m[1] ? -1 : 1
        if (depth === 0) {
          end = m.index
          break
        }
      }
      if (end < 0) return undefined
      const closeLine = /^<\/details>[ \t]*(?:\n|$)/i.exec(src.slice(end))
      const raw = src.slice(0, end + (closeLine ? closeLine[0].length : '</details>'.length))
      return {
        type: 'details',
        raw,
        summary: lexer.inlineTokens(head[1].trim()),
        tokens: lexer.blockTokens(src.slice(head[0].length, end).trim()),
      }
    },
  },
  parseMarkdown: (token, helpers) => {
    const content = helpers.parseChildren(token.tokens ?? [])
    return helpers.createNode('details', {}, [
      fieldNode('detailsSummary', inline(helpers, token.summary)),
      helpers.createNode('detailsContent', {}, content.length ? content : [{ type: 'paragraph' }]),
    ])
  },
  renderMarkdown: (node, helpers) => {
    const [summary, content] = node.content ?? []
    const title = helpers.renderChildren(summary?.content ?? []).trim()
    const body = helpers.renderChildren(content?.content ?? [], '\n\n').trim()
    return `<details><summary>${title}</summary>\n\n${body}\n\n</details>`
  },
})

export const DetailsSummary = Node.create({
  name: 'detailsSummary',
  content: 'inline*',
  defining: true,
  parseHTML: () => [{ tag: 'div[data-field="summary"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-field': 'summary' }), 0],
})

export const DetailsContent = Node.create({
  name: 'detailsContent',
  content: 'block+',
  defining: true,
  parseHTML: () => [{ tag: 'div[data-field="details"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-field': 'details' }), 0],
})

// ---------------------------------------------------------------------------
// Status tag: a style on words, like bold
// ---------------------------------------------------------------------------
//
// `{✓ 通过}` `{✗ 不发}` `{! 待定}`. A mark, not a node: the words stay ordinary
// text, so two people can type in one tag, and comments and suggestions reach
// every character of it.

export const STATUS_KINDS = { ok: '✓', no: '✗', warn: '!' } as const
export type StatusKind = keyof typeof STATUS_KINDS
const KIND_OF: Record<string, StatusKind> = { '✓': 'ok', '✗': 'no', '!': 'warn' }

const STATUS_RE = /^\{([✓✗!])[ \t]+([^{}\n]*?[^{}\s])[ \t]*\}/

export const Status = Mark.create({
  name: 'status',
  inclusive: false,
  excludes: 'status',
  addAttributes() {
    return {
      kind: {
        default: 'ok',
        parseHTML: (element) => element.getAttribute('data-status') ?? 'ok',
        renderHTML: (attributes) => ({ 'data-status': attributes.kind }),
      },
    }
  },
  parseHTML: () => [{ tag: 'span[data-status]' }],
  renderHTML: ({ HTMLAttributes }) => ['span', mergeAttributes(HTMLAttributes), 0],
  markdownTokenName: 'status',
  markdownTokenizer: {
    name: 'status',
    level: 'inline',
    start: (src) => src.search(/\{[✓✗!][ \t]/),
    tokenize(src, _tokens, lexer) {
      const match = STATUS_RE.exec(src)
      if (!match) return undefined
      return { type: 'status', raw: match[0], kind: KIND_OF[match[1]], tokens: lexer.inlineTokens(match[2]) }
    },
  },
  parseMarkdown: (token, helpers) =>
    helpers.applyMark('status', helpers.parseInline(token.tokens ?? []), { kind: token.kind }),
  renderMarkdown: (node, helpers) => {
    const kind = (node.attrs?.kind ?? 'ok') as StatusKind
    return `{${STATUS_KINDS[kind] ?? '✓'} ${helpers.renderChildren(node)}}`
  },
})

// ---------------------------------------------------------------------------
// Formulas: $…$ and $$…$$
// ---------------------------------------------------------------------------
//
// A `$` followed by a digit is money, not a formula: `$5 到 $10` stays text.
// The source is the node's text; the editor renders it with KaTeX.

const INLINE_MATH_RE = /^\$(?![\s\d$])((?:\\\$|[^$\n])+?)(?<!\s)\$(?!\d)/
const BLOCK_MATH_RE = /^ {0,3}\$\$[ \t]*\n?([\s\S]+?)\n?[ \t]*\$\$[ \t]*(?:\n|$)/

export const MathInline = Node.create({
  name: 'mathInline',
  group: 'inline',
  inline: true,
  atom: true,
  addAttributes() {
    return { latex: { default: '', rendered: false } }
  },
  parseHTML: () => [{ tag: 'span[data-math="inline"]', getAttrs: (element) => ({ latex: element.textContent ?? '' }) }],
  renderHTML: ({ node, HTMLAttributes }) => [
    'span',
    mergeAttributes(HTMLAttributes, { 'data-math': 'inline' }),
    node.attrs.latex as string,
  ],
  renderText: ({ node }) => `$${node.attrs.latex as string}$`,
  markdownTokenName: 'mathInline',
  markdownTokenizer: {
    name: 'mathInline',
    level: 'inline',
    start: (src) => src.search(/\$(?![\s\d$])/),
    tokenize(src) {
      const match = INLINE_MATH_RE.exec(src)
      if (!match) return undefined
      return { type: 'mathInline', raw: match[0], latex: match[1] }
    },
  },
  parseMarkdown: (token, helpers) => helpers.createNode('mathInline', { latex: token.latex }),
  renderMarkdown: (node) => `$${node.attrs?.latex ?? ''}$`,
})

export const MathBlock = Node.create({
  name: 'mathBlock',
  group: 'block',
  atom: true,
  addAttributes() {
    return { latex: { default: '', rendered: false } }
  },
  parseHTML: () => [{ tag: 'div[data-math="block"]', getAttrs: (element) => ({ latex: element.textContent ?? '' }) }],
  renderHTML: ({ node, HTMLAttributes }) => [
    'div',
    mergeAttributes(HTMLAttributes, { 'data-math': 'block' }),
    node.attrs.latex as string,
  ],
  markdownTokenName: 'mathBlock',
  markdownTokenizer: {
    name: 'mathBlock',
    level: 'block',
    start: (src) => src.search(/^ {0,3}\$\$/m),
    tokenize(src) {
      const match = BLOCK_MATH_RE.exec(src)
      if (!match) return undefined
      return { type: 'mathBlock', raw: match[0], latex: match[1].trim() }
    },
  },
  parseMarkdown: (token, helpers) => helpers.createNode('mathBlock', { latex: token.latex }),
  renderMarkdown: (node) => `$$\n${node.attrs?.latex ?? ''}\n$$`,
})

// ---------------------------------------------------------------------------
// Footnotes: [^1] in the text, `[^1]: …` on a line of its own
// ---------------------------------------------------------------------------
//
// The note is shown where it is referenced (a popover, or the margin on a wide
// screen); its definition stays a block of its own, so its text is ordinary
// editable text.

const FOOTNOTE_REF_RE = /^\[\^([^\]\s^]+)\](?!:)/
const FOOTNOTE_DEF_RE = /^ {0,3}\[\^([^\]\s^]+)\]:[ \t]*(.*)(?:\n|$)/

export const FootnoteRef = Node.create({
  name: 'footnoteRef',
  group: 'inline',
  inline: true,
  atom: true,
  addAttributes() {
    return {
      label: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-footnote-ref') ?? '',
        renderHTML: (attributes) => ({ 'data-footnote-ref': attributes.label }),
      },
    }
  },
  parseHTML: () => [{ tag: 'sup[data-footnote-ref]' }],
  renderHTML: ({ node, HTMLAttributes }) => ['sup', mergeAttributes(HTMLAttributes), node.attrs.label as string],
  renderText: ({ node }) => `[^${node.attrs.label as string}]`,
  markdownTokenName: 'footnoteRef',
  markdownTokenizer: {
    name: 'footnoteRef',
    level: 'inline',
    start: (src) => src.indexOf('[^'),
    tokenize(src) {
      const match = FOOTNOTE_REF_RE.exec(src)
      if (!match) return undefined
      return { type: 'footnoteRef', raw: match[0], label: match[1] }
    },
  },
  parseMarkdown: (token, helpers) => helpers.createNode('footnoteRef', { label: token.label }),
  renderMarkdown: (node) => `[^${node.attrs?.label ?? ''}]`,
})

export const FootnoteDef = Node.create({
  name: 'footnoteDef',
  group: 'block',
  content: 'inline*',
  defining: true,
  addAttributes() {
    return {
      label: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-footnote') ?? '',
        renderHTML: (attributes) => ({ 'data-footnote': attributes.label }),
      },
    }
  },
  parseHTML: () => [{ tag: 'div[data-footnote]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes), 0],
  markdownTokenName: 'footnoteDef',
  markdownTokenizer: {
    name: 'footnoteDef',
    level: 'block',
    start: (src) => src.search(/^ {0,3}\[\^[^\]\s^]+\]:/m),
    tokenize(src, _tokens, lexer) {
      const match = FOOTNOTE_DEF_RE.exec(src)
      if (!match) return undefined
      return { type: 'footnoteDef', raw: match[0], label: match[1], tokens: lexer.inlineTokens(match[2].trim()) }
    },
  },
  parseMarkdown: (token, helpers) =>
    helpers.createNode('footnoteDef', { label: token.label }, inline(helpers, token.tokens)),
  renderMarkdown: (node, helpers) =>
    `[^${node.attrs?.label ?? ''}]: ${helpers.renderChildren(node.content ?? []).trim()}`,
})

/** Every node and mark this module adds to the document. */
export const docBlocks = [
  Callout,
  Status,
  Timeline,
  TimelineItem,
  field('timelineWhen', 'when'),
  field('timelineTitle', 'title'),
  field('timelineBody', 'body'),
  Stats,
  StatItem,
  field('statName', 'name'),
  field('statValue', 'value'),
  field('statDelta', 'delta'),
  Columns,
  Column,
  Details,
  DetailsSummary,
  DetailsContent,
  MathInline,
  MathBlock,
  FootnoteRef,
  FootnoteDef,
]
