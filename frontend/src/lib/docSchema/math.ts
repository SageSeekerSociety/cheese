// Formulas: $…$ and $$…$$
//
// A `$` followed by a digit is money when prose follows (`$5 到 $10`,
// `花费 $100，其中$x$是单价`), and a formula when math follows (`$1$`,
// `$0 \le k \le n-1$`, `$1 - \sum_{k=0}^{n-1} p^k$`).
// The source is the node's text; the editor renders it with KaTeX.

import { mergeAttributes, Node } from '@tiptap/core'

export const INLINE_DOUBLE_MATH_RE = /^\$\$(?!\$)[ \t]*((?:\\.|[^$\\\n`])+?)[ \t]*\$\$(?!\$)/
export const INLINE_SINGLE_MATH_RE = /^\$(?![\s$])((?:\\.|[^$\\\n`])+?)(?<!\s)\$(?!\d)/
export const BLOCK_MATH_RE = /^ {0,3}\$\$[ \t]*\n?((?:\\[\s\S]|(?!\$\$)[^\\])+?)\n?[ \t]*\$\$[ \t]*(?:\n|$)/

// Fullwidth prose punctuation and bare CJK ideographs outside `{…}` indicate
// prose between two unclosed `$` tokens (`$100，其中$x$`, `$HOME和$USER`).
const PROSE_PUNCT_RE = /[，。；！？（）【】《》“”‘’、]/
const CJK_RE = /[⺀-〿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]/
// Currency followed by English prose (`$100 for ($x$)`, `$100k, or$x$`).
const CURRENCY_PROSE_RE = /^\d[\d,.]*[kmbKMB]?(?:,\s*|\s+)(?:to|or|and|for|in|of|per|with|from|at|by|is|are|the|vs)\b/i

function analyzeMath(s: string): { paren: number; bracket: number; brace: number; bare: string } {
  const clean = s.replace(/\\left[([|.]/g, '').replace(/\\right[)\]|.]/g, '')
  let paren = 0
  let bracket = 0
  let brace = 0
  let bare = ''
  for (let i = 0; i < clean.length; i++) {
    const ch = clean[i]
    if (ch === '\\') {
      if (brace === 0 && i + 1 < clean.length) bare += clean.slice(i, i + 2)
      i++
      continue
    }
    if (ch === '{') {
      brace++
      continue
    }
    if (ch === '}') {
      if (brace > 0) brace--
      continue
    }
    if (brace === 0) bare += ch
    if (ch === '(') paren++
    else if (ch === ')' && paren > 0) paren--
    else if (ch === '[') bracket++
    else if (ch === ']' && bracket > 0) bracket--
  }
  return { paren, bracket, brace, bare: bare.replace(/[_^][⺀-〿぀-ヿ㐀-䶿一-鿿]/g, '') }
}

export function isInlineMath(latex: string): boolean {
  if (!latex || /(?<![\\^_])[-+*/=<>~,:;([{（【《“‘—]$/.test(latex)) return false
  const { paren, bracket, brace, bare } = analyzeMath(latex)
  if (brace > 0) return false
  if (PROSE_PUNCT_RE.test(bare) || CJK_RE.test(bare)) return false
  if (/^\d/.test(latex)) {
    if (paren > 0 || bracket > 0) return false
    if (CURRENCY_PROSE_RE.test(bare)) return false
  }
  return true
}

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
    start: (src) => src.search(/(?<!\\)\$(?!\s)/),
    tokenize(src) {
      const doubleMatch = INLINE_DOUBLE_MATH_RE.exec(src)
      if (doubleMatch) {
        const latex = doubleMatch[1].trim()
        if (isInlineMath(latex)) return { type: 'mathInline', raw: doubleMatch[0], latex }
      }
      const match = INLINE_SINGLE_MATH_RE.exec(src)
      if (!match || !isInlineMath(match[1])) return undefined
      return { type: 'mathInline', raw: match[0], latex: match[1] }
    },
  },
  parseMarkdown: (token, helpers) => helpers.createNode('mathInline', { latex: token.latex }),
  renderMarkdown: (node) => `$${String(node.attrs?.latex ?? '').trim()}$`,
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
    start: (src) => {
      const i = src.search(/\n {0,3}\$\$/)
      return i === -1 ? -1 : i + 1
    },
    tokenize(src) {
      const match = BLOCK_MATH_RE.exec(src)
      if (!match) return undefined
      return { type: 'mathBlock', raw: match[0], latex: match[1].trim() }
    },
  },
  parseMarkdown: (token, helpers) => helpers.createNode('mathBlock', { latex: token.latex }),
  renderMarkdown: (node) => `$$\n${node.attrs?.latex ?? ''}\n$$`,
})
