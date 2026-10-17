// Footnotes: [^1] in the text, `[^1]: …` on a line of its own
//
// The note is shown where it is referenced (a popover, or the margin on a wide
// screen); its definition stays a block of its own, so its text is ordinary
// editable text.

import { mergeAttributes, Node } from '@tiptap/core'

import { inline } from './fields'

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
