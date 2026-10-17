// Stat cards
//
//   :::stats
//   - 活跃项目 | 128 | +12%
//   :::

import type { JSONContent, MarkdownToken } from '@tiptap/core'

import { mergeAttributes, Node } from '@tiptap/core'

import { containerStart, fieldsOf, readContainer } from './containers'
import { fieldNode, inline } from './fields'

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
