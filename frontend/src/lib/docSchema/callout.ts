// Callout: a GitHub alert
//
//   > [!IMPORTANT]
//   > 推荐方案 A。

import { mergeAttributes, Node } from '@tiptap/core'

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
