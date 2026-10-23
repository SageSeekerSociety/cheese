// Status tag: a style on words, like bold
//
// `{✓ 通过}` `{✗ 不发}` `{! 待定}`. A mark, not a node: the words stay ordinary
// text, so two people can type in one tag, and comments and suggestions reach
// every character of it.

import { Mark, mergeAttributes } from '@tiptap/core'

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
  // The class is what the stylesheet keys on: `data-status` alone is a common
  // attribute name, and table rows elsewhere carry one of their own.
  renderHTML: ({ HTMLAttributes }) => ['span', mergeAttributes({ class: 'doc-status' }, HTMLAttributes), 0],
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
