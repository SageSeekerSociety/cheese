// Formulas: $…$ and $$…$$
//
// A `$` followed by a digit is money when prose follows (`$5 到 $10`,
// `花费 $100，其中$x$是单价`), and a formula when math follows (`$1$`,
// `$0 \le k \le n-1$`, `$1 - \sum_{k=0}^{n-1} p^k$`).
// The source is the node's text; the editor renders it with KaTeX.

import { mergeAttributes, Node } from '@tiptap/core'

const INLINE_DOUBLE_MATH_RE = /^\$\$(?!\$)[ \t]*((?:\\.|(?!\$\$)[^\\\n`])+?)[ \t]*\$\$(?!\$)/
const INLINE_SINGLE_MATH_RE = /^\$(?![\s$])((?:\\.|[^$\\\n`])+?)(?<!\s)\$(?!\d)/
const BLOCK_MATH_RE = /^ {0,3}\$\$[ \t]*\n?((?:\\.|(?!\$\$)[\s\S])+?)\n?[ \t]*\$\$[ \t]*(?:\n|$)/
const BLOCK_MATH_START_RE = /(?:^|\n) {0,3}\$\$[ \t]*\n?(?:\\.|(?!\$\$)[\s\S])+?\n?[ \t]*\$\$[ \t]*(?:\n|$)/

const PROSE_PUNCT_RE = /[，。；！？（）【】《》“”‘’、]/
const CJK_RE = /[⺀-〿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]/

function stripBraced(s: string): string {
  let prev = ''
  let cur = s
  while (prev !== cur) {
    prev = cur
    cur = cur.replace(/\{[^{}]*\}/g, '')
  }
  return cur
}

function hasUnclosedOpeners(s: string): boolean {
  let paren = 0
  let bracket = 0
  let brace = 0
  for (let i = 0; i < s.length; i++) {
    if (s[i] === '\\') {
      i++
      continue
    }
    if (s[i] === '(') paren++
    else if (s[i] === ')' && paren > 0) paren--
    else if (s[i] === '[') bracket++
    else if (s[i] === ']' && bracket > 0) bracket--
    else if (s[i] === '{') brace++
    else if (s[i] === '}' && brace > 0) brace--
  }
  return paren > 0 || bracket > 0 || brace > 0
}

export function isInlineMath(latex: string): boolean {
  if (/(?<!\\)[([{（【《“‘—]$/.test(latex)) return false
  const bare = stripBraced(latex)
  if (PROSE_PUNCT_RE.test(bare)) return false
  if (/^\d/.test(latex)) {
    if (hasUnclosedOpeners(latex)) return false
    if (CJK_RE.test(bare)) return false
    if (/^\d[\d,.]*\s+[a-zA-Z]{2,}\b/.test(bare)) return false
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
        if (latex) return { type: 'mathInline', raw: doubleMatch[0], latex }
      }
      const match = INLINE_SINGLE_MATH_RE.exec(src)
      if (!match || !isInlineMath(match[1])) return undefined
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
    start: (src) => {
      const match = BLOCK_MATH_START_RE.exec(src)
      if (!match) return -1
      return match[0].startsWith('\n') ? match.index + 1 : match.index
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
