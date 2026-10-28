/**
 * 「这一块在屏幕上长什么样」的纯判断：它是图片吗、它摆出来的文件叫什么、
 * 它带着几个选项、被回复时该引用哪一句。
 *
 * 全是块自己就能答的问题——不查名册、不碰话题、不发请求。放在这里是因为**房间和
 * 消息行两边都要问**：引用条在房间那一层画（它在输入框上方），而同一句摘要也出现
 * 在消息行里。各留一份就会有一天只改了其中一处。
 */

import type { Block, ChecklistMeta, TodoItem } from '@/cx_types'
import type { ThreadReply } from '@/types/threads'
import type { RefNames } from './refChip'

import { isAgentBlock } from './authorship'
import { docReadNow } from './docReadLoad'
import { fileLabel, videoMimeOf } from './fileKind'
import { plainRefs, toldByMessage } from './refChip'

import { t } from '@/i18n'

/** 附件块里装的是不是一张图。图要直接画出来，别的给一个下载入口。 */
export function isImageBlock(block: Pick<Block, 'kind' | 'mime_type'>): boolean {
  return block.kind === 'attachment' && (block.mime_type || '').startsWith('image/')
}

// The raw endpoint only serves whitelisted suffixes. A video MIME can still
// describe a download-only container such as AVI or MKV.
export function isVideoBlock(block: Pick<Block, 'kind' | 'content'>): boolean {
  return block.kind === 'attachment' && !!videoMimeOf(block.content)
}

/** What a quote cue needs from the message it quotes: a line, not the whole block. */
export type QuoteSource = Pick<Block, 'kind' | 'author_type' | 'author' | 'content'> & {
  meta?: Block['meta']
  mime_type?: Block['mime_type']
}

/**
 * 引用一句话时显示的摘要。附件没有正文可引，就说它是什么。
 *
 * 芝士的话是 markdown，引用条里是一行纯文本：不先读成字，引到的就是
 * `**A / B / C**`、一对反引号和 `:::chart`。按文档的读法读成字（lib/docRead.ts）；读法第一次用到才加载，
 * 加载完之前先引原文，加载完自己换掉。人说的话原样显示，所以也原样引用。@ 人、提话题、
 * 指文件的 token 两边都一样读成名字，和正文里 chip 上写的字一致。
 *
 * 步骤清单另说一句：正文里一步一行，折成一行之后只剩一串记号，认不出说的是什么，
 * 所以摘要不从正文取，取的是清单自己的结构（`checklistSnippet`）。
 */
export function replySnippet(block: QuoteSource, maps: RefNames, max = 24): string {
  if (block.kind === 'attachment')
    return isImageBlock(block) ? t('work.room.attachments.imageSnippet') : t('work.room.attachments.fileSnippet')
  const step = checklistSnippet(block)
  if (step !== null) return clip(step, max)
  const read = isAgentBlock(block) ? docReadNow() : null
  const source = read ? read.plainText(block.content, 'chat') : block.content
  // A message's @ told its person, so its quote keeps the "@" its chip shows.
  const names = block.kind === 'message' ? toldByMessage(maps) : maps
  return clip(plainRefs(source, names).replace(/\s+/g, ' ').trim(), max)
}

/** 一条支线的标题：它挂着的那条消息，去掉开头点的名（「@芝士 」），只留说的事。 */
export function threadHeading(block: Block, maps: RefNames): string {
  const said = block.content.replace(/^(\s*<@[^>\s]+>)+\s*/, '')
  return replySnippet({ ...block, content: said || block.content }, maps, 120)
}

function clip(text: string, max: number): string {
  return text.length > max ? text.slice(0, max) + '…' : text
}

/** 清单一步的记号，和清单消息正文里写的一样（后端的 `_CHECKLIST_MARK`）。 */
const STEP_MARK: Record<TodoItem['status'], string> = {
  completed: '✓',
  in_progress: '✱',
  pending: '○',
}
const MARK_STATUS = Object.fromEntries(Object.entries(STEP_MARK).map(([status, mark]) => [mark, status])) as Record<
  string,
  TodoItem['status']
>
/** 正文里结果那一行的记号（后端 `checklist_text` 写的 `✅ 结果`）。 */
const RESULT_MARK = '✅'

