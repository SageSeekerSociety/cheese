// A mini-app: something the reader works with to understand it — a slider to
// drag, numbers to try — that 芝士 wrote as code, kept apart from the document.
//
//   :::app q7m2kx4t@3 并发数对排队时间的影响
//   拖动并发数，看排队时间和花费怎么变。
//   :::
//
// The opening line names the app (a key unique across the platform, so a
// block copied into another document still names the same app), the version
// the document shows and its title. The one sentence between is the block's
// text: what it shows and what to take from it. That sentence is all a reader
// sees where the app cannot run, and all 芝士 reads of it in the document.
//
// Part of the document schema module (see ./index.ts): nothing here touches the
// DOM or imports the app.

import type { JSONContent } from '@tiptap/core'

import { mergeAttributes, Node } from '@tiptap/core'

import { readContainer } from './blocks'

const HEAD = /^([a-z0-9]{4,16})@([1-9]\d{0,5})(?:[ \t]+(.*))?$/

/** The key, version and title an opening line names, or null. */
export function appHead(info: string): { app: string; version: number; title: string } | null {
  const match = HEAD.exec(info.trim())
  if (!match) return null
  return { app: match[1], version: Number(match[2]), title: (match[3] ?? '').trim() }
}

export const App = Node.create({
  name: 'app',
  group: 'block',
  content: 'inline*',
  defining: true,
  isolating: true,
  addAttributes() {
    return {
      app: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-app') ?? '',
        renderHTML: (attributes) => ({ 'data-app': attributes.app }),
      },
      version: {
        default: 1,
        parseHTML: (element) => Number(element.getAttribute('data-version')) || 1,
        renderHTML: (attributes) => ({ 'data-version': String(attributes.version) }),
      },
      title: {
        default: '',
        parseHTML: (element) => element.getAttribute('data-title') ?? '',
        renderHTML: (attributes) => ({ 'data-title': attributes.title }),
      },
    }
  },
  parseHTML: () => [{ tag: 'div[data-block="app"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-block': 'app' }), 0],
  renderText: ({ node }) => `${node.attrs.title as string}${node.textContent ? `：${node.textContent}` : ''}`,
  markdownTokenName: 'app',
  markdownTokenizer: {
    name: 'app',
    level: 'block',
    start: (src) => src.search(/^:{3,}app\b/m),
    tokenize(src, _tokens, lexer) {
      const found = readContainer(src)
      if (!found || found.name !== 'app') return undefined
      const head = appHead(found.info)
      if (!head) return undefined
      const body = lexer.blockTokens(found.body.trim()).filter((token) => token.type !== 'space')
      if (body.length > 1 || (body.length === 1 && body[0].type !== 'paragraph')) return undefined
      return { type: 'app', raw: found.raw, ...head, tokens: body[0]?.tokens ?? [] }
    },
  },
  parseMarkdown: (token, helpers) => {
    const content: JSONContent[] = token.tokens?.length ? helpers.parseInline(token.tokens) : []
    return helpers.createNode('app', { app: token.app, version: token.version, title: token.title }, content)
  },
  renderMarkdown: (node, helpers) => {
    const { app, version, title } = node.attrs ?? {}
    const head = `:::app ${app}@${version}${title ? ` ${title}` : ''}`
    const text = helpers.renderChildren(node.content ?? []).trim()
    return text ? `${head}\n${text}\n:::` : `${head}\n:::`
  },
})
