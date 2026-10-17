/**
 * 房间那几格：剧本里的留言、芝士的复述、收尾那句、每一轮折出来的通知，以及
 * `cheese_ask` 的一道题、一条长正文，外加两个把行翻成组件 props 的小函数。
 *
 * 从 `../catalogFixtures.ts` 拆出来的一册：行本身由 `./common` 从剧本算出来，
 * 这一册只负责挑哪几行、造剧本没演到的那两格。条目在 `../catalog/conversation.ts`。
 */
import type { MentionPoolEntry } from '@/composables/useRoomMentionPicker'
import type { Block } from '@/cx_types'

import { AGENT_NAME, latest, messagesOf, NAMES, noticesOf, ROOM_REFS, type RoomRow, rows, SCENE } from './common'

/** 王长鑫的两句话：一句只留在话题里的留言，一句点了名交给芝士。 */
export const WANG_LINES = messagesOf(0, 'wang')
/** 芝士复述理解的那一句，和它随后写下的步骤清单。 */
export const CHEESE_LINES = messagesOf(1, 'cheese')
/** 房间收尾那一句。 */
export const WANG_CLOSING = latest(messagesOf(5, 'wang'))
/** 整段对话（到收尾那一句为止）：预览站里「正在改最后一条」那一格摆的就是这些行。 */
export const CHAT_ROWS_FULL: RoomRow[] = rows(5)
/** 正在改的那一条：收尾那句，末尾是刚敲下的一个 `@` —— `@` 一落下候选就开着，所以
 *  这一格不用谁先打字，打开就看得见菜单。 */
export const WANG_EDITING_ROW: RoomRow = {
  ...WANG_CLOSING,
  line: { ...WANG_CLOSING.line, text: `${WANG_CLOSING.line.text} @` },
  block: { ...WANG_CLOSING.block, content: `${WANG_CLOSING.block.content} @` },
}
/** 「这一条是不是我说的」：剧本里说话的那位是 `wang`，而改消息只改自己的。 */
export const isMineWang = (b: Block): boolean => b.author === 'wang'
/** 改消息那个框里的 @ 候选：房间里的人，AI 队友排最前（和产品里一个口径）。 */
export const ROOM_MENTION_POOL: MentionPoolEntry[] = [
  { handle: 'cheese', label: NAMES.cheese ?? '芝士', avatar: null, agent: true },
  { handle: 'wang', label: NAMES.wang ?? '王长鑫', avatar: null, agent: false },
]
/** 这一轮结束时折出来的那几条：本轮摘要、待审阅、已采纳。 */
export const TURN_SUMMARY = latest(noticesOf(3))
export const CARD_FILED = latest(noticesOf(4))
export const ACCEPT_DONE = latest(noticesOf(5))

/**
 * `cheese_ask` 的一条真问题。
 *
 * 画它的是 `RoomMessage.vue` 本身：`meta.options` 躺在那儿，消息下面是一排快捷回复；
 * 有人答过之后 `meta.answer_log` 记下答过的每一句，房间画成「谁说了什么」。
 *
 * 字段形状照后端写（`block/questions.py`）：`meta.asked` 是**问了谁**的 handle
 * 列表（空的是没点名），不是布尔。
 */
const ASK_TEXT = '这次的作业按哪种方式收？'
const ASK_OPTIONS = [{ text: '课程平台收文件' }, { text: '发到课程邮箱' }, { text: '课上交纸质' }]
/** 这道题在等谁答：发起那一轮的人，谁答谁就是他。 */
const ASK_WAITING_FOR = 'wang'

function askRow(answered: { option: string; by: string } | null): RoomRow {
  const meta: Block['meta'] = { asked: [ASK_WAITING_FOR], options: ASK_OPTIONS }
  if (answered) {
    meta.answer_log = [
      {
        kind: 'option',
        option: answered.option,
        note: null,
        by: answered.by,
        at: '2026-09-30T10:24:00Z',
      },
    ]
  }
  const block: Block = {
    id: answered ? 'ask-demo-answered' : 'ask-demo-open',
    conversation_id: 'demo',
    kind: 'message',
    author_type: 'participant',
    author: 'cheese',
    content: ASK_TEXT,
    created_at: '2026-09-30T10:24:00Z',
    meta,
  }
  return {
    line: { kind: 'message', id: block.id, author: block.author, text: ASK_TEXT, time: '10:24', block },
    block,
    notice: null,
    run: [block],
  }
}

/** 还没答：一排快捷回复，点一个就是回了那一句。 */
export const ASK_OPEN = askRow(null)
/** 已经有人答了：整排收成「某某：说了什么」。 */
export const ASK_ANSWERED = askRow({ option: ASK_OPTIONS[0].text, by: 'wang' })

/** `RoomMessage` 要吃的那一大把 props，和 `DemoRoom` 传给它的逐字一样。 */
export function roomMessageProps(row: RoomRow, over: Record<string, unknown> = {}): Record<string, unknown> {
  const who = row.block.author
  return {
    block: row.block,
    parent: null,
    parentName: null,
    runStart: true,
    mine: false,
    topicId: null,
    authorName: NAMES[who] ?? who,
    avatar: null,
    isAgent: SCENE.people[who]?.agent === true,
    time: row.line.time,
    refs: ROOM_REFS,
    viewer: '',
    askBusy: false,
    ...over,
  }
}

/** `RoomNotice` 要吃的那一大把 props（同上）。这一行必须真的是一条通知。 */
export function roomNoticeProps(row: RoomRow, over: Record<string, unknown> = {}): Record<string, unknown> {
  const notice = row.notice
  if (!notice) throw new Error('这一行没有通知，roomNoticeProps 用错了')
  return {
    block: row.block,
    notice,
    run: row.run,
    agent:
      row.block.author === 'system' || !NAMES[row.block.author]
        ? null
        : { name: NAMES[row.block.author]!, handle: row.block.author },
    time: row.line.time,
    agentName: AGENT_NAME,
    refs: ROOM_REFS,
    ...over,
  }
}

/** 一整段交代写进一条消息里：产品里真有人这么发（也是长正文那一格要看的）。 */
const LONG_TEXT = `我把这周的进度理了一遍，发在这里，谁有空看一眼：

1. 课件前三讲的排版都过了一遍，习题答案还差第三讲；
2. 作业提交的口子换了新的表单，旧的那个还留着，等这周过了再关；
3. 上周说的那个「交上来文件名是乱的」问题，是因为两次上传同名文件，已经改成加时间戳了。

下周打算先把第四讲的课件排出来，再把作业那套流程整个跑一遍，有问题在下面回我。`

const LONG_BLOCK: Block = {
  id: 'catalog-long',
  conversation_id: 'demo',
  kind: 'message',
  author_type: 'participant',
  author: 'wang',
  content: LONG_TEXT,
  created_at: new Date('2026-09-28T14:20:00+08:00').toISOString(),
}

/** 一条长消息的行（不是剧本里的，其余几条都是）。 */
export const LONG_ROW: RoomRow = {
  line: { kind: 'message', id: LONG_BLOCK.id, author: 'wang', text: LONG_TEXT, time: '14:20' },
  block: LONG_BLOCK,
  notice: null,
  run: [LONG_BLOCK],
}
