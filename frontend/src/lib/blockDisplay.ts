/**
 * 「这一块在屏幕上长什么样」的纯判断：它是图片吗、它摆出来的文件叫什么、
 * 它带着几个选项、被回复时该引用哪一句。
 *
 * 全是块自己就能答的问题——不查名册、不碰话题、不发请求。放在这里是因为**房间和
 * 消息行两边都要问**：引用条在房间那一层画（它在输入框上方），而同一句摘要也出现
 * 在消息行里。各留一份就会有一天只改了其中一处。
 */

import type { Token } from 'marked'
import type { Block } from '@/cx_types'

import { isAgentBlock } from './authorship'
import { fileLabel } from './fileKind'
import { markdown } from './markdown'

/** 附件块里装的是不是一张图。图要直接画出来，别的给一个下载入口。 */
export function isImageBlock(block: Block): boolean {
  return block.kind === 'attachment' && (block.mime_type || '').startsWith('image/')
}

/** markdown 渲染出来之后读得到的那些字：`**`、反引号、链接地址都不算。 */
function readableText(tokens: Token[]): string {
  return tokens
    .map((token) => {
      if ('tokens' in token && token.tokens?.length) return readableText(token.tokens)
      if (token.type === 'list') return readableText(token.items)
      if (token.type === 'table')
        return [token.header, ...token.rows]
          .flat()
          .map((cell: { tokens: Token[] }) => readableText(cell.tokens))
          .join(' ')
      if ('text' in token && typeof token.text === 'string') return token.text
      return ' '
    })
    .join(' ')
}

/**
 * 引用一句话时显示的摘要。附件没有正文可引，就说它是什么。
 *
 * 芝士的话是 markdown，引用条里是一行纯文本：不先读成字，引到的就是
 * `**A / B / C**` 和一对反引号。人说的话原样显示，所以也原样引用。
 */
export function replySnippet(block: Block): string {
  if (block.kind === 'attachment') return isImageBlock(block) ? '[图片]' : '[文件]'
  const source = isAgentBlock(block) ? readableText(markdown.lexer(block.content)) : block.content
  const text = source.replace(/\s+/g, ' ').trim()
  return text.length > 24 ? text.slice(0, 24) + '…' : text
}

/** 芝士摆出来那份东西的文件名（`cheese show` 写下的是完整路径）。 */
export function artifactName(block: Block): string {
  return block.content.split('/').pop() || block.content
}

/** 那份东西是什么类型，写在文件名下面那行。 */
export function artifactKind(block: Block): string {
  return fileLabel(block.content)
}

/** 这一条是不是带选项的提问（`cheese_ask`）。不是就返回 null。 */
export function askOptions(block: Block): string[] | null {
  const opts = (block.meta as Record<string, unknown> | null)?.options
  return Array.isArray(opts) && opts.length ? (opts as string[]) : null
}

/** 已经有人选过了：选的哪个、谁选的。房间里所有人看到的是同一个答案。 */
export function askAnswered(block: Block): { option: string; by: string } | null {
  const meta = block.meta as Record<string, unknown> | null
  return meta?.answered ? { option: String(meta.answered), by: String(meta.answered_by ?? '') } : null
}