/**
 * 清单消息在引用条、消息行、支线摘要里说的那一句：只念一步，连它的记号。念哪一步：
 * 正在做的那一步（`✱`）；都做完了、写了结果，念结果；否则念还没做的第一步；连这也没有
 * （全做完、没写结果），念最后做完的那一步。做完的第一步往往早就过去了，念它等于什么
 * 都没说。不是清单就返回 null，走平常那句摘要。
 *
 * 结构来自块自己的 `meta.checklist`；支线摘要里那条回复只拿到文本，它那份结构是
 * 后端随着回复一起带上的（`replyBlock`）。迁移前的清单只有一个 `true`，结构得从正文
 * 一行一步里读回来（`legacySnippet`）。
 */
function checklistSnippet(block: Pick<Block, 'meta' | 'content'>): string | null {
  const checklist = block.meta?.checklist
  if (checklist === true) return legacySnippet(block.content)
  if (!checklist || typeof checklist !== 'object') return null
  return stepSnippet(checklist)
}

function stepSnippet({ items, result }: ChecklistMeta): string | null {
  const doing = items.find((item) => item.status === 'in_progress')
  if (doing) return markedStep(doing)
  if (result && items.every((item) => item.status === 'completed')) return `${RESULT_MARK} ${result}`
  const next = items.find((item) => item.status === 'pending') ?? items.at(-1)
  return next ? markedStep(next) : null
}

function markedStep(step: TodoItem): string {
  return `${STEP_MARK[step.status] ?? ''} ${step.subject}`.trim()
}

/** 迁移前的清单：正文一行一步（`✱ 取一份主干代码`），做完时再有一行 `✅ 结果`。一步都
 *  读不出的话念第一行——不念折成一行的整段。 */
function legacySnippet(content: string): string {
  const items: TodoItem[] = []
  let result: string | null = null
  for (const line of content.split('\n')) {
    const text = line.trim()
    const status = MARK_STATUS[text.charAt(0)]
    if (status) items.push({ id: String(items.length + 1), subject: text.slice(1).trim(), status })
    else if (text.startsWith(RESULT_MARK)) result = text.slice(RESULT_MARK.length).trim()
  }
  return stepSnippet({ items, result }) ?? (content.split('\n').find((line) => line.trim()) ?? '').trim()
}

/**
 * 支线摘要里那条最后回复（`ThreadReply`：只有谁、什么话、什么时候）拼成的块。
 * 清单的结构由后端随它带上，这里装回块的 `meta` 里，好让 `replySnippet` 和房间里的
 * 引用条读同一份东西。
 */
export function replyBlock(reply: ThreadReply): Block {
  return {
    id: '',
    conversation_id: '',
    kind: 'message',
    author_type: 'participant',
    author: reply.author,
    content: reply.content,
    created_at: reply.created_at,
    meta: reply.checklist ? { checklist: reply.checklist } : null,
  } as Block
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

/** 答过这道题的一句话：谁说的、说的是什么。 */
/** `skipped`：被问的人跳过了这道题、没有回答（`answer_log` 里 `kind: 'skipped'` 那一条）。 */
export type AskAnswer = { by: string; text: string; skipped: boolean }

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

/** 提问方收回了这道题（`withdrawn`），或者它过了时效（`expired`）：题上 `answer_log`
 *  里的那一条。两者都不是回答（`answered` 仍为假）；撤回了就不再等人答，过期了还能答。 */
export type AskClosed = 'withdrawn' | 'expired'

/** 题是不是被提问方收起来了；没有就是 null。撤回和过期只会有一条（先到的那条作数）。 */
export function askClosedByAsker(block: Block): AskClosed | null {
  const log = (block.meta as Record<string, unknown> | null)?.answer_log
  if (!Array.isArray(log)) return null
  for (const entry of log as Record<string, unknown>[]) {
    if (entry.kind === 'withdrawn' || entry.kind === 'expired') return entry.kind as AskClosed
  }
  return null
}

/**
 * 答过这道题的每一句，按先后：点了选项的是那个选项，打字回的是他那句话；跳过的那一条
 * 没有话，`skipped` 为真。提问方收起题的那一条不算回答，这里就没有它。
 * 房间里所有人看到的是同一份。
 */
export function askAnswers(block: Block): AskAnswer[] {
  const log = (block.meta as Record<string, unknown> | null)?.answer_log
  if (!Array.isArray(log)) return []
  return (log as Record<string, unknown>[])
    .filter((entry) => entry.kind !== 'withdrawn' && entry.kind !== 'expired')
    .map((entry: Record<string, unknown>) => ({
      by: String(entry.by ?? ''),
      // `reject` 只在早先作答的题上留着；它说的就是「以上都不是」。
      text:
        entry.kind === 'reject'
          ? t('ask.replies.noneOfThese')
          : String((entry.kind === 'note' ? entry.note : entry.option) ?? ''),
      skipped: entry.kind === 'skipped',
    }))
}
