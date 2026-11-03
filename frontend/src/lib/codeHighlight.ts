// 代码查看器把一份源码切成一行一行的高亮 HTML。
//
// 高亮用 lowlight（highlight.js 的语法），和文档、消息里的代码块是同一套：它吐出的
// `.hljs-*` 类名在 `styles/docBlocks.css` 里按 `--code-*` 令牌上色，深浅两套主题都
// 现成，同一段代码在对话里和预览里长得一样。Prism 那一套（`utils/prism.ts`）挂的是
// 只有浅色的 solarized 主题，不用它。
//
// 按行切是为了行号和「第 N–M 行」：每一行是自己的一个元素，读者选中的一段落在哪几行，
// 从 DOM 上直接读得出来。lowlight 给的是一棵树，一个跨行的记号（多行字符串、块注释）
// 在树上是一个节点；切行时把它在每一行重新开一次，行内的 HTML 才是配平的。
//
// 这里自己建一个实例而不借 `lib/docSchema` 那一个：那个模块连着整套编辑器扩展，预览
// 只为了高亮去加载它不划算。两个实例共用同一份语法定义。

import { common, createLowlight } from 'lowlight'

const lowlight = createLowlight(common)

/** 超过这么多字就不高亮，只按行摆出纯文本：高亮一份几 MB 的文件会把页面卡住好几秒，
 *  而读者在那么大的文件里要的多半是行号和搜索。 */
export const HIGHLIGHT_MAX_CHARS = 400_000

interface HastText {
  type: 'text'
  value: string
}
interface HastElement {
  type: 'element'
  properties?: { className?: string[] | string }
  children: HastNode[]
}
type HastNode = HastText | HastElement | { type: string }

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/** 把源码切成行。行尾的换行不算多出来的一行：以换行结尾的文件最后一行是它前面那一行。 */
export function splitLines(source: string): string[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n')
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  return lines
}

/** 每一行的高亮 HTML（已转义，可以直接塞进元素里）。认不出的语言、太大的文件按纯文本。 */
export function highlightLines(source: string, language: string | null): string[] {
  const plain = splitLines(source)
  if (!language || !lowlight.registered(language) || source.length > HIGHLIGHT_MAX_CHARS) {
    return plain.map(escapeHtml)
  }
  let tree: { children: HastNode[] }
  try {
    tree = lowlight.highlight(language, plain.join('\n'))
  } catch {
    return plain.map(escapeHtml)
  }
  const lines: string[] = ['']
  const walk = (nodes: HastNode[], classes: string[]) => {
    for (const node of nodes) {
      if (node.type === 'text') {
        const parts = (node as HastText).value.split('\n')
        parts.forEach((part, index) => {
          if (index > 0) lines.push('')
          if (!part) return
          const open = classes.map((name) => `<span class="${escapeHtml(name)}">`).join('')
          lines[lines.length - 1] += open + escapeHtml(part) + '</span>'.repeat(classes.length)
        })
      } else if (node.type === 'element') {
        const element = node as HastElement
        const raw = element.properties?.className
        const own = Array.isArray(raw) ? raw.join(' ') : raw ?? ''
        walk(element.children, own ? [...classes, own] : classes)
      }
    }
  }
  walk(tree.children, [])
  return lines
}

/** 第 `start` 到第 `end` 行（从 1 数，含两头）的原文。 */
export function sourceOfLines(source: string, start: number, end: number): string {
  return splitLines(source)
    .slice(start - 1, end)
    .join('\n')
}
