// 「待发批注」：在预览里指了好几处，攒着一条消息一起交给芝士。
//
// 逐条打断会生成好几个版本、每次都重排一遍；攒成一条，芝士一轮改完。所以在预览里指一处
// 可以「加入待发」，攒够了再一起发。这一份说的是「攒在哪、谁看得见」：
//
// 谁提供：话题页（它同时拿着右栏和对话栏）。谁读：右栏预览（往里加）、撰写框（列出来、
// 发送）。没人提供时（资料库、产物页那些没有对话栏的地方）预览退回自己面板底下那条。
//
// 内容按「看得见就够」存：位置、原文、批注文字、去处，加上已经拼好的那条消息文本
// （发出去的那句话就是它，预览那边怎么拼的、这里原样存着，不重拼）。
import type { InjectionKey, Ref } from 'vue'

import { ref } from 'vue'

/** 攒着的这一条要去哪：交给芝士改，还是只挂成评论。
 *
 *  今天 `queueLocator` 只往里放 `'cheese'`——「加入待发」那颗键只在去处是芝士时出现，
 *  只留评论的当场就挂上去了（见 `usePreviewLocator`）。这一档留着是因为去处菜单马上要
 *  多一项「问个人芝士（私聊）」，那时攒着的就不止交给房间芝士这一种了。 */
export type PendingAudience = 'cheese' | 'comment'

/** 这一条指着文件里的哪儿——正文里那颗序号 pin 靠它找回来。
 *
 *  和评论的锚点同一套判据（`markdownQuote.ts`）：存「原文 + 前后文」而不是 DOM 位置，
 *  正文重排之后还能按字找；页上那一处存页码和比例，和 `page-pin` 同形。找不到就
 *  不画那颗 pin，tray 里那一条照样在。 */
export type PendingAnchor =
  | { kind: 'text-range'; text: string; heading: string | null; prefix: string; suffix: string }
  | { kind: 'page-text'; page: number; text: string; prefix?: string; suffix?: string }
  | { kind: 'page-pin'; page: number; x: number; y: number }
  | { kind: 'sheet-cell'; sheet: string; address: string }

export interface PendingNote {
  id: string
  /** 哪一份文件，如 `招募方案.docx`。 */
  file: string
  /** 第几版，如 `第 3 版`；拿不到就是空。 */
  version: string
  /** 文件里哪一处，如 `第 1 页 · 二、时间安排`。 */
  where: string
  /** 指出来的原文（已经截断过）。 */
  quote: string
  /** 读者写的那句话。 */
  text: string
  audience: PendingAudience
  /** 已经拼好的那条消息（位置 + 原文 + 那句话），发出去就用它。 */
  message: string
  /** 文件里的位置：正文里按它画序号 pin。拿不到就是 undefined，那一处不画 pin。 */
  anchor?: PendingAnchor
}

export interface PendingNotesApi {
  notes: Ref<readonly PendingNote[]>
  add: (note: Omit<PendingNote, 'id'>) => void
  remove: (id: string) => void
  clear: () => void
  /** 把攒着的这一批加上可选的总体说明，合成一条消息发出去。 */
  send: (summary: string) => boolean
}

export const PENDING_NOTES: InjectionKey<PendingNotesApi> = Symbol('pending-notes')

let seq = 0

/** 一条待发批注的 id。只为删的时候认得出来，不跨会话，编号从 1 起在框里标出来。 */
export function pendingNoteId(): string {
  seq += 1
  return `pn-${seq}`
}

/** 这一处显示成原型里那种一行 `文件 · 版本 · 位置`。缺哪段就少哪段，不留空分隔符。 */
export function pendingWhere(note: Pick<PendingNote, 'file' | 'version' | 'where'>): string {
  return [note.file, note.version, note.where].filter((part) => part && part.trim()).join(' · ')
}

/** 攒着的这一批加上可选的总体说明，拼成一条要发出去的消息。
 *
 *  没有总体说明时就是各条消息按空行连起来（和原来 `sendQueued` 的拼法一致）；有说明就
 *  写最前面，后面空一行再跟各条——受话人先读到「这一批是要干什么」。
 *
 *  只有「交给芝士」的那些才并进这一条消息；只留评论的那些不走这条（评论不经对话发）。 */
export function composePending(notes: readonly PendingNote[], summary: string): string {
  const body = notes
    .filter((note) => note.audience === 'cheese')
    .map((note) => note.message)
    .join('\n\n')
  const head = summary.trim()
  if (!head) return body
  return body ? `${head}\n\n${body}` : head
}

/** 话题页那一份待发篮：整个话题共一份，右栏预览往里加，撰写框读它、发送。
 *
 *  发送要落到对话那一栏上，而那一栏是把手上来的（`ChatColumnHandle`），所以把发送这一步
 *  交给调用方，这里只管攒和拼。`send` 返回对话栏收没收下，收下才清空——发不出去时
 *  这一批要留着，不能悄悄丢掉。 */
export function usePendingNotes(sendMessage: (content: string) => boolean): PendingNotesApi {
  const notes = ref<PendingNote[]>([])
  function add(note: Omit<PendingNote, 'id'>) {
    notes.value = [...notes.value, { ...note, id: pendingNoteId() }]
  }
  function remove(id: string) {
    notes.value = notes.value.filter((note) => note.id !== id)
  }
  function clear() {
    notes.value = []
  }
  function send(summary: string) {
    const message = composePending(notes.value, summary)
    if (!message.trim()) return false
    if (!sendMessage(message)) return false
    // 发出去的只是 `composePending` 收进去的那几条（交给芝士的）。今天篮里只有这一
    // 种，整批清掉等于清掉发出去的那几条；真攒进只挂评论的（去处菜单加「问个人芝士」
    // 之后）时，那几条**不能**跟着清——它们没经对话发出去，清掉就是无声地丢。所以
    // 按发出去的那几条逐个删，不整批清。
    const sent = new Set(notes.value.filter((note) => note.audience === 'cheese').map((note) => note.id))
    notes.value = notes.value.filter((note) => !sent.has(note.id))
    return true
  }
  return { notes, add, remove, clear, send }
}
