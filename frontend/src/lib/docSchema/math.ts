// Formulas: $…$ and $$…$$
//
// A `$` followed by a digit is money, not a formula: `$5 到 $10` stays text.
// The source is the node's text; the editor renders it with KaTeX.

import { mergeAttributes, Node } from '@tiptap/core'

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
