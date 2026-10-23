// Columns
//
//   ::::columns
//   :::column
//   **方案 A**
//   :::
//   ::::

import type { JSONContent, MarkdownToken } from '@tiptap/core'

import { mergeAttributes, Node } from '@tiptap/core'

import { containerStart, readContainer } from './containers'

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
