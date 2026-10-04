/**
 * 「这一块在屏幕上长什么样」的纯判断：它是图片吗、它摆出来的文件叫什么、
 * 它带着几个选项、被回复时该引用哪一句。
 *
 * 全是块自己就能答的问题——不查名册、不碰话题、不发请求。放在这里是因为**房间和
 * 消息行两边都要问**：引用条在房间那一层画（它在输入框上方），而同一句摘要也出现
 * 在消息行里。各留一份就会有一天只改了其中一处。
 */

import type { Block } from '@/cx_types'
import type { RefNames } from './refChip'

import { isAgentBlock } from './authorship'
import { docReadNow } from './docReadLoad'
import { fileLabel } from './fileKind'
import { plainRefs } from './refChip'

import { t } from '@/i18n'

/** 附件块里装的是不是一张图。图要直接画出来，别的给一个下载入口。 */
export function isImageBlock(block: Block): boolean {
  return block.kind === 'attachment' && (block.mime_type || '').startsWith('image/')
}

/**
 * 引用一句话时显示的摘要。附件没有正文可引，就说它是什么。
 *
 * 芝士的话是 markdown，引用条里是一行纯文本：不先读成字，引到的就是
 * `**A / B / C**`、一对反引号和 `:::chart`。按文档的读法读成字（lib/docRead.ts）；读法第一次用到才加载，
 * 加载完之前先引原文，加载完自己换掉。人说的话原样显示，所以也原样引用。@ 人、提话题、
 * 指文件的 token 两边都一样读成名字，和正文里 chip 上写的字一致。
 */
export function replySnippet(block: Block, maps: RefNames): string {
  if (block.kind === 'attachment')
    return isImageBlock(block) ? t('work.room.attachments.imageSnippet') : t('work.room.attachments.fileSnippet')
  const read = isAgentBlock(block) ? docReadNow() : null
  const source = read ? read.plainText(block.content, 'chat') : block.content
  const text = plainRefs(source, maps).replace(/\s+/g, ' ').trim()
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

/** 一个选项。`text` 是提问方给的那几个字，`explain` 是他补的解释，没补就没有。 */
export type AskOption = { text: string; explain?: string }

/**
 * 当前生效的那一版答案。`answer_log` 末条才是生效的那一版，前面几条是被更正掉的。
 */
export type AskAnswer = {
  kind: 'option' | 'note' | 'reject'
  /** 这一版认的是什么 —— 按 `kind` 决定是选项文字、自由输入，还是「以上都不是」。 */
  label: string
  by: string
}

/** 这一条是不是带选项的提问（`cheese_ask`）。不是就返回 null。 */
export function askOptions(block: Block): AskOption[] | null {
  const opts = (block.meta as Record<string, unknown> | null)?.options
  if (!Array.isArray(opts) || !opts.length) return null
  // 只认 `{text}` 对象。`string[]` 是迁移前的形状，那之后没有一处还会写它。
  const out = opts.flatMap((o) => {
    const item = o as { text?: unknown; explain?: unknown } | null
    if (!item || typeof item !== 'object' || typeof item.text !== 'string') return []
    return [{ text: item.text, ...(item.explain ? { explain: String(item.explain) } : {}) }]
  })
  return out.length ? out : null
}

/** 已经有人答过了：答的什么、谁答的。房间里所有人看到的是同一版。 */
export function askAnswered(block: Block): AskAnswer | null {
  const log = (block.meta as Record<string, unknown> | null)?.answer_log
  if (!Array.isArray(log) || !log.length) return null
  const last = log[log.length - 1] as Record<string, unknown>
  const kind = (last.kind ?? 'option') as AskAnswer['kind']
  // 「以上都不是」走目录：和表单里的 reject 选项共用同一个说法。
  const label =
    kind === 'reject' ? t('ask.form.reject') : kind === 'note' ? String(last.note ?? '') : String(last.option ?? '')
  return { kind, label, by: String(last.by ?? '') }
}

/**
 * 作答时要带的那个版本号：就是日志现在的长度。初答 0，之后是 `answer_log` 末项
 * 的 `v` —— 而末项的 `v` 恰好等于长度，所以这里不必再读一次。
 */
export function askVersion(block: Block): number {
  const log = (block.meta as Record<string, unknown> | null)?.answer_log
  return Array.isArray(log) ? log.length : 0
}
