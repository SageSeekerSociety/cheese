// Chart: a table, drawn
//
//   :::chart bar
//   | 周 | 改版前 | 改版后 |
//   | --- | --- | --- |
//   | 第 1 周 | 1,020 | 1,980 |
//   :::
//
// The first column is the categories (or the x values of a scatter), every
// other column one series. Opened anywhere else it is still the table.

import { mergeAttributes, Node } from '@tiptap/core'

import { containerStart, readContainer } from './containers'

export const CHART_KINDS = ['bar', 'line', 'area', 'stacked', 'pie', 'scatter'] as const
export type ChartKind = (typeof CHART_KINDS)[number]
/** The kinds whose bars can lie on their side: `:::chart bar horizontal`. */
export const CHART_HORIZONTAL = new Set<string>(['bar', 'stacked'])

/** `bar horizontal` → its kind and whether it is horizontal; null when not a chart's head. */
export function chartInfo(info: string): { kind: ChartKind; horizontal: boolean } | null {
  const [kind, ...options] = info.split(/[ \t]+/).filter(Boolean)
  if (!(CHART_KINDS as readonly string[]).includes(kind)) return null
  if (options.length > 1 || (options.length && (options[0] !== 'horizontal' || !CHART_HORIZONTAL.has(kind)))) {
    return null
  }
  return { kind: kind as ChartKind, horizontal: options.length === 1 }
}

/** A data cell as a number: `1,020`, `71%`, `$3.2`, `-1.3` read; anything with
 *  words in it does not, since its unit belongs in the column's head. Empty is
 *  a missing value, not zero. */
export function chartNumber(text: string): number | null | undefined {
  const flat = text.trim()
  if (!flat) return undefined
  const bare = flat
    .replace(/^[+＋]/, '')
    .replace(/[,，\s]/g, '')
    .replace(/^([-−]?)[$¥€£]/, '$1')
    .replace(/%$/, '')
  if (!/^[-−]?(\d+(\.\d+)?|\.\d+)$/.test(bare)) return null
  return Number(bare.replace('−', '-'))
}

export const Chart = Node.create({
  name: 'chart',
  group: 'block',
  content: 'table',
  defining: true,
  isolating: true,
  addAttributes() {
    return {
      kind: {
        default: 'bar',
        parseHTML: (element) => element.getAttribute('data-kind') ?? 'bar',
        renderHTML: (attributes) => ({ 'data-kind': attributes.kind }),
      },
      horizontal: {
        default: false,
        parseHTML: (element) => element.hasAttribute('data-horizontal'),
        renderHTML: (attributes) => (attributes.horizontal ? { 'data-horizontal': '' } : {}),
      },
    }
  },
  parseHTML: () => [{ tag: 'div[data-block="chart"]' }],
  renderHTML: ({ HTMLAttributes }) => ['div', mergeAttributes(HTMLAttributes, { 'data-block': 'chart' }), 0],
  markdownTokenName: 'chart',
  markdownTokenizer: {
    name: 'chart',
    level: 'block',
    start: containerStart('chart'),
    tokenize(src, _tokens, lexer) {
      const found = readContainer(src)
      if (!found || found.name !== 'chart') return undefined
      const info = chartInfo(found.info)
      if (!info) return undefined
      const tokens = lexer.blockTokens(found.body.trim()).filter((token) => token.type !== 'space')
      if (tokens.length !== 1 || tokens[0].type !== 'table') return undefined
      return { type: 'chart', raw: found.raw, ...info, tokens }
    },
  },
  parseMarkdown: (token, helpers) =>
    helpers.createNode(
      'chart',
      { kind: token.kind, horizontal: token.horizontal },
      helpers.parseChildren(token.tokens ?? [])
    ),
  renderMarkdown: (node, helpers) => {
    const head = `${node.attrs?.kind ?? 'bar'}${node.attrs?.horizontal ? ' horizontal' : ''}`
    return `:::chart ${head}\n${helpers.renderChildren(node.content ?? []).trim()}\n:::`
  },
})
