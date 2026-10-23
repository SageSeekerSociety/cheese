// Timeline
//
//   :::timeline
//   - 10:02 | 提交合并请求 {✓ 单元测试}
//     类型检查报了 2 处错误。
//   :::

import type { JSONContent, MarkdownToken } from '@tiptap/core'

import { mergeAttributes, Node } from '@tiptap/core'

import { containerStart, fieldsOf, ITEM_LINE, readContainer } from './containers'
import { fieldNode, inline } from './fields'

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
