// Folded section: <details>
//
//   <details><summary>原始数据</summary>
//
//   正文……
//
//   </details>

import { mergeAttributes, Node } from '@tiptap/core'

import { fieldNode, inline } from './fields'

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
